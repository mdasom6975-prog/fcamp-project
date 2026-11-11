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


# ---------------- P2: 게시물 = 장면 3장 ----------------
import io  # noqa: E402

from PIL import Image  # noqa: E402

from . import compose, llm  # noqa: E402
from .models import FeedPost  # noqa: E402

SHOTS = ("hold", "use", "detail")
SHOT_LABEL = {"hold": "모델이 제품을 든 모습", "use": "모델이 제품을 사용하는 모습", "detail": "제품 상세"}
GEN_SIZE = (1024, 1536)
FEED_SIZE = (1080, 1350)            # 인스타그램 세로형 4:5
JUDGE_MIN = 2                       # 이 점수 이하인 핵심 항목이 있으면 1회 다시 만든다
JUDGE_ITEMS = {"hold": ["제품 일치", "작은 글자", "얼굴 일치", "손·신체", "새 글자 없음", "장면"],
               "use": ["제품 일치", "작은 글자", "얼굴 일치", "손·신체", "새 글자 없음", "장면"],
               "detail": ["새 글자 없음", "장면"]}
KEY_ITEMS = {"제품 일치", "작은 글자", "얼굴 일치", "손·신체", "새 글자 없음"}
FEED_JUDGE_SCHEMA = llm._obj({"scores": llm._arr(llm._obj({"item": llm.STR, "score": {"type": "integer"}, "reason": llm.STR})),
                              "overall": {"type": "integer"}, "summary": llm.STR})


class PostCreate(BaseModel):
    feed_brand_id: int
    photo_asset_id: int
    gender: str = Field(pattern="^(여|남)$")
    product_name: str = Field(min_length=1, max_length=100)
    category: str = Field(default="", max_length=30)
    label_claims: list[str] = Field(default=[], max_length=6)
    usage: str = Field(default="", max_length=100)       # 사용 방법 한 줄(선택). 있으면 사용 장면 동작으로 쓴다


def _action(category: str, usage: str) -> str:
    if usage.strip():
        return usage.strip()
    acts = dict(a.split("=", 1) for a in str(prompts.load("image/feed/use").meta.get("actions", "")).split(" | ") if "=" in a)
    return next((v for k, v in acts.items() if k != "기본" and k in (category or "")), acts.get("기본", "손바닥에 덜어 볼에 바르는"))


def _product_text(p: dict) -> str:
    return " · ".join(x for x in (p.get("name"), p.get("category")) if x)


def shot_prompt(shot: str, gender: str, product: dict, tone: str, safe: bool = False, slot: dict | None = None) -> str:
    """slot: detail 의 제품 자리(imagegen.product_slot_vars 결과 {slot, shape})."""
    meta = prompts.load("image/feed/model").meta
    wear = meta["wear_safe"] if safe else meta["wear"]
    extra = {"action": _action(product.get("category", ""), product.get("usage", ""))} if shot == "use" else {}
    if shot == "detail":
        extra = slot or {"slot": "가로 35~65%, 세로 35~66%", "shape": ""}
    return prompts.render(f"image/feed/{shot}", gender=GENDER_WORD[gender], wear=wear, product=_product_text(product),
                          tone=(tone.rstrip(".") + ".") if tone else "", **extra)


def detail_slot(product_img: Image.Image) -> tuple[dict, dict]:
    """상세컷의 제품 자리. 최종 4:5 캔버스에서 누끼가 놓일 상자(placed)를 정하고, 생성 캔버스(2:3, 위아래를 같은 만큼 잘라 4:5 로 만든다)
    기준으로 바꿔 프롬프트의 {slot, shape} 를 만든다. 반환 (placed, slot_vars). 배경 받침과 붙이는 위치가 같아야 공중에 뜨지 않는다."""
    FW, FH = FEED_SIZE
    GW, GH = GEN_SIZE
    placed = compose.product_box(FW, FH, product_img, compose.template_for(FW, FH))
    k = GW / FW
    oy = (GH - FH * k) / 2
    x0, y0, x1, y1 = placed["box"]
    gen = {**placed, "canvas": [GW, GH], "box": [round(x0 * k), round(y0 * k + oy), round(x1 * k), round(y1 * k + oy)]}
    return placed, imagegen.product_slot_vars(compose.template_for(FW, FH), gen)


def _png(im: Image.Image) -> bytes:
    im = im.convert("RGB")
    im.thumbnail((1024, 1024))
    b = io.BytesIO()
    im.save(b, format="PNG")
    return b.getvalue()


def judge_shot(shot: str, image: Image.Image, product_img: Image.Image, face: Image.Image | None, product: dict) -> dict | None:
    """결과를 원본 제품·기준 얼굴과 비교해 채점. 실패하면 None(생성을 막지 않는다)."""
    items = JUDGE_ITEMS[shot]
    imgs = [_png(image), _png(product_img)] + ([_png(face)] if face is not None and shot != "detail" else [])
    attach = "1번 결과 이미지, 2번 제품 원본" + (", 3번 모델 기준 얼굴" if len(imgs) == 3 else "")
    try:
        data = llm.vision_json(prompts.render("analysis/feed_judge_system"),
                               prompts.render("analysis/feed_judge_user", shot=SHOT_LABEL[shot], product=_product_text(product),
                                              attach=attach, items=", ".join(items)),
                               imgs, "judge", FEED_JUDGE_SCHEMA)
    except Exception:
        return None
    scores = {str(s.get("item")): int(s.get("score") or 0) for s in data.get("scores") or [] if s.get("item") in items}
    return {"scores": scores, "overall": int(data.get("overall") or 0), "summary": str(data.get("summary") or "")}


def _bad(j: dict | None) -> bool:
    return j is not None and any(v <= JUDGE_MIN for k, v in j["scores"].items() if k in KEY_ITEMS)


def _gen_with_retry(fn):
    """안전 필터 거절이면 보수적 옷차림으로 1회 다시(fn(safe) 를 부른다)."""
    try:
        return fn(False)
    except Exception as e:
        if not _blocked(e):
            raise
        return fn(True)


def render_shot(shot: str, gender: str, product: dict, tone: str, face: Image.Image | None,
                product_img: Image.Image) -> tuple[Image.Image, Image.Image]:
    """장면 1장. 반환 (최종 4:5 이미지, 심사할 이미지). detail 은 빈 배경을 만들고 원본 누끼를 붙인다.
    detail 도 붙인 결과를 심사한다(빈 배경을 보면 '제품이 없다'로 장면 점수가 늘 낮았다. 라벨 글자는 심사 프롬프트가 '새 글자'에서 뺀다)."""
    if shot == "detail":
        cut = compose.has_cutout(product_img)
        placed, slot = detail_slot(product_img) if cut else (None, None)
        bg = _gen_with_retry(lambda safe: imagegen.generate_background(shot_prompt(shot, gender, product, tone, safe, slot), *GEN_SIZE))
        bg = compose.cover_crop(bg, *FEED_SIZE, focus=False)      # 중앙 기준으로 잘라야 프롬프트에 준 자리와 맞는다
        if cut:
            box = seat_on_surface(bg, placed["box"])
            out = compose.paste_cutout(contact_shadow(bg, box), product_img, box)
            return out, out
        return bg, bg
    img = _gen_with_retry(lambda safe: imagegen.generate_variant(shot_prompt(shot, gender, product, tone, safe), *GEN_SIZE,
                                                                 face, product=product_img, kind=f"feed_{shot}"))
    out = compose.cover_crop(img, *FEED_SIZE, focus=False)     # 중앙 기준: 프롬프트가 위·아래 10%를 비우게 했다(내용 기준은 아래를 잘랐다)
    return out, out


def make_shot(shot: str, gender: str, product: dict, tone: str, face, product_img) -> dict:
    """생성 → 심사 → 미달이면 1회 다시 → 기준 통과 우선·overall 높은 쪽. 반환 {image, judge, tries, error}."""
    tries = []
    for _ in range(2):
        try:
            out, check = render_shot(shot, gender, product, tone, face, product_img)
        except HTTPException:
            raise
        except Exception as e:
            if not tries:
                return {"image": None, "judge": None, "tries": 1, "error": type(e).__name__}
            break
        j = judge_shot(shot, check, product_img, face, product)
        tries.append((out, j))
        if not _bad(j):
            break
    out, j = max(tries, key=lambda t: (not _bad(t[1]), (t[1] or {}).get("overall", 0)))
    return {"image": out, "judge": j, "tries": len(tries), "error": None}


SURFACE_BAND = (0.5, 0.92)   # 받침 모서리를 찾는 세로 범위(캔버스 대비)
SURFACE_SINK = 0.07          # 모서리보다 이만큼 아래(앞쪽)에 바닥을 둔다 — 모서리 가까이 두면 떠 보인다(0.035 실측: 여전히 뜸)


def seat_on_surface(bg: Image.Image, box: list[int]) -> list[int]:
    """생성 배경에서 받침의 뒤쪽 모서리(가운데 열들에서 밝기가 가로로 가장 크게 바뀌는 줄)를 찾아, 제품 바닥이 그 조금 앞에 닿게 상자를
    위아래로만 옮긴다. 이미지 모델이 프롬프트의 바닥 높이를 정확히 지키지 않아 제품이 떠 보였다(P2 실측). 뚜렷한 모서리가 없으면 그대로."""
    import numpy as np
    g = np.asarray(bg.convert("L"), dtype=np.float32)
    H, W = g.shape
    x0, x1 = int(W * 0.25), int(W * 0.75)
    y0, y1 = int(H * SURFACE_BAND[0]), int(H * SURFACE_BAND[1])
    diff = np.abs(np.diff(g[y0:y1, x0:x1], axis=0)).mean(axis=1)
    if diff.size < 5:
        return box
    smooth = np.convolve(diff, np.ones(5) / 5, mode="same")
    i = int(smooth.argmax())
    if smooth[i] < max(3.0, float(np.median(smooth)) * 3):     # 뚜렷한 가로 모서리가 없다
        return box
    edge = y0 + i
    bottom = min(int(H * 0.95), edge + int(H * SURFACE_SINK))
    dy = bottom - box[3]
    return [box[0], box[1] + dy, box[2], box[3] + dy]


def contact_shadow(bg: Image.Image, box: list[int]) -> Image.Image:
    """제품 바닥에 진한 접촉 그림자와, 왼쪽 위 빛(detail.md)의 반대쪽으로 길게 눕는 옅은 그림자를 깐다. 제품을 붙이기 전에 부른다.
    compose.paste_cutout 의 그림자는 밝은 받침에서 거의 보이지 않아 제품이 떠 보였다(P2 실측). 이전 버전과 같이 쓰는 compose 는 고치지 않는다."""
    from PIL import ImageDraw, ImageFilter
    x0, y0, x1, y1 = box
    pw, ph = x1 - x0, y1 - y0
    cx = (x0 + x1) // 2
    layer = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # 옅은 그림자: 바닥에서 오른쪽 뒤로 눕는 넓은 타원
    d.ellipse([cx - pw * 0.45, y1 - ph * 0.06, cx + pw * 0.85, y1 + ph * 0.03], fill=(40, 35, 30, 70))
    soft = layer.filter(ImageFilter.GaussianBlur(max(6, ph * 0.03)))
    # 접촉 그림자: 바닥 바로 아래 얇고 진한 띠
    tight = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    ImageDraw.Draw(tight).ellipse([cx - pw * 0.48, y1 - ph * 0.018, cx + pw * 0.48, y1 + ph * 0.014], fill=(25, 20, 15, 190))
    tight = tight.filter(ImageFilter.GaussianBlur(max(2, ph * 0.008)))
    out = Image.alpha_composite(bg.convert("RGBA"), soft)
    return Image.alpha_composite(out, tight).convert("RGB")


MAIN_BODY_MIN = 0.3      # 가장 큰 덩어리 대비 이 비율보다 작은 떨어진 덩어리는 버린다


def main_body(cut: Image.Image) -> Image.Image:
    """누끼에서 제품 본체만 남긴다: 알파로 연결된 덩어리 중 가장 큰 것(과 그 30% 이상인 것)만 두고, 떨어져 있는 작은 덩어리
    (쇼핑몰 사진의 판촉 스티커·배지 등)는 지운다. 남은 것이 없으면 그대로. 상세컷 위치·그림자가 본체 기준이 되게 한다."""
    import numpy as np
    from scipy import ndimage
    rgba = cut.convert("RGBA")
    a = np.array(rgba.getchannel("A"))
    lab, n = ndimage.label(a > 8)
    if n <= 1:
        return rgba
    sizes = ndimage.sum(np.ones_like(a), lab, index=range(1, n + 1))
    keep = {i + 1 for i, v in enumerate(sizes) if v >= sizes.max() * MAIN_BODY_MIN}
    mask = np.isin(lab, list(keep))
    arr = np.array(rgba)
    arr[..., 3] = np.where(mask, arr[..., 3], 0)
    return Image.fromarray(arr, "RGBA")


def _post_out(db: Session, p: FeedPost) -> dict:
    return {"id": p.id, "feed_brand_id": p.feed_brand_id, "gender": p.gender, "product": p.product,
            "shots": {k: {**v, "url": f"/api/assets/{v['asset_id']}/file" if v.get("asset_id") else None} for k, v in (p.shots or {}).items()}}


def _inputs(db: Session, fb: FeedBrand, photo_asset_id: int, gender: str):
    from .models import Asset as A
    m = chosen_model(db, fb.brand_id, gender)
    if m is None:
        raise HTTPException(400, f"이 브랜드의 {GENDER_WORD[gender]} 모델을 먼저 골라 주세요")
    pa, fa = db.get(A, photo_asset_id), db.get(A, m.asset_id)
    if pa is None or not storage.exists(pa.file_path):
        raise HTTPException(400, "제품 사진을 찾을 수 없습니다. 다시 올려 주세요")
    product_img = Image.open(storage.local(pa.file_path))
    product_img.load()
    if compose.has_cutout(product_img):
        product_img = main_body(product_img)     # 판촉 스티커 같은 떨어진 덩어리 제거(참조·상세 합성 모두)
    face = Image.open(storage.local(fa.file_path)).convert("RGB") if fa and storage.exists(fa.file_path) else None
    tone = (_tones().get(fb.tone) or {}).get("tone", "")
    return product_img, face, tone


def _save_shot(db: Session, fb: FeedBrand, r: dict) -> dict:
    if r["image"] is None:
        return {"asset_id": None, "judge": None, "tries": r["tries"], "error": r["error"]}
    key = storage.put_image(f"feed_{uuid.uuid4().hex[:10]}.png", r["image"])
    a = Asset(brand_id=fb.brand_id, kind="generated", file_path=key, mime="image/png", bytes=0, width=r["image"].width, height=r["image"].height)
    db.add(a)
    db.flush()
    return {"asset_id": a.id, "judge": r["judge"], "tries": r["tries"], "error": None}


@router.post("/posts")
def create_post(body: PostCreate, db: Session = Depends(get_db)):
    """제품 사진과 고른 모델로 장면 3장을 동시에 만들고 각각 심사한다(미달이면 그 장만 1회 다시)."""
    fb = _feed_brand(db, body.feed_brand_id)
    product_img, face, tone = _inputs(db, fb, body.photo_asset_id, body.gender)
    product = {"name": body.product_name.strip(), "category": body.category.strip(),
               "label_claims": [c.strip() for c in body.label_claims if c.strip()], "usage": body.usage.strip()}
    with cf.ThreadPoolExecutor(max_workers=len(SHOTS)) as ex:
        futs = {s: ex.submit(make_shot, s, body.gender, product, tone, face, product_img) for s in SHOTS}
        results = {s: f.result() for s, f in futs.items()}
    if all(r["image"] is None for r in results.values()):
        raise HTTPException(502, {"message": "이미지를 만들지 못했습니다. 잠시 후 다시 시도해 주세요",
                                  "errors": {s: r["error"] for s, r in results.items()}})
    p = FeedPost(feed_brand_id=fb.id, photo_asset_id=body.photo_asset_id, gender=body.gender, product=product,
                 shots={s: _save_shot(db, fb, r) for s, r in results.items()})
    db.add(p)
    db.commit()
    return _post_out(db, p)


@router.get("/posts/{post_id}")
def get_post(post_id: int, db: Session = Depends(get_db)):
    p = db.get(FeedPost, post_id)
    if p is None:
        raise HTTPException(404, "게시물을 찾을 수 없습니다")
    return _post_out(db, p)


@router.post("/posts/{post_id}/shots/{shot}/regenerate")
def regenerate_shot(post_id: int, shot: str, db: Session = Depends(get_db)):
    p = db.get(FeedPost, post_id)
    if p is None:
        raise HTTPException(404, "게시물을 찾을 수 없습니다")
    if shot not in SHOTS:
        raise HTTPException(400, "장면은 hold·use·detail 중 하나입니다")
    fb = _feed_brand(db, p.feed_brand_id)
    product_img, face, tone = _inputs(db, fb, p.photo_asset_id, p.gender)
    r = make_shot(shot, p.gender, p.product, tone, face, product_img)
    if r["image"] is None:
        raise HTTPException(502, {"message": "이미지를 만들지 못했습니다", "error": r["error"]})
    p.shots = {**(p.shots or {}), shot: _save_shot(db, fb, r)}
    db.commit()
    return _post_out(db, p)
