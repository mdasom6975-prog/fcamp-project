"""피드 세트 생성기(2026-11 개편, docs/12 §7 B): 소상공인 화장품 브랜드가 제품 사진 한 장으로 가상 모델 피드 이미지 3장과 캡션을 얻는다.
P1 — 브랜드와 브랜드 전속 가상 모델(남·여 각 1명, 후보 중 선택해 저장). 이전 버전(routers_smallbiz)은 고치지 않는다."""
import concurrent.futures as cf
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import calllog, imagegen, prompts, smallbiz, storage
from .models import Asset, Brand, FeedBrand, VirtualModel
from .routers_assets import get_db
from .routers_brands import _version_from

router = APIRouter(prefix="/api/feed", dependencies=[Depends(calllog.track_design)])

GENDERS = ("여", "남")
GENDER_WORD = {"여": "여성", "남": "남성"}
CANDIDATES = 4
FACE_SIZE = (1024, 1024)


class BrandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    tone: str = Field(min_length=1, max_length=40)       # 화장품 톤 프리셋 id


def _tones() -> dict[str, dict]:
    return {p["id"]: p for p in smallbiz.for_industry("화장품")}


def _model_out(m: VirtualModel) -> dict:
    return {"id": m.id, "gender": m.gender, "asset_id": m.asset_id, "appearance": m.appearance, "status": m.status,
            "url": f"/api/assets/{m.asset_id}/file"}


def _brand_out(db: Session, fb: FeedBrand) -> dict:
    b = db.get(Brand, fb.brand_id)
    ms = db.scalars(select(VirtualModel).where(VirtualModel.brand_id == fb.brand_id).order_by(VirtualModel.id)).all()
    return {"id": fb.id, "brand_id": fb.brand_id, "name": b.name if b else "", "tone": fb.tone,
            "models": {g: next((_model_out(m) for m in ms if m.gender == g and m.status == "선택"), None) for g in GENDERS},
            "candidates": [_model_out(m) for m in ms if m.status == "후보"]}


def _feed_brand(db: Session, feed_brand_id: int) -> FeedBrand:
    fb = db.get(FeedBrand, feed_brand_id)
    if fb is None:
        raise HTTPException(404, "브랜드를 찾을 수 없습니다")
    return fb


@router.get("/tones")
def tones():
    return {"tones": [smallbiz.public(p) for p in _tones().values()]}


@router.post("/brands")
def create_brand(body: BrandCreate, db: Session = Depends(get_db)):
    """이름과 톤 프리셋으로 브랜드를 만든다. 규칙(색·어조·금지어)은 톤 프리셋에서 가져와 기존 brands/brand_versions 에 저장한다."""
    tone = _tones().get(body.tone)
    if tone is None:
        raise HTTPException(400, "알 수 없는 톤입니다")
    b_in = smallbiz.brand_in(tone).model_copy(update={"name": body.name.strip()})
    brand = Brand(name=b_in.name, description=b_in.description)
    db.add(brand)
    db.flush()
    v = _version_from(b_in, brand.id, 1)
    db.add(v)
    db.flush()
    brand.current_version_id = v.id
    fb = FeedBrand(brand_id=brand.id, tone=body.tone)
    db.add(fb)
    db.commit()
    return _brand_out(db, fb)


@router.get("/brands")
def list_brands(db: Session = Depends(get_db)):
    return {"brands": [_brand_out(db, fb) for fb in db.scalars(select(FeedBrand).order_by(FeedBrand.id.desc())).all()]}


@router.get("/brands/{feed_brand_id}")
def get_brand(feed_brand_id: int, db: Session = Depends(get_db)):
    return _brand_out(db, _feed_brand(db, feed_brand_id))


def appearances(gender: str) -> list[str]:
    meta = prompts.load("image/feed/model").meta
    return [a.strip() for a in str(meta.get(f"appearances_{gender}", "")).split("|") if a.strip()]


def model_prompt(gender: str, appearance: str, safe: bool = False, idx: int = 0) -> str:
    """후보 idx 번째의 옷은 meta wears 의 같은 순서(후보마다 다른 옷이 얼굴도 덜 비슷해 보이게 한다)."""
    meta = prompts.load("image/feed/model").meta
    wears = [w.strip() for w in str(meta.get("wears", "")).split("|") if w.strip()] or [meta["wear"]]
    return prompts.render("image/feed/model", gender=GENDER_WORD[gender], appearance=appearance,
                          wear=meta["wear_safe"] if safe else wears[idx % len(wears)])


def _blocked(e: Exception) -> bool:
    """결과물 안전 필터 거절(moderation_blocked)인가. 이 경우만 옷차림을 더 보수적으로 바꿔 한 번 더 만든다."""
    err = imagegen.error_info(e)
    return err.get("code") == "moderation_blocked" or "moderation_blocked" in str(e)


def make_face(gender: str, appearance: str, idx: int = 0):
    """기준 얼굴 1장. 안전 필터에 막히면 보수적인 옷차림으로 1회 다시."""
    try:
        return imagegen.generate_background(model_prompt(gender, appearance, idx=idx), *FACE_SIZE)
    except Exception as e:
        if not _blocked(e):
            raise
        return imagegen.generate_background(model_prompt(gender, appearance, safe=True), *FACE_SIZE)


@router.post("/brands/{feed_brand_id}/models")
def create_candidates(feed_brand_id: int, gender: str = Body(..., embed=True), db: Session = Depends(get_db)):
    """성별 하나의 후보 얼굴 CANDIDATES 장을 동시에 만든다. 이전 후보(같은 성별)는 지운다. 일부가 실패하면 성공한 것만 저장하고 실패 수를 알린다."""
    fb = _feed_brand(db, feed_brand_id)
    if gender not in GENDERS:
        raise HTTPException(400, "성별은 여 또는 남입니다")
    looks = appearances(gender)[:CANDIDATES]
    with cf.ThreadPoolExecutor(max_workers=len(looks)) as ex:
        futures = [ex.submit(make_face, gender, a, i) for i, a in enumerate(looks)]
        results = []
        for a, f in zip(looks, futures):
            try:
                results.append((a, f.result(), None))
            except Exception as e:
                results.append((a, None, type(e).__name__))
    ok = [(a, im) for a, im, err in results if im is not None]
    if not ok:
        raise HTTPException(502, {"message": "모델 사진을 만들지 못했습니다. 잠시 후 다시 시도해 주세요",
                                  "errors": [err for _, _, err in results]})
    for old in db.scalars(select(VirtualModel).where(VirtualModel.brand_id == fb.brand_id, VirtualModel.gender == gender,
                                                     VirtualModel.status == "후보")).all():
        db.delete(old)
    for a, im in ok:
        key = storage.put_image(f"face_{uuid.uuid4().hex[:10]}.png", im)
        asset = Asset(brand_id=fb.brand_id, kind="generated", file_path=key, mime="image/png", bytes=0, width=im.width, height=im.height)
        db.add(asset)
        db.flush()
        db.add(VirtualModel(brand_id=fb.brand_id, gender=gender, asset_id=asset.id, appearance=a, status="후보"))
    db.commit()
    return {**_brand_out(db, fb), "failed": len(results) - len(ok)}


@router.post("/models/{model_id}/select")
def select_model(model_id: int, db: Session = Depends(get_db)):
    """후보 하나를 이 브랜드의 해당 성별 모델로 정한다. 이전 선택은 후보로 돌리지 않고 지운다(얼굴이 하나로 유지되게)."""
    m = db.get(VirtualModel, model_id)
    if m is None:
        raise HTTPException(404, "모델을 찾을 수 없습니다")
    fb = db.scalar(select(FeedBrand).where(FeedBrand.brand_id == m.brand_id))
    for other in db.scalars(select(VirtualModel).where(VirtualModel.brand_id == m.brand_id, VirtualModel.gender == m.gender,
                                                       VirtualModel.id != m.id)).all():
        db.delete(other)
    m.status = "선택"
    db.commit()
    return _brand_out(db, fb)


def chosen_model(db: Session, brand_id: int, gender: str) -> VirtualModel | None:
    """게시물 만들기(P2)에서 쓸 브랜드의 선택된 모델."""
    return db.scalar(select(VirtualModel).where(VirtualModel.brand_id == brand_id, VirtualModel.gender == gender,
                                                VirtualModel.status == "선택"))
