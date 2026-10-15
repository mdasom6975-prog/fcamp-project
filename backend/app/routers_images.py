import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import compose, imagegen
from .db import BASE_DIR, STORAGE_DIR
from .models import Asset, Brand, BrandVersion, CopyVariant, Design, Element, GeneratedImage, PresetVersion
from .routers_assets import FONT_DIR, get_db
from .routers_review import invalidate_reviews

router = APIRouter(prefix="/api")


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
            "copy_variant_id": g.copy_variant_id, "layout": g.layout, "created_at": g.created_at.isoformat()}


def _latest(db: Session, design_id: int, pid: int, kind: str) -> GeneratedImage | None:
    return db.scalar(select(GeneratedImage).where(
        GeneratedImage.design_id == design_id, GeneratedImage.preset_version_id == pid,
        GeneratedImage.kind == kind).order_by(GeneratedImage.id.desc()).limit(1))


def _design(db: Session, design_id: int) -> Design:
    d = db.get(Design, design_id)
    if not d or d.kind != "광고 이미지":
        raise HTTPException(404, "광고 이미지 시안을 찾을 수 없습니다")
    return d


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
    if db.scalar(select(Element.id).where(Element.design_id == design_id,
                                          Element.preset_version_id == preset_id).limit(1)) is not None:
        raise HTTPException(409, "편집한 내용이 있습니다. 편집 화면에서 '처음 상태로 되돌리기' 후 다시 합성해 주세요")
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

    # 배경: 있으면 재사용(비용 없음), 없거나 다시 만들기 요청이면 새로 생성
    bg_row = None if regenerate_background else _latest(db, design_id, preset_id, "background")
    if bg_row is None or not Path(bg_row.file_path).exists():
        tpl = compose.template_for(w, h)
        prompt = imagegen.build_prompt(brand.name, bv.image_tone or {}, d.brief, tpl)
        try:
            bg = imagegen.generate_background(prompt, w, h)
        except Exception as e:  # 키 등 민감 정보는 메시지에 넣지 않는다
            raise HTTPException(502, f"이미지 생성에 실패했습니다 ({type(e).__name__})")
        STORAGE_DIR.mkdir(exist_ok=True)
        path = STORAGE_DIR / f"bg_{uuid.uuid4().hex[:10]}.png"
        bg.save(path)
        bg_row = GeneratedImage(design_id=design_id, preset_version_id=preset_id, kind="background",
                                file_path=str(path), width=bg.width, height=bg.height, prompt=prompt,
                                brand_version_id=bv.id, layout={})
        db.add(bg_row)
        db.flush()
    bg_img = Image.open(bg_row.file_path)

    if separate:
        # 분리 자산형: 텍스트 없는 이미지만. 로고·카피는 내보내기에서 별도 파일로 제공한다.
        out_img = compose.cover_crop(bg_img, w, h)
        report = {"template": compose.template_for(w, h), "canvas": [w, h], "separate_assets": True,
                  "warnings": [], "elements": []}
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
