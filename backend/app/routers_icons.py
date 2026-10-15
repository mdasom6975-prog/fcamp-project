import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import icons
from .db import STORAGE_DIR
from .models import Brand, BrandVersion, Design, GeneratedImage, IconAsset
from .routers_assets import get_db
from .routers_review import invalidate_reviews

router = APIRouter(prefix="/api")
WEB_SIZE = (256, 256)   # 웹용 아이콘 기본 크기 (사용자 결정)


def _design(db: Session, design_id: int) -> Design:
    d = db.get(Design, design_id)
    if not d:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")
    return d


def _version_set(db: Session, version_id: int) -> list[IconAsset]:
    """브랜드 규칙 버전 하나에는 아이콘 세트가 한 세트만 있다. (가장 최근 차수의 세트)"""
    last = db.scalar(select(func.max(IconAsset.batch_no)).where(IconAsset.brand_version_id == version_id))
    if not last:
        return []
    return list(db.scalars(select(IconAsset).where(IconAsset.brand_version_id == version_id, IconAsset.batch_no == last)
                           .order_by(IconAsset.position)).all())


def _size(d: Design) -> tuple[int, int]:
    ic = (d.brief or {}).get("icon") or {}
    if ic.get("purpose") == "사용자 지정" and ic.get("width") and ic.get("height"):
        return int(ic["width"]), int(ic["height"])
    return WEB_SIZE


def _style(bv: BrandVersion) -> dict:
    st = bv.icon_style or {}
    color = next((c["hex"] for c in (bv.colors or []) if c.get("role") == "주조색"), "#000000")
    return {"stroke_width": float(st.get("stroke_width") or 2), "corner": st.get("corner") or "둥근",
            "fill": st.get("fill") or "윤곽", "color": color}


def _out(a: IconAsset) -> dict:
    return {"id": a.id, "position": a.position, "name": a.name, "concept": a.concept, "style": a.style,
            "width": a.width, "height": a.height, "violations": a.violations, "batch_no": a.batch_no}


def _save(db, d, bv, batch, pos, name, concept, elements, style, w, h, forbidden, existing=None):
    clean = icons.sanitize(elements)
    if not clean:
        raise ValueError("유효한 도형이 없습니다")
    svg, info = icons.build_svg(clean, style)
    STORAGE_DIR.mkdir(exist_ok=True)
    path = STORAGE_DIR / f"icon_{uuid.uuid4().hex[:10]}.png"
    path.write_bytes(icons.to_png(svg, w, h))
    viol = [{"type": "forbidden", "detail": f"기능명에 금지 표현 '{p}' 포함"} for p in forbidden if p and p in name]
    fields = dict(name=name[:60], concept=(concept or "")[:200], elements=clean,
                  style={**style, **info, "viewBox": icons.VIEW, "live_area": icons.LIVE},
                  svg=svg, png_path=str(path), width=w, height=h, violations=viol)
    if existing is not None:
        for k, v in fields.items():
            setattr(existing, k, v)
        return existing
    a = IconAsset(design_id=d.id, brand_version_id=bv.id, batch_no=batch, position=pos, **fields)
    db.add(a)
    return a


@router.get("/designs/{design_id}/icons")
def list_icons(design_id: int, db: Session = Depends(get_db)):
    d = _design(db, design_id)
    return [_out(a) for a in _version_set(db, d.brand_version_id)]


@router.post("/designs/{design_id}/icons")
def create_icons(design_id: int, db: Session = Depends(get_db)):
    d = _design(db, design_id)
    # 규칙 버전마다 한 세트만: 이미 있으면 새로 만들지 않는다 (개별 아이콘은 다시 그릴 수 있다)
    if _version_set(db, d.brand_version_id):
        raise HTTPException(409, "이 브랜드 규칙 버전의 아이콘 세트는 이미 있습니다. 개별 아이콘은 다시 그릴 수 있습니다.")
    if d.kind == "광고 이미지":
        has_image = db.scalar(select(func.count()).select_from(GeneratedImage).where(
            GeneratedImage.design_id == d.id, GeneratedImage.kind == "composite"))
        if not has_image:
            raise HTTPException(400, "광고 이미지를 먼저 생성한 뒤 아이콘 세트를 만들 수 있습니다")
    bv = db.get(BrandVersion, d.brand_version_id)
    brand = db.get(Brand, d.brand_id)
    style, (w, h) = _style(bv), _size(d)
    forbidden = bv.forbidden_phrases or []
    try:
        items = icons.propose_and_draw(brand.name, d.brief, style, forbidden)
    except Exception as e:
        raise HTTPException(502, f"아이콘 생성에 실패했습니다 ({type(e).__name__})")
    # 모델이 목표 개수보다 적게 주면 겹치지 않는 기능을 추가로 받는다 (최대 2회)
    for _ in range(2):
        names = [str(x.get("name") or "").strip() for x in items]
        uniq = []
        for it in items:
            n = str(it.get("name") or "").strip()
            if n and n not in [str(u.get("name")).strip() for u in uniq]:
                uniq.append(it)
        items = uniq
        if len(items) >= icons.COUNT:
            break
        try:
            items += icons.propose_more(brand.name, d.brief, style, forbidden,
                                        [str(x.get("name")) for x in items], icons.COUNT - len(items))
        except Exception:
            break
    batch = 1
    made, skipped = 0, []
    for i, it in enumerate(items, 1):
        name = str(it.get("name") or "").strip()
        try:
            if not name:
                raise ValueError("기능명이 없습니다")
            _save(db, d, bv, batch, i, name, str(it.get("concept") or ""), it.get("elements"), style, w, h, forbidden)
            made += 1
        except Exception as e:
            # 도형이 잘못된 아이콘은 한 번 다시 그려 본다
            try:
                els = icons.redraw_one(brand.name, d.brief, name or f"아이콘 {i}", str(it.get("concept") or ""), style)
                _save(db, d, bv, batch, i, name or f"아이콘 {i}", str(it.get("concept") or ""), els, style, w, h, forbidden)
                made += 1
            except Exception as e2:
                skipped.append({"position": i, "name": name, "reason": str(e2)[:80]})
    invalidate_reviews(db, d.id)
    db.commit()
    return {"icons": list_icons(design_id, db), "requested": icons.COUNT, "made": made, "skipped": skipped,
            "complete": made >= icons.COUNT}


@router.post("/designs/{design_id}/icons/{icon_id}/regenerate")
def regenerate_icon(design_id: int, icon_id: int, db: Session = Depends(get_db)):
    """한 아이콘만 다시 그린다. 스타일은 세트와 동일하게 코드가 강제한다."""
    d = _design(db, design_id)
    a = db.get(IconAsset, icon_id)
    if not a or a.brand_version_id != d.brand_version_id:
        raise HTTPException(404, "아이콘을 찾을 수 없습니다")
    bv = db.get(BrandVersion, a.brand_version_id)
    brand = db.get(Brand, d.brand_id)
    src = db.get(Design, a.design_id) or d      # 세트를 만든 캠페인의 설명 기준으로 다시 그린다
    style, (w, h) = _style(bv), (a.width, a.height)
    try:
        els = icons.redraw_one(brand.name, src.brief, a.name, a.concept, style)
        _save(db, d, bv, a.batch_no, a.position, a.name, a.concept, els, style, w, h,
              bv.forbidden_phrases or [], existing=a)
    except Exception as e:
        raise HTTPException(502, f"아이콘 다시 그리기에 실패했습니다 ({type(e).__name__})")
    invalidate_reviews(db, d.id)
    db.commit()
    return _out(a)


@router.get("/icons/{icon_id}/svg")
def icon_svg(icon_id: int, db: Session = Depends(get_db)):
    a = db.get(IconAsset, icon_id)
    if not a:
        raise HTTPException(404, "아이콘을 찾을 수 없습니다")
    return Response(a.svg, media_type="image/svg+xml")


@router.get("/icons/{icon_id}/png")
def icon_png(icon_id: int, db: Session = Depends(get_db)):
    a = db.get(IconAsset, icon_id)
    if not a or not Path(a.png_path).exists():
        raise HTTPException(404, "아이콘을 찾을 수 없습니다")
    return FileResponse(a.png_path, media_type="image/png")
