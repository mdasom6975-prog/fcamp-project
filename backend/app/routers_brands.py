from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import smallbiz
from .models import Asset, Brand, BrandVersion
from .routers_assets import PROVIDED_FONTS, get_db
from .schemas import DEFAULT_COLOR_ROLES, DEFAULT_LEVELS, BrandIn

router = APIRouter(prefix="/api")


def _clean(items: list[str]) -> list[str]:
    seen, out = set(), []
    for s in items:
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _check_refs(b: BrandIn, db: Session) -> None:
    if b.logo_asset_id is not None:
        a = db.get(Asset, b.logo_asset_id)
        if not a or a.kind != "logo":
            raise HTTPException(400, "logo_asset_id 가 로고 파일이 아닙니다")
    if b.logo_square_asset_id is not None:
        a = db.get(Asset, b.logo_square_asset_id)
        if not a or a.kind != "logo":
            raise HTTPException(400, "logo_square_asset_id 가 로고 파일이 아닙니다")
        if a.width and a.height and (abs(a.width / a.height - 1) > 0.05 or min(a.width, a.height) < 128):
            raise HTTPException(400, f"정사각 로고는 1:1 이고 128×128 이상이어야 합니다 (현재 {a.width}×{a.height})")
    provided = {f["name"] for f in PROVIDED_FONTS}
    for f in b.fonts:
        if f.source == "provided" and f.name not in provided:
            raise HTTPException(400, f"제공 목록에 없는 폰트: {f.name}")
        if f.source == "upload":
            a = db.get(Asset, f.asset_id) if f.asset_id else None
            if not a or a.kind != "font":
                raise HTTPException(400, "업로드한 폰트 파일이 아닙니다")
    for rid in b.image_tone.reference_asset_ids:
        a = db.get(Asset, rid)
        if not a or a.kind != "reference":
            raise HTTPException(400, f"기준 이미지가 아닙니다: {rid}")


def _version_from(b: BrandIn, brand_id: int, version_no: int) -> BrandVersion:
    levels = {**DEFAULT_LEVELS, **b.levels}
    return BrandVersion(
        brand_id=brand_id, version_no=version_no,
        logo_asset_id=b.logo_asset_id,
        logo_square_asset_id=b.logo_square_asset_id,
        logo_margin=({"percent": b.logo_margin_percent} if b.logo_margin_percent is not None else None),
        logo_position=b.logo_position,
        levels=levels,
        colors=[c.model_dump() for c in b.colors],
        fonts=[f.model_dump() for f in b.fonts],
        icon_style=b.icon_style.model_dump() if b.icon_style else {},
        image_tone=b.image_tone.model_dump(),
        voice={"description": b.voice.description,
               "headline_max_chars": b.voice.headline_max_chars, "polite": b.voice.polite},
        required_phrases=_clean(b.required_phrases),
        forbidden_phrases=_clean(b.forbidden_phrases),
    )


def _warnings(b: BrandIn) -> list[str]:
    w = []
    both = set(_clean(b.required_phrases)) & set(_clean(b.forbidden_phrases))
    if both:
        w.append(f"필수 표현과 금지 표현에 같은 문구가 있습니다: {', '.join(sorted(both))}")
    roles = [c.role for c in b.colors]
    if len(roles) != len(set(roles)):
        w.append("같은 역할 이름의 색상이 중복되어 있습니다")
    return w


def _version_out(v: BrandVersion) -> dict:
    return {
        "id": v.id, "version_no": v.version_no, "created_at": v.created_at.isoformat(),
        "logo_asset_id": v.logo_asset_id, "logo_square_asset_id": v.logo_square_asset_id,
        "logo_margin": v.logo_margin, "logo_position": v.logo_position, "levels": v.levels,
        "colors": v.colors, "fonts": v.fonts, "icon_style": v.icon_style,
        "image_tone": v.image_tone, "voice": v.voice,
        "required_phrases": v.required_phrases, "forbidden_phrases": v.forbidden_phrases,
    }


@router.get("/brand-defaults")
def brand_defaults():
    return {"levels": DEFAULT_LEVELS, "color_roles": DEFAULT_COLOR_ROLES}


@router.get("/brands")
def list_brands(db: Session = Depends(get_db)):
    brands = [b for b in db.scalars(select(Brand).order_by(Brand.id)).all()
              if not b.name.startswith(smallbiz.BRAND_PREFIX)]     # 소상공인 스타일 프리셋의 숨은 브랜드는 목록에서 뺀다
    out = []
    for b in brands:
        v = db.get(BrandVersion, b.current_version_id) if b.current_version_id else None
        out.append({"id": b.id, "name": b.name, "description": b.description,
                    "current_version_id": b.current_version_id,
                    "current_version_no": v.version_no if v else None,
                    "created_at": b.created_at.isoformat()})
    return out


@router.post("/brands")
def create_brand(body: BrandIn, db: Session = Depends(get_db)):
    _check_refs(body, db)
    brand = Brand(name=body.name.strip(), description=body.description.strip())
    db.add(brand)
    db.flush()
    v = _version_from(body, brand.id, 1)
    db.add(v)
    db.flush()
    brand.current_version_id = v.id
    db.commit()
    return {"id": brand.id, "name": brand.name, "description": brand.description, "version": _version_out(v), "warnings": _warnings(body)}


@router.get("/brands/{brand_id}")
def get_brand(brand_id: int, version_id: int | None = None, db: Session = Depends(get_db)):
    brand = db.get(Brand, brand_id)
    if not brand:
        raise HTTPException(404, "브랜드를 찾을 수 없습니다")
    versions = db.scalars(select(BrandVersion).where(BrandVersion.brand_id == brand_id)
                          .order_by(BrandVersion.version_no)).all()
    cur = next((v for v in versions if v.id == (version_id or brand.current_version_id)), None)
    if not cur:
        raise HTTPException(404, "버전을 찾을 수 없습니다")
    return {"id": brand.id, "name": brand.name, "description": brand.description,
            "current_version_id": brand.current_version_id,
            "version": _version_out(cur),
            "versions": [{"id": v.id, "version_no": v.version_no,
                          "created_at": v.created_at.isoformat()} for v in versions]}


@router.put("/brands/{brand_id}")
def update_brand(brand_id: int, body: BrandIn, db: Session = Depends(get_db)):
    """수정은 기존 버전을 덮어쓰지 않고 새 버전을 만든다."""
    brand = db.get(Brand, brand_id)
    if not brand:
        raise HTTPException(404, "브랜드를 찾을 수 없습니다")
    _check_refs(body, db)
    last = db.scalar(select(BrandVersion.version_no).where(BrandVersion.brand_id == brand_id)
                     .order_by(BrandVersion.version_no.desc()).limit(1)) or 0
    brand.name = body.name.strip()
    brand.description = body.description.strip()
    v = _version_from(body, brand_id, last + 1)
    db.add(v)
    db.flush()
    brand.current_version_id = v.id
    db.commit()
    return {"id": brand.id, "name": brand.name, "description": brand.description, "version": _version_out(v), "warnings": _warnings(body)}
