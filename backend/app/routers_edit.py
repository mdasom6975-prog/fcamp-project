import base64
import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import calllog, compose, copywriter, editing, phrases
from .db import STORAGE_DIR
from .models import (Asset, Brand, BrandVersion, CopyVariant, Design, DesignSnapshot, Element,
                     GeneratedImage, PresetVersion, ReviewResult)
from .routers_assets import FONT_DIR, PROVIDED_FONTS, get_db
from .routers_images import _color, _img_out, create_shared_background

router = APIRouter(prefix="/api", dependencies=[Depends(calllog.track_design)])   # 호출 기록에 시안 id
PREVIEW_MAX = 900


# ---------- 공통 보조 ----------
def _design(db: Session, design_id: int) -> Design:
    d = db.get(Design, design_id)
    if not d or d.kind != "광고 이미지":
        raise HTTPException(404, "광고 이미지 시안을 찾을 수 없습니다")
    return d


def _preset(db: Session, d: Design, pid: int) -> PresetVersion:
    pv = db.get(PresetVersion, pid)
    if not pv or pid not in d.preset_version_ids:
        raise HTTPException(404, "이 시안에 포함된 규격이 아닙니다")
    if pv.placement.content_kind == "분리 자산형":
        raise HTTPException(400, "분리 자산형은 이미지에 문구·로고를 합성하지 않아 편집 대상이 아닙니다")
    return pv


def _font_ref(bv: BrandVersion) -> dict:
    fonts = bv.fonts or []
    f = next((x for x in fonts if x.get("role") == "제목"), None) or (fonts[0] if fonts else None)
    if f and f.get("source") == "upload" and f.get("asset_id"):
        return {"source": "upload", "asset_id": f["asset_id"]}
    if f and f.get("source") == "provided" and f.get("name"):
        return {"source": "provided", "name": f["name"]}
    return {"source": "provided", "name": "Pretendard-Bold"}


def _font_path(db: Session, ref: dict) -> str:
    if ref.get("source") == "upload" and ref.get("asset_id"):
        a = db.get(Asset, ref["asset_id"])
        if a and Path(a.file_path).exists():
            return a.file_path
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
        return compose.load_logo(a.file_path, a.mime) if a and Path(a.file_path).exists() else None
    return f


def _latest(db: Session, did: int, pid: int, kind: str, original_only: bool = False):
    rows = db.scalars(select(GeneratedImage).where(
        GeneratedImage.design_id == did, GeneratedImage.preset_version_id == pid,
        GeneratedImage.kind == kind).order_by(GeneratedImage.id.desc())).all()
    for r in rows:
        if not original_only or not (r.layout or {}).get("edited"):
            return r
    return None


def _els(db: Session, did: int, pid: int) -> list[Element]:
    return db.scalars(select(Element).where(Element.design_id == did, Element.preset_version_id == pid)
                      .order_by(Element.z_index, Element.id)).all()


def _el_out(e: Element) -> dict:
    return {"id": e.id, "type": e.type, "role": (e.props or {}).get("_role", ""), "locked": e.locked,
            "z_index": e.z_index, "scope": e.scope, "props": {k: v for k, v in e.props.items() if k != "_role"}}


def _bootstrap(db: Session, d: Design, pid: int, bv: BrandVersion) -> list[Element]:
    comp = _latest(db, d.id, pid, "composite", original_only=True)
    if not comp or (comp.layout or {}).get("separate_assets"):
        raise HTTPException(400, "먼저 이미지를 생성해 주세요 (카피 선택 후 이미지 생성)")
    for e in editing.default_elements(comp.layout, _font_ref(bv), bv.logo_asset_id):
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
    bg = Image.open(bg_path)
    img, measure = editing.render(bg, W, H, _as_render(rows), lambda ref: _font_path(db, ref), _logo_of(db))
    text_el = next((r for r in rows if r.type == "text"), None)
    brand_hex = {c["hex"].upper() for c in (bv.colors or []) if c.get("hex")}
    warns = editing.warnings_for(measure, W, H, pv.safe_area, brand_hex, phrases.effective(bv, d.brief)["forbidden_phrases"],
                                 text_el.props["text"] if text_el else None, img,
                                 text_el.props["color"] if text_el else None)
    return img, measure, warns


def _save_render(db, d, pv, rows, bg_row, comp_prev) -> dict:
    img, measure, warns = _render_rows(db, d, pv, rows, bg_row.file_path)
    STORAGE_DIR.mkdir(exist_ok=True)
    path = STORAGE_DIR / f"edt_{uuid.uuid4().hex[:10]}.png"
    img.save(path)
    layout = {"template": compose.template_for(pv.production_width, pv.production_height),
              "canvas": [pv.production_width, pv.production_height], "edited": True, "warnings": warns,
              "elements": [], "margin_source": "편집됨"}
    for r in rows:
        if r.type == "shape":
            layout["scrim"] = {"mode": r.props["mode"], "color": r.props["color"], "opacity": r.props["opacity"]}
        elif r.type == "logo" and measure.get("logo", {}).get("box"):
            layout["elements"].append({"type": "logo", "box": measure["logo"]["box"], "locked": r.locked})
        elif r.type == "text":
            layout["elements"].append({"type": "text", "text": r.props["text"], "box": measure["text"]["box"],
                                       "font_size": r.props["font_size"], "color": r.props["color"],
                                       "locked": r.locked})
    g = GeneratedImage(design_id=d.id, preset_version_id=pv.id, kind="composite", file_path=str(path),
                       width=pv.production_width, height=pv.production_height,
                       copy_variant_id=comp_prev.copy_variant_id if comp_prev else None,
                       brand_version_id=d.brand_version_id, layout=layout)
    db.add(g)
    db.add(DesignSnapshot(design_id=d.id, elements=[{"preset_version_id": pv.id, **_el_out(r)} for r in rows]))
    d.status = "수정됨"
    for rr in db.scalars(select(ReviewResult).where(ReviewResult.design_id == d.id,
                                                     ReviewResult.preset_version_id == pv.id)).all():
        rr.valid = False       # 수정 후에는 기존 검수 결과를 무효화한다
    db.flush()
    return {"composite": _img_out(g), "warnings": warns}


def _bg_row(db, did, pid) -> GeneratedImage:
    bg = _latest(db, did, pid, "background")
    if not bg or not Path(bg.file_path).exists():
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
        _save_render(db, d, opv, orows, _bg_row(db, d.id, pid), _latest(db, d.id, pid, "composite"))
        applied.append(pid)
    db.flush()
    return applied, skipped


# ---------- 조회 ----------
@router.get("/designs/{design_id}/presets/{preset_id}/edit")
def get_edit(design_id: int, preset_id: int, db: Session = Depends(get_db)):
    d = _design(db, design_id)
    pv = _preset(db, d, preset_id)
    bv = db.get(BrandVersion, d.brand_version_id)
    rows = _els(db, d.id, preset_id) or _bootstrap(db, d, preset_id, bv)
    db.commit()
    fonts = [{"label": f["label"], "ref": {"source": "provided", "name": f["name"]}} for f in PROVIDED_FONTS]
    for f in bv.fonts or []:
        if f.get("source") == "upload" and f.get("asset_id"):
            fonts.append({"label": f"업로드 폰트 ({f.get('role')})", "ref": {"source": "upload", "asset_id": f["asset_id"]}})
    comp = _latest(db, d.id, preset_id, "composite")
    return {"canvas": [pv.production_width, pv.production_height], "name": pv.placement.name,
            "elements": [_el_out(r) for r in rows], "fonts": fonts,
            "brand_colors": [{"role": c.get("role"), "hex": c.get("hex")} for c in (bv.colors or [])],
            "composite": _img_out(comp), "edited": bool((comp.layout or {}).get("edited")) if comp else False}


# ---------- 미리보기(저장하지 않음) ----------
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
def preview(design_id: int, preset_id: int, elements: list[dict] = Body(..., embed=True), db: Session = Depends(get_db)):
    d = _design(db, design_id)
    pv = _preset(db, d, preset_id)
    rows = _els(db, d.id, preset_id)
    if not rows:
        raise HTTPException(400, "편집 화면을 먼저 열어 주세요")
    applied = _apply_payload(db, rows, elements, (pv.production_width, pv.production_height))
    render_rows = [{"type": a["row"].type, "props": a["props"], "z_index": a["row"].z_index} for a in applied]
    bg = Image.open(_bg_row(db, d.id, preset_id).file_path)
    W, H = pv.production_width, pv.production_height
    img, measure = editing.render(bg, W, H, render_rows, lambda ref: _font_path(db, ref), _logo_of(db))
    bv = db.get(BrandVersion, d.brand_version_id)
    txt = next((a for a in applied if a["row"].type == "text"), None)
    warns = editing.warnings_for(measure, W, H, pv.safe_area,
                                 {c["hex"].upper() for c in (bv.colors or []) if c.get("hex")},
                                 phrases.effective(bv, d.brief)["forbidden_phrases"], txt["props"]["text"] if txt else None, img,
                                 txt["props"]["color"] if txt else None)
    small = img.copy()
    small.thumbnail((PREVIEW_MAX, PREVIEW_MAX))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=85)
    return {"image": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(),
            "warnings": warns, "size": [W, H]}


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
    d = _design(db, design_id)
    pv = _preset(db, d, preset_id)
    rows = _els(db, d.id, preset_id)
    if not rows:
        raise HTTPException(400, "편집 화면을 먼저 열어 주세요")
    applied = _apply_payload(db, rows, elements, (pv.production_width, pv.production_height))
    _persist(db, rows, applied)
    res = _save_render(db, d, pv, rows, _bg_row(db, d.id, preset_id), _latest(db, d.id, preset_id, "composite"))
    db.commit()
    return {**res, "elements": [_el_out(r) for r in rows]}


@router.post("/designs/{design_id}/edit/common-text")
def common_text(design_id: int, text: str = Body(..., embed=True), db: Session = Depends(get_db)):
    """문구를 모든 지면에 적용한다. 잠겼거나 지면별로 따로 수정한 문구는 건드리지 않는다."""
    d = _design(db, design_id)
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
            _save_render(db, d, pv, rows, _bg_row(db, d.id, pid), _latest(db, d.id, pid, "composite"))
            applied_ids.append(pid)
    db.commit()
    return {"applied": applied_ids, "skipped": skipped}


# ---------- 요소 재생성 / 처음 상태로 ----------
@router.post("/designs/{design_id}/presets/{preset_id}/edit/regenerate")
def regenerate(design_id: int, preset_id: int, element: str = Body(..., embed=True), db: Session = Depends(get_db)):
    d = _design(db, design_id)
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
    res = _save_render(db, d, pv, rows, _bg_row(db, d.id, preset_id), _latest(db, d.id, preset_id, "composite"))
    db.commit()
    return {**res, **extra, "elements": [_el_out(r) for r in rows]}


@router.post("/designs/{design_id}/presets/{preset_id}/edit/reset")
def reset_edit(design_id: int, preset_id: int, db: Session = Depends(get_db)):
    """편집 내용을 버리고 자동 배치 상태로 되돌린다."""
    d = _design(db, design_id)
    pv = _preset(db, d, preset_id)
    for r in _els(db, d.id, preset_id):
        db.delete(r)
    db.flush()
    bv = db.get(BrandVersion, d.brand_version_id)
    rows = _bootstrap(db, d, preset_id, bv)
    res = _save_render(db, d, pv, rows, _bg_row(db, d.id, preset_id), _latest(db, d.id, preset_id, "composite"))
    db.commit()
    return {**res, "elements": [_el_out(r) for r in rows]}
