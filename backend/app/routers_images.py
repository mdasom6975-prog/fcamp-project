import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import compose, imagegen
from .db import BASE_DIR, STORAGE_DIR
from .models import (Asset, Brand, BrandVersion, CopyVariant, Design, DesignSnapshot, Element, GeneratedImage,
                     IconAsset, PresetVersion, ReviewResult)
from .routers_assets import FONT_DIR, get_db
from .routers_review import invalidate_icon_reviews, invalidate_reviews

router = APIRouter(prefix="/api")
log = logging.getLogger("app.images")


def _font_path(bv: BrandVersion, role: str, db: Session) -> str:
    """브랜드 폰트(역할 일치 → 첫 번째 → 제공 폰트 Pretendard Bold) 파일 경로."""
    fonts = bv.fonts or []
    f = next((x for x in fonts if x.get("role") == role), None) or (fonts[0] if fonts else None)
    if f and f.get("source") == "upload" and f.get("asset_id"):
        a = db.get(Asset, f["asset_id"])
        if a and Path(a.file_path).exists():
            return a.file_path
    if f and f.get("source") == "provided" and f.get("name"):
        p = FONT_DIR / f"{f['name']}.otf"
        if p.exists():
            return str(p)
    return str(FONT_DIR / "Pretendard-Bold.otf")


def _color(bv: BrandVersion, role: str) -> str | None:
    c = next((x for x in (bv.colors or []) if x.get("role") == role), None)
    return c["hex"] if c else None


def _img_out(g: GeneratedImage | None) -> dict | None:
    if not g:
        return None
    return {"id": g.id, "kind": g.kind, "width": g.width, "height": g.height,
            "copy_variant_id": g.copy_variant_id, "layout": g.layout, "created_at": g.created_at.isoformat(),
            # 캐시 무효화용 파일 토큰: 행 id 는 삭제 후 재사용될 수 있지만 파일 이름(uuid)은 고유하다
            "file": Path(g.file_path).stem}


def _latest(db: Session, design_id: int, pid: int, kind: str) -> GeneratedImage | None:
    return db.scalar(select(GeneratedImage).where(
        GeneratedImage.design_id == design_id, GeneratedImage.preset_version_id == pid,
        GeneratedImage.kind == kind).order_by(GeneratedImage.id.desc()).limit(1))


def _latest_design_bg(db: Session, design_id: int, template: str | None = None) -> GeneratedImage | None:
    """시안의 최신 배경. template 을 주면 그 비율군(생성 크기의 비율 기준)으로 만든 배경만 찾는다."""
    rows = db.scalars(select(GeneratedImage).where(
        GeneratedImage.design_id == design_id, GeneratedImage.kind == "background"
    ).order_by(GeneratedImage.id.desc())).all()
    for g in rows:
        if template is None or compose.template_for(g.width, g.height) == template:
            return g
    return None


def _design(db: Session, design_id: int) -> Design:
    d = db.get(Design, design_id)
    if not d or d.kind != "광고 이미지":
        raise HTTPException(404, "광고 이미지 시안을 찾을 수 없습니다")
    return d


@router.get("/image-usage")
def image_usage():
    return imagegen.usage()


@router.get("/designs/{design_id}/images")
def list_images(design_id: int, db: Session = Depends(get_db)):
    d = _design(db, design_id)
    out = []
    for pid in d.preset_version_ids:
        out.append({"preset_id": pid, "background": _img_out(_latest(db, design_id, pid, "background")),
                    "composite": _img_out(_latest(db, design_id, pid, "composite"))})
    return out


@router.post("/designs/{design_id}/copy/{variant_id}/select")
def select_copy(design_id: int, variant_id: int, db: Session = Depends(get_db)):
    _design(db, design_id)
    v = db.get(CopyVariant, variant_id)
    if not v or v.design_id != design_id:
        raise HTTPException(404, "카피를 찾을 수 없습니다")
    db.execute(update(CopyVariant).where(CopyVariant.design_id == design_id,
                                         CopyVariant.preset_version_id == v.preset_version_id).values(selected=False))
    v.selected = True
    invalidate_reviews(db, design_id, v.preset_version_id)
    db.commit()
    return {"selected": variant_id, "preset_id": v.preset_version_id}


def _edit_saved(db: Session, design_id: int, preset_id: int) -> bool:
    """이 지면을 편집 화면에서 '저장'한 적이 있는가 (저장 시점마다 DesignSnapshot 이 남는다)."""
    for snap in db.scalars(select(DesignSnapshot).where(DesignSnapshot.design_id == design_id)).all():
        if any(e.get("preset_version_id") == preset_id for e in (snap.elements or [])):
            return True
    return False


def _gen_error(e: Exception) -> str:
    """이미지 생성 실패 메시지. 서버 로그에 원인을 남기고, 화면에는 공급자 오류 문구(모델·파라미터 안내)만 보여 준다.
    키 등 민감 정보는 메시지에 넣지 않는다."""
    log.exception("이미지 생성 실패: %s", type(e).__name__)
    msg = imagegen.error_info(e).get("message")
    if msg:
        return f"이미지 생성에 실패했습니다 ({type(e).__name__}): {msg[:300]}"
    return f"이미지 생성에 실패했습니다 ({type(e).__name__})"


def _generate_one(db: Session, d: Design, template: str,
                  reference: GeneratedImage | None = None) -> tuple[Path, Image.Image, str, dict]:
    """템플릿(비율군) 하나의 배경을 생성해 저장한다. 반환: (파일 경로, 이미지, 프롬프트, 기록용 layout).
    생성 크기는 imagegen.size_for(비율군별이면 1024×1536 / 1536×1024 / 1024²), 형태는 브리프 image_style(자동이면 톤·설명으로).
    reference(다른 비율군의 배경 행)를 주고 변형이 가능하면 그 이미지를 참조로 붙여 '같은 장면'을 이 비율의 구도로 다시 만든다."""
    bv = db.get(BrandVersion, d.brand_version_id)
    brand = db.get(Brand, d.brand_id)
    style, reason = imagegen.pick_style(bv.image_tone or {}, d.brief or {})
    gw, gh = imagegen.size_for(template)
    ref_img = None
    if reference is not None and imagegen.can_vary() and Path(reference.file_path).exists():
        ref_img = Image.open(reference.file_path)
    try:
        prompt = imagegen.build_prompt(brand.name, bv.image_tone or {}, d.brief, template, style)
        if ref_img is not None:
            prompt = imagegen.build_variant_prompt(prompt)
            bg = imagegen.generate_variant(prompt, gw, gh, ref_img)
        else:
            bg = imagegen.generate_background(prompt, gw, gh)
    except imagegen.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except Exception as e:
        raise HTTPException(502, _gen_error(e))
    STORAGE_DIR.mkdir(exist_ok=True)
    path = STORAGE_DIR / f"bg_{uuid.uuid4().hex[:10]}.png"
    bg.save(path)
    layout = {"template": template, "size": [bg.width, bg.height], "style": style, "style_reason": reason,
              "prompts": imagegen.prompt_refs(template, style, variant=ref_img is not None)}
    if ref_img is not None:
        layout["reference"] = {"background_id": reference.id, "file": Path(reference.file_path).name,
                               "template": (reference.layout or {}).get("template")}
    return path, bg, prompt, layout


def _bg_row(d: Design, pid: int, path: Path | str, bg_w: int, bg_h: int, prompt: str | None, bv_id: int,
            layout: dict) -> GeneratedImage:
    return GeneratedImage(design_id=d.id, preset_version_id=pid, kind="background", file_path=str(path),
                          width=bg_w, height=bg_h, prompt=prompt, brand_version_id=bv_id, layout=dict(layout))


def create_shared_background(db: Session, d: Design) -> dict[int, GeneratedImage]:
    """배경을 비율군(가로형/정사각/세로형)마다 1장씩 만들고, 같은 비율군의 규격은 같은 파일로 연결한다. {preset_id: 배경 행}.
    첫 비율군(시안의 첫 규격 기준)은 프롬프트로 생성하고, 나머지 비율군은 그 이미지를 참조로 붙여 **같은 장면**을
    각 비율의 구도로 다시 만든다(images.edit, IMAGE_BG_VARIANT=0 이면 독립 생성). 비용은 비율군 수(1~3장)만큼 든다.
    IMAGE_BG_PER_RATIO=0 이면 정사각 1장을 모두가 공유."""
    sizes = [(pid, pv.production_width, pv.production_height) for pid in d.preset_version_ids
             if (pv := db.get(PresetVersion, pid)) and pv.production_width]
    if not sizes:
        raise HTTPException(400, "제작 크기가 정해진 규격이 없습니다")
    bv = db.get(BrandVersion, d.brand_version_id)
    groups: dict[str, list[int]] = {}
    for pid, w, h in sizes:
        groups.setdefault(compose.template_for(w, h), []).append(pid)
    if not imagegen.PER_RATIO:   # 예전 방식: 첫 번째 규격의 구도로 1장
        groups = {compose.template_for(sizes[0][1], sizes[0][2]): [pid for pid, _, _ in sizes]}
    rows: dict[int, GeneratedImage] = {}
    master: GeneratedImage | None = None
    for template, pids in groups.items():
        try:
            path, bg, prompt, layout = _generate_one(db, d, template, reference=master)
        except HTTPException as e:
            if master is None:
                raise
            # 변형 생성이 실패해도 이미 만든(과금된) 첫 배경을 버리지 않는다: 이 비율군은 첫 배경을 잘라 쓰고 사유를 남긴다
            log.warning("비율군 %s 변형 실패 → 첫 배경으로 대체: %s", template, e.detail)
            layout = {**(master.layout or {}), "template": template, "fallback": str(e.detail)}
            for pid in pids:
                rows[pid] = _bg_row(d, pid, master.file_path, master.width, master.height, master.prompt, bv.id, layout)
                db.add(rows[pid])
            db.flush()
            continue
        for pid in pids:
            rows[pid] = _bg_row(d, pid, path, bg.width, bg.height, prompt, bv.id, layout)
            db.add(rows[pid])
        db.flush()
        if master is None:
            master = rows[pids[0]]
    return rows


@router.post("/designs/{design_id}/background/regenerate")
def regenerate_background(design_id: int, style: str | None = Body(None, embed=True), db: Session = Depends(get_db)):
    """시안 전체의 배경을 비율군마다 새로 생성하고, 같은 비율군 매체가 같은 원본을 공유하도록 연결한다.
    style 을 주면 브리프의 배경 형태(image_style)를 그 값으로 바꾼 뒤 생성한다."""
    d = _design(db, design_id)
    if style is not None:
        style = style.strip() or imagegen.AUTO_STYLE
        if style != imagegen.AUTO_STYLE and style not in imagegen.STYLES:
            raise HTTPException(400, f"배경 형태는 {[imagegen.AUTO_STYLE, *imagegen.STYLES]} 중 하나")
        d.brief = {**(d.brief or {}), "image_style": style}
        db.flush()
    rows = create_shared_background(db, d)
    db.commit()
    first = next(iter(rows.values()))
    fallbacks = sorted({r.layout.get("fallback") for r in rows.values() if r.layout.get("fallback")})
    return {"preset_ids": list(rows), "generated": len({r.file_path for r in rows.values()}),
            "style": first.layout.get("style"), "style_reason": first.layout.get("style_reason"),
            "warnings": [f"일부 비율군은 변형 생성에 실패해 첫 배경을 잘라 썼습니다: {m}" for m in fallbacks]}


@router.post("/designs/{design_id}/reset-generated")
def reset_generated(design_id: int, include_copy: bool = Body(False, embed=True),
                    include_icons: bool = Body(False, embed=True), db: Session = Depends(get_db)):
    """시안의 생성 결과(배경·합성·편집 요소·검수 결과)만 지운다. 브랜드·규격·입력 내용(brief)은 유지하고,
    카피는 include_copy=True, 아이콘 세트는 include_icons=True 일 때만 지운다(세트는 브랜드 규칙 버전 소유라 같은 버전을
    쓰는 다른 캠페인에서도 사라진다). 이미지 생성 API 호출 테스트를 처음부터 다시 하기 위한 용도."""
    d = _design(db, design_id)
    imgs = db.scalars(select(GeneratedImage).where(GeneratedImage.design_id == design_id)).all()
    paths = {g.file_path for g in imgs}
    for g in imgs:
        db.delete(g)
    icons_deleted = 0
    if include_icons:
        icon_files: set[str] = set()
        for a in db.scalars(select(IconAsset).where(IconAsset.brand_version_id == d.brand_version_id)).all():
            icon_files.update(p for p in (a.png_path, a.source_path) if p)
            db.delete(a)
            icons_deleted += 1
        invalidate_icon_reviews(db, d.brand_version_id)
        paths |= icon_files
    for model in (Element, DesignSnapshot, ReviewResult):
        for r in db.scalars(select(model).where(model.design_id == design_id)).all():
            db.delete(r)
    if include_copy:
        for r in db.scalars(select(CopyVariant).where(CopyVariant.design_id == design_id)).all():
            db.delete(r)
    d.status = "미검수"
    db.flush()
    # 다른 행이 같은 파일을 쓰지 않을 때만 파일 삭제 (경로는 STORAGE_DIR 하위만)
    still = {p for (p,) in db.execute(select(GeneratedImage.file_path)).all()}
    still |= {p for row in db.execute(select(IconAsset.png_path, IconAsset.source_path)).all() for p in row if p}
    removed = 0
    for p in paths - still:
        fp = Path(p)
        if STORAGE_DIR in fp.parents and fp.exists():
            fp.unlink()
            removed += 1
    for t in (STORAGE_DIR / "thumbs").glob("*.jpg"):      # 이 시안의 축소본 캐시(파일 이름 기준)도 정리
        if any(t.name.startswith(Path(p).stem + "_") for p in paths):
            t.unlink()
    db.commit()
    return {"deleted_images": len(imgs), "deleted_files": removed, "copy_cleared": include_copy,
            "icons_deleted": icons_deleted}


@router.post("/designs/{design_id}/presets/{preset_id}/image")
def make_image(design_id: int, preset_id: int, regenerate_background: bool = Body(False, embed=True),
               db: Session = Depends(get_db)):
    d = _design(db, design_id)
    if preset_id not in d.preset_version_ids:
        raise HTTPException(404, "이 시안에 포함된 규격이 아닙니다")
    pv = db.get(PresetVersion, preset_id)
    if not pv or not pv.production_width:
        raise HTTPException(400, "제작 크기가 정해지지 않은 규격입니다")
    w, h = pv.production_width, pv.production_height
    els = db.scalars(select(Element).where(Element.design_id == design_id, Element.preset_version_id == preset_id)).all()
    if els:
        if _edit_saved(db, design_id, preset_id):
            raise HTTPException(409, "편집 화면에서 저장한 내용이 있습니다. 편집 화면의 '처음 상태로 되돌리기' 후 다시 합성해 주세요")
        # 편집 화면을 열기만 하면 기본 요소가 만들어진다. 저장한 적이 없으면 자동 합성으로 되돌려도 잃는 것이 없다
        for e in els:
            db.delete(e)
        db.flush()
    separate = pv.placement.content_kind == "분리 자산형"
    bv = db.get(BrandVersion, d.brand_version_id)
    brand = db.get(Brand, d.brand_id)

    sel = None
    if not separate:
        sel = db.scalar(select(CopyVariant).where(
            CopyVariant.design_id == design_id, CopyVariant.preset_version_id == preset_id,
            CopyVariant.selected.is_(True)))
        if not sel:
            raise HTTPException(400, "합성에 사용할 카피를 먼저 선택해 주세요")

    # 배경은 비율군(템플릿)마다 1장 생성하고, 같은 비율군 매체는 같은 원본을 각 규격 크기에 맞춰 자른다(cover_crop).
    # 다시 만들기를 요청했거나 이 시안에 배경이 하나도 없을 때만 새로 생성한다. 같은 비율군 배경이 없으면
    # (예: 예전에 정사각 1장만 만든 시안) 추가 생성 없이 시안의 최신 배경을 그대로 자른다.
    tpl = compose.template_for(w, h)
    bg_row = None
    if not regenerate_background:
        same = _latest_design_bg(db, design_id, tpl)
        # 같은 비율군 배경이 있으면 그대로 연결(생성 호출 없음). 없고 다른 비율군 배경만 있으면: 변형 생성이 가능할 때는
        # 그 장면을 참조해 이 비율군 배경을 1장 만들고(비율군마다 1장 원칙), 불가능하면 예전처럼 잘라 쓴다
        src = same or (None if imagegen.can_vary() else _latest_design_bg(db, design_id))
        if src is not None and Path(src.file_path).exists():
            bg_row = _latest(db, design_id, preset_id, "background")
            if bg_row is None or bg_row.file_path != src.file_path:   # 이 규격에 연결(파일은 공유, 생성 호출 없음)
                bg_row = _bg_row(d, preset_id, src.file_path, src.width, src.height, src.prompt,
                                 src.brand_version_id, src.layout or {})
                db.add(bg_row)
                db.flush()
    if bg_row is None:
        # 새로 만들 때 시안에 다른 배경이 있으면 그 장면을 참조해 통일한다. 변형이 실패하면 그 배경을 잘라 쓴다(폴백)
        ref = _latest_design_bg(db, design_id)
        try:
            path, bg, prompt, layout = _generate_one(db, d, tpl, reference=ref if ref and ref.file_path else None)
            bg_row = _bg_row(d, preset_id, path, bg.width, bg.height, prompt, bv.id, layout)
        except HTTPException as e:
            if ref is None or regenerate_background or not Path(ref.file_path).exists():
                raise
            log.warning("비율군 %s 변형 실패 → 기존 배경으로 대체: %s", tpl, e.detail)
            bg_row = _bg_row(d, preset_id, ref.file_path, ref.width, ref.height, ref.prompt, ref.brand_version_id,
                             {**(ref.layout or {}), "template": tpl, "fallback": str(e.detail)})
        db.add(bg_row)
        db.flush()
    bg_img = Image.open(bg_row.file_path)

    if separate:
        # 분리 자산형: 텍스트 없는 이미지만. 로고·카피는 내보내기에서 별도 파일로 제공한다.
        nw, nh, x, y, crop = compose.crop_window(bg_img.convert("RGB"), w, h)
        out_img = bg_img.convert("RGB").resize((nw, nh), Image.LANCZOS).crop((x, y, x + w, y + h))
        report = {"template": compose.template_for(w, h), "canvas": [w, h], "separate_assets": True,
                  "crop": crop, "warnings": [], "elements": []}
        kind_path = STORAGE_DIR / f"sep_{uuid.uuid4().hex[:10]}.png"
        out_img.save(kind_path)
        row = GeneratedImage(design_id=design_id, preset_version_id=preset_id, kind="composite",
                             file_path=str(kind_path), width=w, height=h, brand_version_id=bv.id, layout=report)
    else:
        logo = None
        if bv.logo_asset_id:
            a = db.get(Asset, bv.logo_asset_id)
            if a and Path(a.file_path).exists():
                logo = compose.load_logo(a.file_path, a.mime)
        text_hex = _color(bv, "텍스트색")
        scrim_hex = _color(bv, "배경색")
        out_img, report = compose.compose(
            bg_img, w, h, sel.headline, _font_path(bv, "제목", db), logo,
            pv.safe_area, (bv.logo_margin or {}).get("percent"), text_hex, scrim_hex)
        if bv.logo_asset_id and logo is None:
            report["warnings"].append("로고 파일을 찾지 못해 합성하지 못했습니다")
        if not bv.logo_asset_id:
            report["warnings"].append("브랜드에 로고가 등록되어 있지 않습니다")
        path = STORAGE_DIR / f"cmp_{uuid.uuid4().hex[:10]}.png"
        out_img.save(path)
        row = GeneratedImage(design_id=design_id, preset_version_id=preset_id, kind="composite",
                             file_path=str(path), width=w, height=h, copy_variant_id=sel.id,
                             brand_version_id=bv.id, layout=report)
    db.add(row)
    invalidate_reviews(db, design_id, preset_id)
    db.commit()
    return {"background": _img_out(bg_row), "composite": _img_out(row)}


@router.get("/generated/{image_id}/file")
def image_file(image_id: int, db: Session = Depends(get_db)):
    g = db.get(GeneratedImage, image_id)
    if not g or not Path(g.file_path).exists():
        raise HTTPException(404, "이미지를 찾을 수 없습니다")
    return FileResponse(g.file_path, media_type="image/png")


THUMB_SIDE = 360


@router.get("/generated/{image_id}/thumb")
def image_thumb(image_id: int, db: Session = Depends(get_db)):
    """썸네일 그리드용 축소본(긴 변 360px JPEG). 생성 결과는 바뀌지 않으므로 id 기준으로 파일 캐시한다."""
    g = db.get(GeneratedImage, image_id)
    if not g or not Path(g.file_path).exists():
        raise HTTPException(404, "이미지를 찾을 수 없습니다")
    out = STORAGE_DIR / "thumbs" / f"{Path(g.file_path).stem}_{THUMB_SIDE}.jpg"   # 파일 이름 기준(행 id 재사용 대비)
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(g.file_path).convert("RGB")
        im.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
        im.save(out, "JPEG", quality=82)
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
