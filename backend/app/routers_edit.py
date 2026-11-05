import base64
import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import calllog, compose, copyfix, copywriter, editing, phrases, storage
from .models import (Asset, Brand, BrandVersion, CopyVariant, Design, DesignSnapshot, Element,
                     GeneratedImage, PresetVersion, ReviewResult)
from .routers_assets import FONT_DIR, PROVIDED_FONTS, get_db, get_design, latest_image
from .routers_copy import select_variant
from .routers_review import _brand_dict, ensure_unlocked, gather, invalidate_reviews, run_review
from .routers_images import (_color, _edit_saved, _img_out, compose_preset, create_shared_background, make_image,
                             prewarm_thumbs)

router = APIRouter(prefix="/api", dependencies=[Depends(calllog.track_design)])   # 호출 기록에 시안 id
# 편집 미리보기 크기(긴 변 px). 화면이 보여 줄 크기 × 픽셀 배율(max_px)을 보내면 그 크기로, 안 보내면 PREVIEW_MAX.
# 레티나 화면에서 900px 고정이면 브라우저가 키워 보여 줘 흐렸다(편집 화면 폭 436px × 2 = 872px, 세로형은 긴 변 1092px)
PREVIEW_MAX = 900
PREVIEW_RANGE = (400, 1600)
PREVIEW_JPEG_QUALITY = 90


# ---------- 공통 보조 ----------
def _preset(db: Session, d: Design, pid: int) -> PresetVersion:
    pv = db.get(PresetVersion, pid)
    if not pv or pid not in d.preset_version_ids:
        raise HTTPException(404, "이 시안에 포함된 규격이 아닙니다")
    if pv.placement.content_kind == "분리 자산형":
        raise HTTPException(400, "분리 자산형은 이미지에 문구·로고를 합성하지 않아 편집 대상이 아닙니다")
    return pv


def _font_ref(bv: BrandVersion, role: str = "제목") -> dict:
    fonts = bv.fonts or []
    f = next((x for x in fonts if x.get("role") == role), None) or (fonts[0] if fonts else None)
    if f and f.get("source") == "upload" and f.get("asset_id"):
        return {"source": "upload", "asset_id": f["asset_id"]}
    if f and f.get("source") == "provided" and f.get("name"):
        return {"source": "provided", "name": f["name"]}
    return {"source": "provided", "name": "Pretendard-Bold"}


def _font_path(db: Session, ref: dict) -> str:
    if ref.get("source") == "upload" and ref.get("asset_id"):
        a = db.get(Asset, ref["asset_id"])
        if a and storage.exists(a.file_path):
            return str(storage.local(a.file_path))      # ImageFont 는 로컬 파일이 필요하다
    if ref.get("source") == "provided":
        p = FONT_DIR / f"{ref.get('name')}.otf"
        if p.exists():
            return str(p)
    return str(FONT_DIR / "Pretendard-Bold.otf")


def _logo_of(db: Session):
    def f(asset_id):
        if not asset_id:
            return None
        a = db.get(Asset, asset_id)
        return compose.load_logo(a.file_path, a.mime) if a and storage.exists(a.file_path) else None
    return f


def _els(db: Session, did: int, pid: int) -> list[Element]:
    return db.scalars(select(Element).where(Element.design_id == did, Element.preset_version_id == pid)
                      .order_by(Element.z_index, Element.id)).all()


def _el_out(e: Element) -> dict:
    return {"id": e.id, "type": e.type, "role": (e.props or {}).get("_role", ""), "locked": e.locked,
            "z_index": e.z_index, "scope": e.scope, "props": {k: v for k, v in e.props.items() if k != "_role"}}


def _bootstrap(db: Session, d: Design, pid: int, bv: BrandVersion) -> list[Element]:
    comp = latest_image(db, d.id, pid, "composite", original_only=True)
    if not comp or (comp.layout or {}).get("separate_assets"):
        raise HTTPException(400, "먼저 이미지를 생성해 주세요 (카피 선택 후 이미지 생성)")
    for e in editing.default_elements(comp.layout, _font_ref(bv), bv.logo_asset_id, _font_ref(bv, "본문")):
        props = dict(e["props"])
        props["_role"] = e["role"]
        scope = "공통" if e["type"] == "text" else "지면별"
        db.add(Element(design_id=d.id, preset_version_id=pid, type=e["type"], scope=scope,
                       props=props, z_index=e["z_index"], locked=e["locked"]))
    db.flush()
    return _els(db, d.id, pid)


def _as_render(rows: list[Element]) -> list[dict]:
    return [{"type": r.type, "props": {k: v for k, v in r.props.items() if k != "_role"},
             "z_index": r.z_index, "locked": r.locked} for r in rows]


def _render_rows(db, d, pv, rows, bg_path):
    W, H = pv.production_width, pv.production_height
    bv = db.get(BrandVersion, d.brand_version_id)
    bg = Image.open(storage.local(bg_path))
    img, measure = editing.render(bg, W, H, _as_render(rows), lambda ref: _font_path(db, ref), _logo_of(db))
    text_el = next((r for r in rows if r.type == "text"), None)
    brand_hex = {c["hex"].upper() for c in (bv.colors or []) if c.get("hex")}
    warns = editing.warnings_for(measure, W, H, pv.safe_area, brand_hex, phrases.effective(bv, d.brief)["forbidden_phrases"],
                                 text_el.props["text"] if text_el else None, img,
                                 text_el.props["color"] if text_el else None)
    return img, measure, warns


def _save_render(db, d, pv, rows, bg_row, comp_prev, copy_variant_id: int | None = None, snapshot: bool = True) -> dict:
    img, measure, warns = _render_rows(db, d, pv, rows, bg_row.file_path)
    path = storage.put_image(f"edt_{uuid.uuid4().hex[:10]}.png", img)
    prewarm_thumbs(path, img)
    layout = {"template": compose.template_for(pv.production_width, pv.production_height),
              "canvas": [pv.production_width, pv.production_height], "edited": True, "warnings": warns,
              "elements": [], "margin_source": "편집됨"}
    for r in rows:
        if r.type == "shape":
            layout["scrim"] = {"mode": r.props["mode"], "color": r.props["color"], "opacity": r.props["opacity"],
                               **{k: r.props[k] for k in ("side", "extent") if k in r.props}}
        elif r.type == "logo" and measure.get("logo", {}).get("box"):
            layout["elements"].append({"type": "logo", "box": measure["logo"]["box"], "locked": r.locked})
        elif r.type == "text":
            layout["elements"].append({"type": "text", "text": r.props["text"], "box": measure["text"]["box"],
                                       "font_size": r.props["font_size"], "color": r.props["color"],
                                       "align": r.props["align"], "locked": r.locked})
        elif r.type == "body" and measure.get("body"):       # 본문·CTA 도 저장 레이아웃에 남긴다(편집 전 합성과 같은 모양)
            layout["elements"].append({"type": "body", "role": "본문", "text": r.props["text"], "lines": measure["body"]["lines"],
                                       "box": measure["body"]["box"], "font_size": r.props["font_size"],
                                       "color": r.props["color"], "cut": measure["body"]["cut"], "align": r.props["align"],
                                       "locked": r.locked})
        elif r.type == "cta" and measure.get("cta"):
            layout["elements"].append({"type": "cta", "role": "CTA", "text": r.props["text"], "box": measure["cta"]["box"],
                                       "font_size": r.props["font_size"], "bg_color": r.props["bg_color"],
                                       "text_color": r.props["text_color"], "corner": r.props.get("corner"), "locked": r.locked})
    layout["in_image"] = ["headline"] + [k for k, t in (("body", "body"), ("cta", "cta")) if any(r.type == t for r in rows)]
    g = GeneratedImage(design_id=d.id, preset_version_id=pv.id, kind="composite", file_path=path,
                       width=pv.production_width, height=pv.production_height,
                       copy_variant_id=copy_variant_id or (comp_prev.copy_variant_id if comp_prev else None),
                       brand_version_id=d.brand_version_id, layout=layout)
    db.add(g)
    if snapshot:
        db.add(DesignSnapshot(design_id=d.id, elements=[{"preset_version_id": pv.id, **_el_out(r)} for r in rows]))
    d.status = "수정됨"
    for rr in db.scalars(select(ReviewResult).where(ReviewResult.design_id == d.id,
                                                     ReviewResult.preset_version_id == pv.id)).all():
        rr.valid = False       # 수정 후에는 기존 검수 결과를 무효화한다
    db.flush()
    return {"composite": _img_out(g), "warnings": warns}


def _bg_row(db, did, pid) -> GeneratedImage:
    bg = latest_image(db, did, pid, "background")
    if not bg or not storage.exists(bg.file_path):
        raise HTTPException(400, "배경 이미지가 없습니다. 먼저 이미지를 생성해 주세요")
    return bg


def rerender_edited(db, d: Design, exclude: int | None = None, note_unedited: bool = False) -> tuple[list[int], list[dict]]:
    """편집 요소가 있는 지면들을 요소를 유지한 채 현재 배경으로 다시 그린다(배경 재생성 뒤 호출). 잠긴 배경은 건너뛴다.
    반환: (다시 그린 preset_id 들, 건너뛴 {preset_id, name, reason} 들). note_unedited 면 편집 전 지면도 사유와 함께 돌려준다."""
    applied, skipped = [], []
    for pid in d.preset_version_ids:
        if pid == exclude:
            continue
        opv = db.get(PresetVersion, pid)
        orows = _els(db, d.id, pid)
        name = opv.placement.name if opv else str(pid)
        if not orows:
            if note_unedited:
                skipped.append({"preset_id": pid, "name": name, "reason": "편집 전 상태입니다. '문구 다시 합성'으로 새 배경을 반영하세요"})
            continue
        oimg = next((r for r in orows if r.type == "image"), None)
        if oimg is None or oimg.locked:
            skipped.append({"preset_id": pid, "name": name, "reason": "배경이 잠겨 있어 이전 배경을 유지합니다"})
            continue
        oimg.props = {**oimg.props, "zoom": 1.0, "cx": 0.5, "cy": 0.5}      # 새 이미지는 자르기 초기화
        _save_render(db, d, opv, orows, _bg_row(db, d.id, pid), latest_image(db, d.id, pid, "composite"))
        applied.append(pid)
    db.flush()
    return applied, skipped


# ---------- 조회 ----------
@router.get("/designs/{design_id}/presets/{preset_id}/edit")
def get_edit(design_id: int, preset_id: int, db: Session = Depends(get_db)):
    d = get_design(db, design_id, ad_only=True)
    pv = _preset(db, d, preset_id)
    bv = db.get(BrandVersion, d.brand_version_id)
    rows = _els(db, d.id, preset_id) or _bootstrap(db, d, preset_id, bv)
    db.commit()
    fonts = [{"label": f["label"], "ref": {"source": "provided", "name": f["name"]}} for f in PROVIDED_FONTS]
    for f in bv.fonts or []:
        if f.get("source") == "upload" and f.get("asset_id"):
            fonts.append({"label": f"업로드 폰트 ({f.get('role')})", "ref": {"source": "upload", "asset_id": f["asset_id"]}})
    comp = latest_image(db, d.id, preset_id, "composite")
    return {"canvas": [pv.production_width, pv.production_height], "name": pv.placement.name,
            "elements": [_el_out(r) for r in rows], "fonts": fonts,
            "brand_colors": [{"role": c.get("role"), "hex": c.get("hex")} for c in (bv.colors or [])],
            "composite": _img_out(comp), "edited": bool((comp.layout or {}).get("edited")) if comp else False}


# ---------- 미리보기(저장하지 않음) ----------
def element_boxes(rows: list[tuple[int, str]], measure: dict) -> dict[str, list[int]]:
    """미리보기에서 끌어 옮길 수 있게, 요소 id → 캔버스 픽셀 경계 상자 [x0, y0, x1, y1]. 렌더가 잰 값(measure)을 그대로 쓴다.
    제목·로고·본문·CTA 는 지면마다 최대 하나씩이라 종류로 짝을 맞춘다. 그려지지 않은 요소(로고 자산 없음 등)는 뺀다."""
    out: dict[str, list[int]] = {}
    for rid, t in rows:
        box = (measure.get(t) or {}).get("box")
        if t in ("text", "logo", "body", "cta") and box:
            out[str(rid)] = [int(v) for v in box]
    return out



def _apply_payload(db, rows: list[Element], payload: list[dict], canvas) -> list[dict]:
    """payload 를 검증해 렌더용 요소 목록으로 만든다. 잠긴 요소의 값 변경은 거절한다."""
    by_id = {r.id: r for r in rows}
    out = []
    for r in rows:
        item = next((x for x in payload if x.get("id") == r.id), None)
        props = {k: v for k, v in r.props.items() if k != "_role"}
        if item is not None and "props" in item:
            try:
                new = editing.validate_props(r.type, {**props, **item["props"]}, tuple(canvas))
            except ValueError as e:
                raise HTTPException(400, f"{(r.props or {}).get('_role') or r.type}: {e}")
            if r.locked and new != editing.validate_props(r.type, props, tuple(canvas)):
                raise HTTPException(400, f"잠긴 요소는 수정할 수 없습니다: {(r.props or {}).get('_role') or r.type}")
            props = new
        out.append({"row": r, "props": props, "locked": item.get("locked", r.locked) if item else r.locked})
    unknown = [x.get("id") for x in payload if x.get("id") not in by_id]
    if unknown:
        raise HTTPException(400, f"알 수 없는 요소: {unknown}")
    return out


@router.post("/designs/{design_id}/presets/{preset_id}/edit/preview")
def preview(design_id: int, preset_id: int, elements: list[dict] = Body(..., embed=True),
            max_px: int | None = Body(None, embed=True), db: Session = Depends(get_db)):
    d = get_design(db, design_id, ad_only=True)
    pv = _preset(db, d, preset_id)
    rows = _els(db, d.id, preset_id)
    if not rows:
        raise HTTPException(400, "편집 화면을 먼저 열어 주세요")
    applied = _apply_payload(db, rows, elements, (pv.production_width, pv.production_height))
    render_rows = [{"type": a["row"].type, "props": a["props"], "z_index": a["row"].z_index} for a in applied]
    bg = Image.open(storage.local(_bg_row(db, d.id, preset_id).file_path))
    W, H = pv.production_width, pv.production_height
    img, measure = editing.render(bg, W, H, render_rows, lambda ref: _font_path(db, ref), _logo_of(db))
    bv = db.get(BrandVersion, d.brand_version_id)
    txt = next((a for a in applied if a["row"].type == "text"), None)
    warns = editing.warnings_for(measure, W, H, pv.safe_area,
                                 {c["hex"].upper() for c in (bv.colors or []) if c.get("hex")},
                                 phrases.effective(bv, d.brief)["forbidden_phrases"], txt["props"]["text"] if txt else None, img,
                                 txt["props"]["color"] if txt else None)
    side = min(max(max_px, PREVIEW_RANGE[0]), PREVIEW_RANGE[1]) if max_px else PREVIEW_MAX
    small = img.copy()
    small.thumbnail((side, side))           # 원본보다 크게 키우지는 않는다
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=PREVIEW_JPEG_QUALITY)
    L, T, R, B, _ = compose._margins(W, H, pv.safe_area)
    return {"image": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(),
            "warnings": warns, "size": [W, H], "boxes": element_boxes([(a["row"].id, a["row"].type) for a in applied], measure),
            "margins": [L, T, R, B]}


# ---------- 저장 ----------
def _persist(db, rows, applied):
    for a in applied:
        r = a["row"]
        old_text = r.props.get("text")
        props = dict(a["props"])
        if r.type == "text" and props["text"] != old_text:
            props["text_override"] = True        # 이 지면만 따로 수정한 문구 (공통 변경에서 보호)
        props["_role"] = r.props.get("_role", "")
        r.props = props
        r.locked = bool(a["locked"])


@router.put("/designs/{design_id}/presets/{preset_id}/edit")
def save_edit(design_id: int, preset_id: int, elements: list[dict] = Body(..., embed=True), db: Session = Depends(get_db)):
    d = get_design(db, design_id, ad_only=True)
    ensure_unlocked(d)
    pv = _preset(db, d, preset_id)
    rows = _els(db, d.id, preset_id)
    if not rows:
        raise HTTPException(400, "편집 화면을 먼저 열어 주세요")
    applied = _apply_payload(db, rows, elements, (pv.production_width, pv.production_height))
    _persist(db, rows, applied)
    res = _save_render(db, d, pv, rows, _bg_row(db, d.id, preset_id), latest_image(db, d.id, preset_id, "composite"))
    db.commit()
    return {**res, "elements": [_el_out(r) for r in rows]}


@router.post("/designs/{design_id}/edit/common-text")
def common_text(design_id: int, text: str = Body(..., embed=True), db: Session = Depends(get_db)):
    """문구를 모든 지면에 적용한다. 잠겼거나 지면별로 따로 수정한 문구는 건드리지 않는다."""
    d = get_design(db, design_id, ad_only=True)
    ensure_unlocked(d)
    text = text.strip()
    if not text or len(text) > 300:
        raise HTTPException(400, "문구는 1~300자여야 합니다")
    bv = db.get(BrandVersion, d.brand_version_id)
    applied_ids, skipped = [], []
    for pid in d.preset_version_ids:
        pv = db.get(PresetVersion, pid)
        if not pv or pv.placement.content_kind == "분리 자산형":
            continue
        name = pv.placement.name
        try:
            rows = _els(db, d.id, pid) or _bootstrap(db, d, pid, bv)
        except HTTPException:
            skipped.append({"preset_id": pid, "name": name, "reason": "이미지가 아직 없습니다"})
            continue
        t = next((r for r in rows if r.type == "text"), None)
        if t is None:
            skipped.append({"preset_id": pid, "name": name, "reason": "문구 요소가 없습니다"})
        elif t.locked:
            skipped.append({"preset_id": pid, "name": name, "reason": "문구가 잠겨 있습니다"})
        elif t.props.get("text_override"):
            skipped.append({"preset_id": pid, "name": name, "reason": "이 지면에서 따로 수정한 문구입니다"})
        else:
            t.props = {**t.props, "text": text}
            _save_render(db, d, pv, rows, _bg_row(db, d.id, pid), latest_image(db, d.id, pid, "composite"))
            applied_ids.append(pid)
    db.commit()
    return {"applied": applied_ids, "skipped": skipped}


# ---------- 요소 재생성 / 처음 상태로 ----------
@router.post("/designs/{design_id}/presets/{preset_id}/edit/regenerate")
def regenerate(design_id: int, preset_id: int, element: str = Body(..., embed=True), db: Session = Depends(get_db)):
    d = get_design(db, design_id, ad_only=True)
    ensure_unlocked(d)
    pv = _preset(db, d, preset_id)
    rows = _els(db, d.id, preset_id)
    if not rows:
        raise HTTPException(400, "편집 화면을 먼저 열어 주세요")
    bv = db.get(BrandVersion, d.brand_version_id)
    brand = db.get(Brand, d.brand_id)
    extra: dict = {}
    if element == "background":
        img_el = next(r for r in rows if r.type == "image")
        if img_el.locked:
            raise HTTPException(400, "배경이 잠겨 있어 재생성할 수 없습니다")
        # 배경은 모든 매체가 공유한다: 편집 중인(요소가 있는) 매체의 비율군만 지금 만들어 연결하고, 그 매체들을 새 배경으로
        # 다시 그린다. 나머지 비율군은 그 매체를 '문구 다시 합성'할 때 현재 장면을 참조해 만든다(쓰지 않는 비율군 호출 없음).
        editing_pids = [pid for pid in d.preset_version_ids if pid == preset_id or _els(db, d.id, pid)]
        _, deferred = create_shared_background(db, d, in_use=editing_pids)
        img_el.props = {**img_el.props, "zoom": 1.0, "cx": 0.5, "cy": 0.5}   # 새 이미지는 자르기 초기화
        db.flush()
        applied, skipped = rerender_edited(db, d, exclude=preset_id, note_unedited=True)
        extra = {"applied": applied, "skipped": skipped, "deferred": deferred}
    elif element == "text":
        t = next((r for r in rows if r.type == "text"), None)
        if t is None or t.locked:
            raise HTTPException(400, "문구가 없거나 잠겨 있어 재생성할 수 없습니다")
        rules = phrases.rules(bv, d.brief)
        limits = copywriter.limits_for(pv.text_limits)
        try:
            variants, attempts, *_ = copywriter.generate_checked(
                brand.name, rules, d.brief, {"platform": pv.placement.platform.name, "name": pv.placement.name}, limits)
        except Exception as e:
            raise HTTPException(502, f"문구 생성에 실패했습니다 ({type(e).__name__})")
        if not variants:
            raise HTTPException(502, "문구를 만들지 못했습니다")
        pick = next((v for v in variants if not v["violations"]), variants[0])
        last = max([c.batch_no for c in db.scalars(select(CopyVariant).where(
            CopyVariant.design_id == d.id, CopyVariant.preset_version_id == preset_id)).all()] or [0])
        for i, v in enumerate(variants, 1):
            db.add(CopyVariant(design_id=d.id, preset_version_id=preset_id, batch_no=last + 1, variant_no=i,
                               headline=v["headline"], body=v["body"], cta=v["cta"],
                               limits={k: limits[k] for k in ("headline", "body", "cta")},
                               violations=v["violations"], attempts=attempts, selected=(v is pick)))
        t.props = {**t.props, "text": pick["headline"], "text_override": True}
    else:
        raise HTTPException(400, "element 는 background 또는 text")
    res = _save_render(db, d, pv, rows, _bg_row(db, d.id, preset_id), latest_image(db, d.id, preset_id, "composite"))
    db.commit()
    return {**res, **extra, "elements": [_el_out(r) for r in rows]}


@router.post("/designs/{design_id}/presets/{preset_id}/edit/reset")
def reset_edit(design_id: int, preset_id: int, db: Session = Depends(get_db)):
    """편집 내용을 버리고 자동 배치 상태로 되돌린다."""
    d = get_design(db, design_id, ad_only=True)
    ensure_unlocked(d)
    pv = _preset(db, d, preset_id)
    # 옛 합성의 기록으로 요소를 되살리면 그 뒤에 켠 '이미지 안 본문·CTA' 설정이 빠진다 → 현재 카피·지면 설정으로 다시 합성한다
    # (배경은 그대로라 이미지 생성 호출은 없다). 합성이 요소를 비우므로 새 기본 요소는 아래 _bootstrap 이 만든다
    res = compose_preset(db, design_id, preset_id, discard_edits=True)
    bv = db.get(BrandVersion, d.brand_version_id)
    rows = _els(db, d.id, preset_id) or _bootstrap(db, d, preset_id, bv)
    db.commit()
    return {**res, "warnings": ((res["composite"] or {}).get("layout") or {}).get("warnings", []), "elements": [_el_out(r) for r in rows]}


# ---------- 실행 취소 ----------
def _snap_key(elements: list[dict]) -> list:
    """스냅샷 비교용: id 는 '처음 상태로'에서 새로 매겨지므로 뺀다."""
    return [(e["type"], e.get("role", ""), e["scope"], e["z_index"], e["locked"], e["props"]) for e in elements]


@router.post("/designs/{design_id}/presets/{preset_id}/edit/undo")
def undo_edit(design_id: int, preset_id: int, db: Session = Depends(get_db)):
    """직전 저장 상태로 돌아간다. 저장 시점마다 남는 DesignSnapshot 을 쌓인 순서대로 하나씩 꺼낸다.
    현재와 같은 내용의 스냅샷(다른 지면 편집·배경 재생성으로 남은 것)은 건너뛰고, 더 이전 스냅샷이 없으면 자동 배치 상태로 돌아간다.
    배경 이미지 자체는 되돌리지 않는다(요소의 문구·위치·크기·색·잠금만)."""
    d = get_design(db, design_id, ad_only=True)
    ensure_unlocked(d)
    pv = _preset(db, d, preset_id)
    snaps = [s for s in db.scalars(select(DesignSnapshot).where(DesignSnapshot.design_id == d.id)
                                   .order_by(DesignSnapshot.id.desc())).all()
             if s.elements and s.elements[0].get("preset_version_id") == preset_id]
    if not snaps:
        raise HTTPException(400, "되돌릴 편집이 없습니다")
    cur = _snap_key(snaps[0].elements)
    target = next((s for s in snaps[1:] if _snap_key(s.elements) != cur), None)
    for s in (snaps if target is None else snaps[:snaps.index(target)]):
        db.delete(s)                       # 현재 상태와 그 사이에 쌓인 같은 내용의 스냅샷을 쓴 것으로 친다
    for r in _els(db, d.id, preset_id):
        db.delete(r)
    db.flush()
    bv = db.get(BrandVersion, d.brand_version_id)
    if target is None:
        rows = _bootstrap(db, d, preset_id, bv)
    else:
        rows = []
        for e in target.elements:
            props = dict(e["props"])
            props["_role"] = e.get("role", "")
            row = Element(design_id=d.id, preset_version_id=preset_id, type=e["type"], scope=e["scope"],
                          props=props, z_index=e["z_index"], locked=e["locked"])
            db.add(row)
            rows.append(row)
        db.flush()
    res = _save_render(db, d, pv, rows, _bg_row(db, d.id, preset_id), latest_image(db, d.id, preset_id, "composite"), snapshot=False)
    db.commit()
    return {**res, "elements": [_el_out(r) for r in rows], "to": "이전 저장 상태" if target else "자동 배치 상태"}


# ---------- 시안(A/B/C) 바꾸기: 만든 이미지에도 새 문구를 반영한다 ----------
def _selected(db: Session, did: int, pid: int) -> CopyVariant | None:
    return db.scalar(select(CopyVariant).where(CopyVariant.design_id == did, CopyVariant.preset_version_id == pid,
                                               CopyVariant.selected.is_(True)))


def _variant_of(db: Session, did: int, pid: int, variant_no: int) -> CopyVariant | None:
    """지면의 최신 차수에서 해당 번호의 카피."""
    last = max([c.batch_no for c in db.scalars(select(CopyVariant).where(
        CopyVariant.design_id == did, CopyVariant.preset_version_id == pid)).all()] or [0])
    return db.scalar(select(CopyVariant).where(CopyVariant.design_id == did, CopyVariant.preset_version_id == pid,
                                               CopyVariant.batch_no == last, CopyVariant.variant_no == variant_no))


def _variant_plan(db: Session, d: Design, variant_no: int) -> list[dict]:
    """지면마다 시안을 바꿨을 때 이미지에 일어날 일. mode: none(합성본 없음) · plain(자동 합성이라 그대로 다시 합성) ·
    edited(편집 화면에서 저장함: 위치·크기는 유지하고 문구만 바꾼다). custom: 직접 고친 문구가 있는가(덮어쓸지 물어야 한다)."""
    plan = []
    for pid in d.preset_version_ids:
        pv = db.get(PresetVersion, pid)
        if not pv or pv.placement.content_kind == "분리 자산형":
            continue
        new, old = _variant_of(db, d.id, pid, variant_no), _selected(db, d.id, pid)
        item = {"preset_id": pid, "name": pv.placement.name, "new": new, "old": old, "mode": "none", "custom": [], "rows": []}
        if new is None or latest_image(db, d.id, pid, "composite", original_only=False) is None:
            plan.append(item)
            continue
        rows = _els(db, d.id, pid)
        if rows and _edit_saved(db, d.id, pid):
            item["mode"], item["rows"] = "edited", rows
            old_text = {"text": old.headline if old else None, "body": old.body if old else None, "cta": old.cta if old else None}
            for r in rows:
                if r.type not in old_text or r.locked:
                    continue
                cur = r.props.get("text")
                if (r.type == "text" and r.props.get("text_override")) or (old and cur != old_text[r.type]):
                    item["custom"].append({"text": "제목", "body": "본문", "cta": "CTA"}[r.type])
        else:
            item["mode"] = "plain"
        plan.append(item)
    return plan


@router.get("/designs/{design_id}/copy/select-impact")
def select_impact(design_id: int, variant_no: int, db: Session = Depends(get_db)):
    """시안 번호를 바꾸면 이미지에 어떤 영향이 있는지(저장하지 않는다). 직접 고친 문구가 있으면 화면이 확인창을 띄운다."""
    d = get_design(db, design_id, ad_only=True)
    plan = _variant_plan(db, d, variant_no)
    return {"recompose": [p["name"] for p in plan if p["mode"] in ("plain", "edited")],
            "custom": [{"preset_id": p["preset_id"], "name": p["name"], "fields": p["custom"]} for p in plan if p["custom"]]}


@router.post("/designs/{design_id}/copy/apply-variant")
def apply_variant(design_id: int, variant_no: int = Body(..., embed=True), overwrite_custom: bool = Body(False, embed=True),
                  db: Session = Depends(get_db)):
    """시안 번호를 모든 지면에 적용하고, 이미 만든 합성본도 새 문구로 다시 합성한다(배경 유지, 이미지 생성 비용 없음).
    편집 화면에서 저장한 지면은 위치·크기·잠금을 그대로 두고 문구만 바꾼다. 직접 고친 문구는 overwrite_custom 일 때만 덮어쓴다."""
    d = get_design(db, design_id, ad_only=True)
    ensure_unlocked(d)
    plan = _variant_plan(db, d, variant_no)           # 선택을 바꾸기 전에 이전 선택과 비교해 둔다
    select_variant(design_id, variant_no, db)         # 선택 표시 변경 + 검수 무효화 (404 처리 포함)
    recomposed, kept_custom, skipped = [], [], []
    for it in plan:
        pid, name, mode = it["preset_id"], it["name"], it["mode"]
        if mode == "none":
            continue
        try:
            if mode == "plain":
                make_image(design_id, pid, False, db)
                recomposed.append(pid)
                continue
            pv = db.get(PresetVersion, pid)
            rows = it["rows"]
            changed = False
            for r in rows:
                if r.type not in ("text", "body", "cta") or r.locked:
                    continue
                label = {"text": "제목", "body": "본문", "cta": "CTA"}[r.type]
                if label in it["custom"] and not overwrite_custom:
                    kept_custom.append({"preset_id": pid, "name": name, "field": label})
                    continue
                new_text = {"text": it["new"].headline, "body": it["new"].body, "cta": it["new"].cta}[r.type]
                props = {**r.props, "text": new_text}
                if r.type == "text":
                    props.pop("text_override", None)
                r.props, changed = props, True
            if changed:
                _save_render(db, d, pv, rows, _bg_row(db, d.id, pid), latest_image(db, d.id, pid, "composite"),
                             copy_variant_id=it["new"].id)
                db.commit()
                recomposed.append(pid)
        except HTTPException as e:
            db.rollback()
            skipped.append({"preset_id": pid, "name": name, "reason": str(e.detail)})
    db.commit()
    return {"variant_no": variant_no, "recomposed": recomposed, "kept_custom": kept_custom, "skipped": skipped}


# ---------- 6단계 "고쳐 보기" ----------
@router.post("/designs/{design_id}/presets/{preset_id}/copy/fix")
def fix_copy_violations(design_id: int, preset_id: int, db: Session = Depends(get_db)):
    """검수가 차단한 법적·기능성·브랜드 금지 표현이 든 필드만 모델(채팅 1회)에 고치게 하고, 코드로 다시 검사해 차단 수가 줄었을 때만
    받아들인다(copyfix). 제목은 편집 요소에 써서 다시 합성, 본문·CTA 는 선택 카피에. 그 지면의 검수를 무효화하고 검수를 바로 다시 실행해 돌려준다.
    최대 copyfix.MAX_ROUNDS 회, 회차는 brief["fix_rounds"][preset_id] 에 남는다. COPY_AGENT=0 이면 503."""
    if not copyfix.ENABLED:
        raise HTTPException(503, "고쳐 보기가 꺼져 있습니다 (COPY_AGENT=0)")
    d = get_design(db, design_id, ad_only=True)
    ensure_unlocked(d)
    if preset_id not in d.preset_version_ids:
        raise HTTPException(404, "이 시안에 포함된 규격이 아닙니다")
    pv = db.get(PresetVersion, preset_id)
    rounds_all = dict((d.brief or {}).get("fix_rounds") or {})
    rounds = list(rounds_all.get(str(preset_id)) or [])
    if len(rounds) >= copyfix.MAX_ROUNDS:
        raise HTTPException(400, f"못 고침: 고쳐 보기를 {copyfix.MAX_ROUNDS}회 모두 썼습니다 ({len(rounds)}/{copyfix.MAX_ROUNDS}). 직접 편집해 주세요")
    bv = db.get(BrandVersion, d.brand_version_id)
    cur = db.get(Brand, d.brand_id)
    brand = _brand_dict(bv, cur.name if cur else "", d.brief)
    g = gather(db, d, pv, brand)
    sel, texts = g["sel"], g["texts"]
    if sel is None:
        raise HTTPException(400, "이 지면에 선택된 카피가 없습니다")
    if not copyfix.blocking_violations(texts, brand):
        raise HTTPException(400, "이 지면에 고칠 차단 위반(법적·기능성·금지 표현)이 없습니다")
    r = copyfix.attempt(cur.name if cur else "", brand, pv.placement.platform.name, pv.placement.name, texts, len(rounds) + 1)
    changed = [s for s in r["fields"] if r["texts"].get(s) != texts.get(s)] if r["adopted"] else []
    if r["adopted"]:
        new = r["texts"]
        if "headline" in changed:
            try:        # 합성본이 있으면 편집 요소(없으면 기본 요소를 만들어)에 쓴다 — 검수가 보는 제목이 그것이다
                rows = _els(db, d.id, preset_id) or _bootstrap(db, d, preset_id, bv)
            except HTTPException:
                rows = []
            t = next((x for x in rows if x.type == "text"), None)
            if t is not None and not t.locked:         # 편집 요소가 있으면 거기(검수가 보는 제목)에 쓰고 다시 합성
                t.props = {**t.props, "text": new["headline"]}
                _save_render(db, d, pv, rows, _bg_row(db, d.id, preset_id), latest_image(db, d.id, preset_id, "composite"))
            else:
                sel.headline = new["headline"]
        if "body" in changed:
            if sel.long_body:
                sel.long_body = new["body"]
            else:
                sel.body = new["body"]
        if "cta" in changed:
            sel.cta = new["cta"]
        rules = phrases.rules(bv, d.brief)
        sel.violations = copywriter.check({"headline": sel.headline, "body": sel.body, "cta": sel.cta, "long_body": sel.long_body},
                                          copywriter.limits_for(pv.text_limits or []), rules)
        invalidate_reviews(db, d.id, preset_id)
    rounds.append({"round": len(rounds) + 1, "adopted": r["adopted"], "fields": changed,
                   "before": {k: texts.get(k) for k in copyfix.SLOTS}, "after": {k: r["texts"].get(k) for k in copyfix.SLOTS},
                   "candidate": {k: r["candidate"].get(k) for k in copyfix.SLOTS},
                   "blocking_before": r["blocking_before"], "blocking_after": r["blocking_after"],
                   "phrases": [v["phrase"] for v in r["before"]], "model": r["model"], "error": r["error"]})
    rounds_all[str(preset_id)] = rounds
    d.brief = {**(d.brief or {}), "fix_rounds": rounds_all}
    db.commit()
    rv = run_review(d.id, db)       # 검수는 모델 없이 코드만 — 바로 다시 돌려 새 결과를 보여 준다
    left = copyfix.MAX_ROUNDS - len(rounds)
    if r["adopted"]:
        note = (f"{', '.join(changed)} 을(를) 고쳤습니다: 차단 {r['blocking_before']} → {r['blocking_after']} ({len(rounds)}/{copyfix.MAX_ROUNDS}회)"
                + ("" if r["blocking_after"] == 0 else f". 남은 차단 {r['blocking_after']}건" + (" — 다시 누를 수 있습니다" if left else "")))
    else:
        why = "모델 호출 실패" if r["error"] else "고쳐도 차단이 줄지 않아" if r["candidate"] != texts else "모델이 문구를 바꾸지 않아"
        note = f"고치지 못했습니다 ({why}, 이전 문구 유지) · {len(rounds)}/{copyfix.MAX_ROUNDS}회" + ("" if left else " — 더는 시도할 수 없습니다. 직접 편집해 주세요")
    return {"preset_id": preset_id, "round": len(rounds), "max": copyfix.MAX_ROUNDS, "adopted": r["adopted"], "changed": changed,
            "blocking_before": r["blocking_before"], "blocking_after": r["blocking_after"], "texts": r["texts"],
            "model": r["model"], "note": note, "review": rv}
