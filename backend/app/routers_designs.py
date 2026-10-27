from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import phrases
from .models import Asset, Brand, BrandVersion, Design, PresetVersion
from .routers_assets import get_db
from .routers_review import ensure_unlocked, invalidate_reviews

router = APIRouter(prefix="/api")

KINDS = ("광고 이미지", "아이콘 세트")
ICON_PURPOSES = ("웹용", "사용자 지정")


class BriefIn(BaseModel):
    product_name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    key_message: str = Field(default="", max_length=200)
    target: str = Field(default="", max_length=300)
    product_image_asset_id: int | None = None      # 제품 이미지(선택)
    image_style: str = "자동"                       # 배경 형태: 자동 | imagegen.STYLES 의 이름
    product_scale: str = "크게"                     # 제품 크기(투명 누끼 합성): compose.PRODUCT_SCALES 의 이름
    layout: str = "기본"                            # 레이아웃(배치): compose.LAYOUTS 의 이름. 배경 구도·제품 구역·문구 자리가 따른다
    image_model: str | None = None                  # 생성 설정: 이미지 모델(imagegen.MODELS). None 이면 기존 값 유지/기본
    bg_mode: str | None = None                      # 생성 설정: 배경 방식 per_ratio | single. None 이면 기존 값 유지/기본
    # 캠페인 필수·금지 표현. 비어 있으면 브랜드 규칙 값을 쓴다 (phrases.effective)
    required_phrases: list[str] = Field(default=[], max_length=50)
    forbidden_phrases: list[str] = Field(default=[], max_length=50)

    @field_validator("required_phrases", "forbidden_phrases")
    @classmethod
    def _phr(cls, v: list[str]) -> list[str]:
        from .phrases import clean
        out = clean(v)
        if any(len(s) > 100 for s in out):
            raise ValueError("표현은 100자 이내")
        return out

    @model_validator(mode="after")
    def _no_overlap(self):
        both = set(self.required_phrases) & set(self.forbidden_phrases)
        if both:
            raise ValueError(f"필수 표현과 금지 표현에 같은 문구가 있습니다: {', '.join(sorted(both))}")
        return self

    @field_validator("image_model")
    @classmethod
    def _model(cls, v: str | None) -> str | None:
        from .imagegen import MODELS
        if v is not None and v not in MODELS:
            raise ValueError(f"이미지 모델은 {MODELS} 중 하나")
        return v

    @field_validator("bg_mode")
    @classmethod
    def _mode(cls, v: str | None) -> str | None:
        from .imagegen import BG_MODES
        if v is not None and v not in BG_MODES:
            raise ValueError(f"배경 방식은 {list(BG_MODES)} 중 하나")
        return v

    @field_validator("product_scale")
    @classmethod
    def _scale(cls, v: str) -> str:
        from .compose import PRODUCT_SCALES
        if v not in PRODUCT_SCALES:
            raise ValueError(f"제품 크기는 {list(PRODUCT_SCALES)} 중 하나")
        return v

    @field_validator("layout")
    @classmethod
    def _layout(cls, v: str) -> str:
        from .compose import LAYOUTS
        if v not in LAYOUTS:
            raise ValueError(f"레이아웃은 {list(LAYOUTS)} 중 하나")
        return v

    @field_validator("image_style")
    @classmethod
    def _style(cls, v: str) -> str:
        from .imagegen import AUTO_STYLE, STYLES
        v = (v or AUTO_STYLE).strip()
        if v != AUTO_STYLE and v not in STYLES:
            raise ValueError(f"배경 형태는 {[AUTO_STYLE, *STYLES]} 중 하나")
        return v


class IconIn(BaseModel):
    purpose: str
    width: int | None = Field(default=None, ge=16, le=4096)
    height: int | None = Field(default=None, ge=16, le=4096)

    @field_validator("purpose")
    @classmethod
    def _p(cls, v: str) -> str:
        if v not in ICON_PURPOSES:
            raise ValueError(f"용도는 {ICON_PURPOSES} 중 하나")
        return v

    @model_validator(mode="after")
    def _size(self):
        # 웹용 기본 크기는 아직 정해지지 않았다 (6-4에서 확정). 사용자 지정은 크기가 필수.
        if self.purpose == "사용자 지정" and not (self.width and self.height):
            raise ValueError("사용자 지정은 가로·세로 크기를 입력해야 합니다")
        return self


class DesignIn(BaseModel):
    brand_id: int
    kind: str
    brief: BriefIn
    preset_version_ids: list[int] = []
    icon: IconIn | None = None

    @field_validator("kind")
    @classmethod
    def _k(cls, v: str) -> str:
        if v not in KINDS:
            raise ValueError(f"시안 종류는 {KINDS} 중 하나")
        return v


def _check_product_image(asset_id: int | None, db: Session) -> None:
    if asset_id is None:
        return
    a = db.get(Asset, asset_id)
    if not a or a.kind != "product":
        raise HTTPException(400, "제품 이미지 파일이 아닙니다")


def _check_presets(ids: list[int], db: Session) -> None:
    for pid in ids:
        v = db.get(PresetVersion, pid)
        if not v or not v.active:
            raise HTTPException(400, f"선택할 수 없는 규격입니다: {pid}")
        if v.status == "확인 필요":
            raise HTTPException(400, f"공식 규격이 확인되지 않아 선택할 수 없습니다: {v.placement.name}")


class DesignPatch(BaseModel):
    brief: BriefIn | None = None                  # None 이면 입력은 그대로 둔다
    preset_version_ids: list[int] | None = None   # None 이면 규격은 그대로 둔다
    brand_id: int | None = None                   # 지정하면 그 브랜드의 '현재(최신) 버전'으로 맞춘다


def _out(d: Design, db: Session) -> dict:
    brand = db.get(Brand, d.brand_id)
    bv = db.get(BrandVersion, d.brand_version_id)
    cur = db.get(BrandVersion, brand.current_version_id) if brand and brand.current_version_id else None
    presets = []
    for pid in d.preset_version_ids:
        v = db.get(PresetVersion, pid)
        if v:
            from .routers_images import in_image_for
            presets.append({"id": v.id, "platform": v.placement.platform.name, "name": v.placement.name,
                            "width": v.production_width, "height": v.production_height, "status": v.status,
                            "in_image": in_image_for(d, v)})
    from .copyfields import asset_spec
    pvs = [db.get(PresetVersion, pid) for pid in d.preset_version_ids]
    return {
        "id": d.id, "kind": d.kind, "status": d.status, "brief": d.brief, "presets": presets,
        # 문구를 여러 개 요구하는 지면(PMax 등)의 필드 — 있으면 '추가 문구 자산 생성' 버튼을 보인다
        "text_asset_fields": [a["field"] for a in asset_spec([v for v in pvs if v])],
        # 실제 적용되는 필수·금지 표현(캠페인 입력 우선, 비면 브랜드 규칙)과 출처
        "effective_phrases": phrases.effective(bv, d.brief) if bv else None,
        "brand": {"id": d.brand_id, "name": brand.name if brand else None,
                  "version_no": bv.version_no if bv else None,
                  "current_version_no": cur.version_no if cur else None},
        # 브랜드가 새 버전으로 바뀌어 이 시안이 이전 버전 기준이면 표시
        "brand_outdated": bool(bv and cur and cur.id != bv.id),
        "created_at": d.created_at.isoformat(),
    }


@router.post("/designs")
def create_design(body: DesignIn, db: Session = Depends(get_db)):
    brand = db.get(Brand, body.brand_id)
    if not brand or not brand.current_version_id:
        raise HTTPException(400, "브랜드를 찾을 수 없습니다")
    if body.kind != "광고 이미지":
        raise HTTPException(400, "캠페인은 광고 이미지를 만드는 용도입니다. 아이콘 세트는 광고 이미지 생성 후 브랜드 규칙 버전마다 한 세트씩 만들 수 있습니다")
    ids = list(dict.fromkeys(body.preset_version_ids))
    if body.kind == "광고 이미지":
        _check_presets(ids, db)      # 규격은 비어 있어도 된다(캠페인 안에서 나중에 선택)
        if body.icon:
            raise HTTPException(400, "광고 이미지 시안에는 아이콘 용도를 지정하지 않습니다")
    else:
        if ids:
            raise HTTPException(400, "아이콘 세트는 광고 규격이 아니라 용도를 선택합니다")
        if not body.icon:
            raise HTTPException(400, "아이콘 세트는 용도(웹용 / 사용자 지정)를 선택해야 합니다")
    _check_product_image(body.brief.product_image_asset_id, db)
    brief = body.brief.model_dump()
    if body.icon:
        brief["icon"] = body.icon.model_dump()
    d = Design(brand_id=brand.id, brand_version_id=brand.current_version_id,
               preset_version_ids=ids, brief=brief, kind=body.kind, status="미검수")
    db.add(d)
    db.commit()
    return _out(d, db)


@router.get("/designs")
def list_designs(brand_id: int | None = None, db: Session = Depends(get_db)):
    q = select(Design).order_by(Design.id.desc())
    if brand_id is not None:
        q = q.where(Design.brand_id == brand_id)
    return [_out(d, db) for d in db.scalars(q).all()]


@router.get("/designs/{design_id}")
def get_design(design_id: int, db: Session = Depends(get_db)):
    d = db.get(Design, design_id)
    if not d:
        raise HTTPException(404, "시안을 찾을 수 없습니다")
    return _out(d, db)


@router.get("/designs/{design_id}/quiz")
def design_quiz(design_id: int, db: Session = Depends(get_db)):
    """생성 대기 화면의 브랜드 퀴즈(비용 없음, 모델 호출 없음). 문항은 quiz.build."""
    from . import quiz
    d = db.get(Design, design_id)
    if not d:
        raise HTTPException(404, "시안을 찾을 수 없습니다")
    bv, brand = db.get(BrandVersion, d.brand_version_id), db.get(Brand, d.brand_id)
    return {"items": quiz.build(brand.name if brand else "브랜드", bv, d.brief) if bv else []}


@router.patch("/designs/{design_id}")
def update_design(design_id: int, body: DesignPatch, db: Session = Depends(get_db)):
    """입력(메시지)·규격 수정. 바뀌면 기존 검수 결과는 무효화된다. 검수 완료된 캠페인은 409."""
    d = db.get(Design, design_id)
    if not d:
        raise HTTPException(404, "시안을 찾을 수 없습니다")
    ensure_unlocked(d)
    if body.brief is not None:
        _check_product_image(body.brief.product_image_asset_id, db)
        dump = body.brief.model_dump()
        for k in ("image_model", "bg_mode"):           # 입력 폼이 보내지 않는 생성 설정은 기존 값을 지우지 않는다
            if dump.get(k) is None:
                dump.pop(k, None)
        d.brief = {**(d.brief or {}), **dump}      # icon 같은 기존 키는 보존
    if body.brand_id is not None:
        # 같은 브랜드면 최신 규칙 버전 적용, 다른 브랜드면 브랜드 변경
        brand = db.get(Brand, body.brand_id)
        if not brand or not brand.current_version_id:
            raise HTTPException(400, "브랜드를 찾을 수 없습니다")
        d.brand_id, d.brand_version_id = brand.id, brand.current_version_id
    if body.preset_version_ids is not None:
        if d.kind != "광고 이미지":
            raise HTTPException(400, "아이콘 세트에는 광고 규격을 지정하지 않습니다")
        ids = list(dict.fromkeys(body.preset_version_ids))
        _check_presets(ids, db)
        d.preset_version_ids = ids
    invalidate_reviews(db, d.id)
    db.commit()
    return _out(d, db)
