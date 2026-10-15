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
                     PresetVersion, ReviewResult)
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


def _latest_design_bg(db: Session, design_id: int) -> GeneratedImage | None:
    return db.scalar(select(GeneratedImage).where(
        GeneratedImage.design_id == design_id, GeneratedImage.kind == "background"
    ).order_by(GeneratedImage.id.desc()).limit(1))


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


def create_shared_background(db: Session, d: Design) -> dict[int, GeneratedImage]:
    """배경을 1회만 생성하고 시안에 포함된 모든 규격에 같은 파일로 연결한다. {preset_id: 배경 행}"""
    sizes = [(pid, pv.production_width, pv.production_height) for pid in d.preset_version_ids
             if (pv := db.get(PresetVersion, pid)) and pv.production_width]
    if not sizes:
        raise HTTPException(400, "제작 크기가 정해진 규격이 없습니다")
    bv = db.get(BrandVersion, d.brand_version_id)
    brand = db.get(Brand, d.brand_id)
    _, w, h = sizes[0]  # 구도 안내(여백 위치)는 첫 번째 규격 기준
    try:
        prompt = imagegen.build_prompt(brand.name, bv.image_tone or {}, d.brief, compose.template_for(w, h))
        bg = imagegen.generate_background(prompt, w, h)
    except imagegen.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except Exception as e:  # 키 등 민감 정보는 메시지에 넣지 않는다
        raise HTTPException(502, f"이미지 생성에 실패했습니다 ({type(e).__name__})")
    STORAGE_DIR.mkdir(exist_ok=True)
    path = STORAGE_DIR / f"bg_{uuid.uuid4().hex[:10]}.png"
    bg.save(path)
    rows: dict[int, GeneratedImage] = {}
    for pid, _w, _h in sizes:
        rows[pid] = GeneratedImage(design_id=d.id, preset_version_id=pid, kind="background",
                                   file_path=str(path), width=bg.width, height=bg.height, prompt=prompt,
                                   brand_version_id=bv.id, layout={})
        db.add(rows[pid])
    db.flush()
    return rows


@router.post("/designs/{design_id}/background/regenerate")
def regenerate_background(design_id: int, db: Session = Depends(get_db)):
    """시안 전체의 배경을 1회만 새로 생성하고, 모든 매체가 같은 원본을 공유하도록 연결한다."""
    d = _design(db, design_id)
    rows = create_shared_background(db, d)
    db.commit()
    return {"preset_ids": list(rows)}


@router.post("/designs/{design_id}/reset-generated")
def reset_generated(design_id: int, include_copy: bool = Body(False, embed=True), db: Session = Depends(get_db)):
    """시안의 생성 결과(배경·합성·편집 요소·검수 결과)만 지운다. 브랜드·규격·입력 내용(brief)·아이콘은 유지하고,
    카피는 include_copy=True 일 때만 지운다. 이미지 생성 API 호출 테스트를 처음부터 다시 하기 위한 용도."""
    d = _design(db, design_id)
    imgs = db.scalars(select(GeneratedImage).where(GeneratedImage.design_id == design_id)).all()
    paths = {g.file_path for g in imgs}
    for g in imgs:
        db.delete(g)
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
    removed = 0
    for p in paths - still:
        fp = Path(p)
        if STORAGE_DIR in fp.parents and fp.exists():
            fp.unlink()
            removed += 1
    db.commit()
    return {"deleted_images": len(imgs), "deleted_files": removed, "copy_cleared": include_copy}


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

    # 배경은 시안당 한 번만 생성하고, 매체별로는 같은 원본을 각 규격 크기에 맞춰 리사이즈(cover_crop)한다.
    # 다시 만들기를 요청했거나 이 시안에 배경이 하나도 없을 때만 새로 생성한다.
    src = _latest_design_bg(db, design_id)
    bg_row = None
    if not regenerate_background and src is not None and Path(src.file_path).exists():
        bg_row = _latest(db, design_id, preset_id, "background")
        if bg_row is None or bg_row.file_path != src.file_path:   # 이 규격에 연결(파일은 공유, 생성 호출 없음)
            bg_row = GeneratedImage(design_id=design_id, preset_version_id=preset_id, kind="background",
                                    file_path=src.file_path, width=src.width, height=src.height,
                                    prompt=src.prompt, brand_version_id=src.brand_version_id, layout={})
            db.add(bg_row)
            db.flush()
    if bg_row is None:
        tpl = compose.template_for(w, h)
        try:
            prompt = imagegen.build_prompt(brand.name, bv.image_tone or {}, d.brief, tpl)
            bg = imagegen.generate_background(prompt, w, h)
        except imagegen.QuotaExceeded as e:
            raise HTTPException(429, str(e))
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
