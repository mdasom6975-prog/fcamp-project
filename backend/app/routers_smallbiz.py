"""소상공인용 광고 이미지: 입력 최소화(품목 글·사진·스타일 하나)로 캠페인을 만들고 카피·이미지를 이어서 만든다.
기존 카피·이미지·내보내기 엔드포인트를 그대로 재사용하고, 여기서는 스타일 프리셋 → 숨은 브랜드, 입력 → 캠페인 변환만 한다."""
import io
import uuid

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from PIL import Image, ImageFilter, ImageOps, ImageStat
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import calllog, cutout, imagegen, llm, pricing, smallbiz, storage
from .models import Asset, Brand, BrandVersion, CopyVariant, Design, Placement, PresetVersion
from .routers_assets import get_db
from .routers_brands import _version_from

router = APIRouter(prefix="/api/smallbiz", dependencies=[Depends(calllog.track_design)])

PHOTO_MAX_BYTES = 15 * 1024 * 1024
PHOTO_LONG_SIDE = 1280         # 업로드 사진은 서버에서 이 크기로 줄여 저장한다 (스마트폰 사진은 보통 수 MB)


class CampaignIn(BaseModel):
    what: str = Field(min_length=1, max_length=1000)        # 홍보할 품목·물품 (자유 입력)
    message: str = Field(default="", max_length=200)        # 한마디(선택). 비우면 AI 가 제안
    shop_name: str = Field(default="", max_length=60)       # 가게 이름(선택)
    photo_asset_id: int | None = None
    preset_id: str | None = None                            # 비우면 추천 1순위
    placements: list[str] | None = None                     # 비우면 기본 규격


@router.get("/presets")
def list_presets():
    return {"presets": [smallbiz.public(p) for p in smallbiz.PRESETS], "default_placements": smallbiz.DEFAULT_PLACEMENTS,
            "common_forbidden": smallbiz.COMMON_FORBIDDEN}


def _image_price(model: str, size: str) -> float | None:
    usd, _ = pricing.cost_of({"ok": True, "api": "images.generate", "model": model, "quality": imagegen.OPENAI_QUALITY,
                              "size": size, "provider": imagegen.PROVIDER})
    return usd


@router.get("/pricing")
def pricing_info(db: Session = Depends(get_db)):
    """화면에 보여 줄 호출 계획과 가격표. 지면 규격에서 배경 호출 수를 계산하고, 단가는 pricing.py(추정치) 기준이다.
    이미지 단가는 토큰 과금이라 실제 값은 호출 기록(/designs/{id}/api-calls)이 정확하고, 여기 값은 장당 표 기준 추정이다."""
    model, chat_model = imagegen.OPENAI_MODEL, llm.chat_model("copy")
    sizes = {}
    for pid in _placement_ids(db, smallbiz.DEFAULT_PLACEMENTS):
        pv = db.get(PresetVersion, pid)
        sizes[pid] = (pv.production_width, pv.production_height)
    from .compose import template_for
    groups = sorted({template_for(w, h) for w, h in sizes.values()})
    px = {t: f"{imagegen.TEMPLATE_SIZES[t][0]}x{imagegen.TEMPLATE_SIZES[t][1]}" for t in groups}
    per_image = {t: _image_price(model, px[t]) for t in groups}
    img_total = sum(v or 0 for v in per_image.values())
    chat_rates, _ = pricing._lookup(pricing.table()["chat"], chat_model)
    return {
        "usd_krw": pricing.USD_KRW, "image_model": model, "image_quality": imagegen.OPENAI_QUALITY, "chat_model": chat_model,
        "image_prices": [{"group": t, "size": px[t], "usd": per_image[t], "krw": pricing.krw(per_image[t])} for t in groups],
        "chat_rates_per_million": chat_rates,
        "plan": [{"step": "한마디 제안", "kind": "LLM", "calls": "0~1", "note": "한마디를 비웠을 때만"},
                 {"step": "카피 3안", "kind": "LLM", "calls": "1", "note": "규격이 여러 개여도 캠페인당 1회"},
                 {"step": "배경 이미지", "kind": "이미지", "calls": str(len(groups)),
                  "note": f"비율군({', '.join(groups)})마다 1장, 같은 비율 지면은 공유"},
                 {"step": "글자·로고 합성·내려받기", "kind": "AI 없음", "calls": "0", "note": "Pillow 로 처리"}],
        "image_total_usd": round(img_total, 4), "image_total_krw": pricing.krw(img_total),
        "restyle": {"calls": len(groups), "usd": round(img_total, 4), "krw": pricing.krw(img_total),
                    "note": "스타일 바꾸기·배경 다시 만들기 1회당 (카피는 다시 부르지 않음)"},
    }


@router.post("/recommend")
def recommend(text: str = Body(..., embed=True)):
    return {"preset_ids": smallbiz.recommend(text)}


def _hidden_brand(db: Session, preset: dict) -> Brand:
    """프리셋의 숨은 브랜드(처음이면 만들고, 프리셋 정의가 바뀌었으면 새 버전을 추가한다)."""
    b_in = smallbiz.brand_in(preset)
    brand = db.scalar(select(Brand).where(Brand.name == b_in.name))
    if brand is None:
        brand = Brand(name=b_in.name, description=b_in.description)
        db.add(brand)
        db.flush()
        v = _version_from(b_in, brand.id, 1)
        db.add(v)
        db.flush()
        brand.current_version_id = v.id
        return brand
    cur = db.get(BrandVersion, brand.current_version_id)
    new = _version_from(b_in, brand.id, (cur.version_no if cur else 0) + 1)
    same = cur is not None and all(getattr(cur, k) == getattr(new, k) for k in
                                   ("colors", "fonts", "image_tone", "voice", "forbidden_phrases", "required_phrases", "levels"))
    if not same:
        db.add(new)
        db.flush()
        brand.current_version_id = new.id
    return brand


def _placement_ids(db: Session, names: list[str]) -> list[int]:
    ids: list[int] = []
    for n in names:
        pv = db.scalars(select(PresetVersion).join(Placement).where(
            Placement.name == n, PresetVersion.active.is_(True), PresetVersion.status != "확인 필요",
            PresetVersion.production_width.is_not(None)).order_by(PresetVersion.version_no.desc())).first()
        if pv is not None and pv.id not in ids:
            ids.append(pv.id)
    return ids


def _check_photo(im: Image.Image) -> list[str]:
    """사진 품질 안내(막지는 않는다): 해상도·흐림·밝기."""
    warns = []
    if min(im.size) < 400:
        warns.append(f"사진 해상도가 낮아요 ({im.width}×{im.height}). 더 큰 사진을 쓰면 결과가 좋아집니다")
    g = im.convert("L")
    g.thumbnail((512, 512))
    sharp = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).var[0]      # 가장자리 응답의 분산이 작으면 흐린 사진
    if sharp < 40:
        warns.append("사진이 흐리게 보여요. 초점이 맞은 사진으로 다시 올려 주세요")
    mean = ImageStat.Stat(g).mean[0]
    if mean < 45:
        warns.append("사진이 너무 어두워요. 밝은 곳에서 다시 찍어 주세요")
    elif mean > 235:
        warns.append("사진이 너무 밝아요. 빛이 덜 센 곳에서 다시 찍어 주세요")
    return warns


@router.post("/photo")
async def upload_photo(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """품목 사진 업로드. 서버에서 긴 변 1280px 로 줄여 제품 이미지 자산으로 저장하고 품질 안내를 함께 돌려준다."""
    data = await file.read()
    if len(data) > PHOTO_MAX_BYTES:
        raise HTTPException(400, "사진은 15MB 이하여야 합니다")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        im = ImageOps.exif_transpose(im)
    except Exception:
        raise HTTPException(400, "이미지 파일을 읽을 수 없습니다 (JPG·PNG·WEBP)")
    if min(im.size) < 64:
        raise HTTPException(400, "사진이 너무 작습니다")
    im.thumbnail((PHOTO_LONG_SIDE, PHOTO_LONG_SIDE), Image.LANCZOS)
    warns = _check_photo(im)
    alpha = im.mode in ("RGBA", "LA") or "transparency" in im.info
    buf = io.BytesIO()
    cut = None if alpha else cutout.cut(im)        # 배경을 지워 누끼로 저장하면 제품을 AI 가 다시 그리지 않고 원본 그대로 합성한다
    if alpha or cut is not None:
        (im.convert("RGBA") if alpha else cut).save(buf, format="PNG", optimize=True)
        ext, mime = "png", "image/png"
    else:
        im.convert("RGB").save(buf, format="JPEG", quality=88, optimize=True)
        ext, mime = "jpg", "image/jpeg"
    key = storage.put(f"product_{uuid.uuid4().hex[:10]}.{ext}", buf.getvalue(), mime)
    a = Asset(kind="product", file_path=key, mime=mime, bytes=buf.tell(), width=im.width, height=im.height)
    db.add(a)
    db.commit()
    return {"id": a.id, "width": a.width, "height": a.height, "warnings": warns, "cutout": cut is not None}


def _suggest_message(what: str) -> str:
    """한마디가 비었을 때 AI 가 짧게 제안한다. 가격·할인·기간은 지어내지 않는다. 실패하면 품목 글 앞부분을 쓴다."""
    system = ("너는 동네 가게 광고 카피라이터다. 사용자가 적은 품목 설명만 근거로 광고 한마디를 한국어 20자 이내로 쓴다. "
              "가격, 할인율, 기간, 수상·후기·최고·최저가 같은 사실 확인이 필요한 말은 쓰지 않는다.")
    try:
        data = llm.chat_json(system, f"품목: {what}", "copy", llm._obj({"message": {"type": "string"}}, ["message"]))
        msg = str(data.get("message") or "").strip()
        if msg:
            return msg[:200]
    except Exception:
        pass
    return what.strip().splitlines()[0][:40]


def _design(db: Session, design_id: int) -> Design:
    d = db.get(Design, design_id)
    if not d or d.kind != "광고 이미지":
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")
    return d


@router.post("/campaigns")
def create_campaign(body: CampaignIn, db: Session = Depends(get_db)):
    """품목 글(+사진)로 캠페인을 만들고 카피 3안을 생성해 1안을 선택해 둔다. 이미지는 /campaigns/{id}/images 로 이어서 만든다."""
    preset = smallbiz.get(body.preset_id) if body.preset_id else smallbiz.get(smallbiz.recommend(body.what)[0])
    if preset is None:
        raise HTTPException(400, "알 수 없는 스타일입니다")
    if body.photo_asset_id is not None:
        a = db.get(Asset, body.photo_asset_id)
        if not a or a.kind != "product":
            raise HTTPException(400, "사진을 찾을 수 없습니다. 다시 올려 주세요")
    pids = _placement_ids(db, body.placements or smallbiz.DEFAULT_PLACEMENTS)
    if not pids:
        raise HTTPException(400, "선택할 수 있는 규격이 없습니다")
    brand = _hidden_brand(db, preset)
    what = body.what.strip()
    desc = (f"가게: {body.shop_name.strip()}. " if body.shop_name.strip() else "") + what
    first = what.splitlines()[0].strip()
    brief = {"product_name": first[:100], "description": desc[:1000],
             "key_message": body.message.strip() or _suggest_message(what), "target": "",
             "product_image_asset_id": body.photo_asset_id, "image_style": preset["image_style"],
             "required_phrases": [], "forbidden_phrases": [], "smallbiz": {"preset_id": preset["id"]}}
    d = Design(brand_id=brand.id, brand_version_id=brand.current_version_id, preset_version_ids=pids, brief=brief,
               kind="광고 이미지", status="미검수")
    db.add(d)
    db.commit()
    from . import routers_copy
    out = routers_copy.create_copy(d.id, db)
    if out.get("failures"):
        raise HTTPException(502, {"message": "카피를 만들지 못했습니다. 잠시 후 다시 시도해 주세요", "design_id": d.id,
                                  "failures": out["failures"]})
    try:
        routers_copy.select_variant(d.id, 1, db)
    except HTTPException:
        raise HTTPException(502, {"message": "카피 시안이 비어 있습니다. 다시 시도해 주세요", "design_id": d.id})
    return {"design_id": d.id, "preset_id": preset["id"], "key_message": brief["key_message"]}


@router.get("/campaigns/{design_id}")
def get_campaign(design_id: int, db: Session = Depends(get_db)):
    """화면 복원용: 입력, 스타일, 규격별 합성 이미지(없으면 null)."""
    from . import routers_images
    d = _design(db, design_id)
    items = []
    for pid in d.preset_version_ids:
        pv = db.get(PresetVersion, pid)
        g = routers_images._latest(db, design_id, pid, "composite")
        items.append({"preset_id": pid, "name": pv.placement.name if pv else str(pid),
                      "image": routers_images._img_out(g)})
    b = d.brief or {}
    sel = db.scalars(select(CopyVariant).where(CopyVariant.design_id == d.id, CopyVariant.selected.is_(True))).first()
    from .routers_copy import with_legal
    legal_hits = [{k: v.get(k) for k in ("phrase", "reason", "law", "suggest", "grounded", "severity")}
                  for v in (with_legal(sel, d.brief) if sel else []) if v.get("type") == "legal"]
    return {"design_id": d.id, "what": b.get("description", ""), "legal": legal_hits, "key_message": b.get("key_message", ""),
            "preset_id": (b.get("smallbiz") or {}).get("preset_id"), "has_photo": bool(b.get("product_image_asset_id")),
            "items": items}


@router.post("/campaigns/{design_id}/images")
def make_images(design_id: int, new_background: bool = Body(False, embed=True), db: Session = Depends(get_db)):
    """모든 지면의 이미지를 만든다(첫 호출이 배경을 만들고 같은 비율은 공유). new_background=True 면 배경을 새로 만들고 다시 합성한다."""
    from . import routers_images
    d = _design(db, design_id)
    errors: list[dict] = []
    if new_background:
        try:
            routers_images.regenerate_background(design_id, style=None, db=db)
        except HTTPException as e:
            raise HTTPException(e.status_code, e.detail)
    for pid in list(d.preset_version_ids):
        try:
            routers_images.make_image(design_id, pid, regenerate_background=False, db=db)
        except HTTPException as e:
            errors.append({"preset_id": pid, "status": e.status_code, "message": str(e.detail)})
    return {"design_id": design_id, "errors": errors}


@router.put("/campaigns/{design_id}/style")
def change_style(design_id: int, preset_id: str = Body(..., embed=True), db: Session = Depends(get_db)):
    """'다른 스타일로': 캠페인의 숨은 브랜드를 다른 프리셋으로 바꾼다(입력·카피는 유지). 이어서 /images 를 new_background=True 로 부른다."""
    d = _design(db, design_id)
    preset = smallbiz.get(preset_id)
    if preset is None:
        raise HTTPException(400, "알 수 없는 스타일입니다")
    brand = _hidden_brand(db, preset)
    d.brand_id, d.brand_version_id = brand.id, brand.current_version_id
    d.brief = {**(d.brief or {}), "image_style": preset["image_style"], "smallbiz": {"preset_id": preset["id"]}}
    from .routers_review import invalidate_reviews
    invalidate_reviews(db, d.id)
    db.commit()
    return {"design_id": d.id, "preset_id": preset["id"]}


@router.post("/campaigns/{design_id}/export")
def export_campaign(design_id: int, db: Session = Depends(get_db)):
    """규격별 PNG 묶음(zip). 소상공인용은 검수 단계 없이 작업본으로 내보낸다."""
    from . import routers_export
    _design(db, design_id)
    return routers_export.make_export(design_id, None, True, db)
