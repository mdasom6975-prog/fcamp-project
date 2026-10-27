import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import calllog, compose, imagegen
from . import storage
from .db import BASE_DIR
from .models import (Asset, Brand, BrandVersion, CopyVariant, Design, DesignSnapshot, Element, GeneratedImage,
                     IconAsset, PresetVersion, ReviewResult)
from .routers_assets import FONT_DIR, get_db
from .routers_review import ensure_unlocked, invalidate_icon_reviews, invalidate_reviews

router = APIRouter(prefix="/api", dependencies=[Depends(calllog.track_design)])   # 호출 기록에 시안 id
log = logging.getLogger("app.images")


def _font_path(bv: BrandVersion, role: str, db: Session) -> str:
    """브랜드 폰트(역할 일치 → 첫 번째 → 제공 폰트 Pretendard Bold) 파일 경로."""
    fonts = bv.fonts or []
    f = next((x for x in fonts if x.get("role") == role), None) or (fonts[0] if fonts else None)
    if f and f.get("source") == "upload" and f.get("asset_id"):
        a = db.get(Asset, f["asset_id"])
        if a and storage.exists(a.file_path):
            return str(storage.local(a.file_path))      # ImageFont 는 로컬 파일이 필요하다
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


def _scene(g: GeneratedImage | None) -> str | None:
    """배경이 속한 장면 id. 배경 재생성 1회가 장면 하나이고, 그 참조로 만든 다른 비율군 변형은 같은 장면이다.
    장면을 기록하기 전(예전) 배경은 None → 어느 장면과도 같은 것으로 본다."""
    return (g.layout or {}).get("scene") if g is not None else None


def _same_scene(g: GeneratedImage, current: str | None) -> bool:
    s = _scene(g)
    return s is None or current is None or s == current


def _latest_design_bg(db: Session, design_id: int, template: str | None = None) -> GeneratedImage | None:
    """시안의 최신 배경. template 을 주면 그 비율군(생성 크기의 비율 기준)으로 만든 배경 중 **현재 장면**(가장 최근 배경의
    장면)에 속하는 것만 찾는다. 배경을 새로 만든 뒤 아직 안 만든 비율군의 예전 배경이 재사용되지 않게 하기 위한 것."""
    rows = db.scalars(select(GeneratedImage).where(
        GeneratedImage.design_id == design_id, GeneratedImage.kind == "background"
    ).order_by(GeneratedImage.id.desc())).all()
    if template is None:
        return rows[0] if rows else None
    current = _scene(rows[0]) if rows else None
    for g in rows:
        if compose.template_for(g.width, g.height) == template and _same_scene(g, current):
            return g
    return None


def _single_master(db: Session, design_id: int) -> GeneratedImage | None:
    """1장 방식의 원본: 현재 장면 배경 중 보완 변형(single_extra)이 아닌 가장 최근 것."""
    rows = db.scalars(select(GeneratedImage).where(
        GeneratedImage.design_id == design_id, GeneratedImage.kind == "background"
    ).order_by(GeneratedImage.id.desc())).all()
    current = _scene(rows[0]) if rows else None
    for g in rows:
        if _same_scene(g, current) and not (g.layout or {}).get("single_extra"):
            return g
    return rows[0] if rows else None


def _current_bg(db: Session, design_id: int, pid: int) -> GeneratedImage | None:
    """지면에 연결된 최신 배경이 현재 장면이면 그 행, 아니면(배경을 새로 만든 뒤 이 비율군은 아직 안 만듦) None."""
    bg = _latest(db, design_id, pid, "background")
    if bg is None:
        return None
    return bg if _same_scene(bg, _scene(_latest_design_bg(db, design_id))) else None


# 이미지 안 문구 설정: 기본은 '제목만'. 이미지가 광고 전체인 업로드형 배너(Google Ads 업로드형 300×250 등)만 본문·CTA 기본 켬.
# Meta·카카오 네이티브는 매체가 본문·버튼을 이미지 밖에 그리므로 끔. 분리 자산형(PMax 등)은 제목도 넣지 않으므로 해당 없음.
def in_image_default(pv) -> dict:
    if pv.placement.content_kind == "분리 자산형":
        return {"body": False, "cta": False, "available": False}
    banner = pv.placement.name.startswith("업로드형")
    return {"body": banner, "cta": banner, "available": True}


def in_image_for(d: Design, pv) -> dict:
    """지면의 이미지 안 문구 설정 {body, cta, available, default}. 브리프 in_image[preset_id] 가 있으면 그 값."""
    base = in_image_default(pv)
    if not base["available"]:
        return {**base, "default": True}
    saved = ((d.brief or {}).get("in_image") or {}).get(str(pv.id))
    if isinstance(saved, dict):
        return {**base, "body": bool(saved.get("body", base["body"])), "cta": bool(saved.get("cta", base["cta"])), "default": False}
    return {**base, "default": True}


def _design(db: Session, design_id: int) -> Design:
    d = db.get(Design, design_id)
    if not d or d.kind != "광고 이미지":
        raise HTTPException(404, "광고 이미지 시안을 찾을 수 없습니다")
    return d


@router.get("/image-usage")
def image_usage():
    """일일 한도 현황 + 오늘 호출 내역(종류별). 한도는 이미지 호출만 세고, calls 는 채팅까지 포함한다."""
    u = imagegen.usage()
    return {**u, "calls": calllog.summary(date=u["date"]),
            "quality": imagegen.OPENAI_QUALITY}


@router.get("/image-settings")
def image_settings():
    """프론트 선택용: 고를 수 있는 이미지 모델(정사각 1장 추정 비용)과 배경 방식, 레이아웃, 기본값.
    layouts 는 캠페인 입력의 레이아웃 고르기와 배치 그림에 쓴다(비율군별 문구 자리·정렬·제품 구역)."""
    return {"models": imagegen.model_options(), "modes": list(imagegen.BG_MODES), "default_mode": imagegen.default_bg_mode(),
            "quality": imagegen.OPENAI_QUALITY, "provider": imagegen.PROVIDER, "usd_krw": imagegen.pricing_rate(),
            "single_min_keep": imagegen.SINGLE_MIN_KEEP, "layouts": layout_options()}


def layout_options() -> list[dict]:
    """compose.LAYOUTS 를 화면용으로: 이름·설명과 비율군별 {side, align, product_slot, text_column}. 기본 레이아웃이 맨 앞이다."""
    out = []
    for name, tpls in compose.LAYOUTS.items():
        t0 = next(iter(tpls.values()))
        out.append({"name": name, "description": t0["description"], "version": t0["version"],
                    "templates": {tpl: {"side": t["text"]["side"], "align": t["text"]["align"],
                                        "product_slot": t["product_slot"], "text_column": t["text_column"]}
                                  for tpl, t in tpls.items()}})
    return sorted(out, key=lambda o: o["name"] != compose.DEFAULT_LAYOUT)


@router.put("/designs/{design_id}/image-settings")
def set_image_settings(design_id: int, image_model: str | None = Body(None, embed=True),
                       bg_mode: str | None = Body(None, embed=True), db: Session = Depends(get_db)):
    """시안의 생성 설정(이미지 모델·배경 방식)을 브리프에 저장한다. 다음 배경 생성·재생성부터 적용된다(기존 배경은 그대로)."""
    d = _design(db, design_id)
    ensure_unlocked(d)
    brief = dict(d.brief or {})
    if image_model is not None:
        if image_model not in imagegen.MODELS:
            raise HTTPException(400, f"이미지 모델은 {imagegen.MODELS} 중 하나")
        brief["image_model"] = image_model
    if bg_mode is not None:
        if bg_mode not in imagegen.BG_MODES:
            raise HTTPException(400, f"배경 방식은 {list(imagegen.BG_MODES)} 중 하나")
        brief["bg_mode"] = bg_mode
    d.brief = brief
    db.commit()
    return {"settings": imagegen.settings_for(d.brief), "image_model": brief.get("image_model"), "bg_mode": brief.get("bg_mode")}


@router.put("/designs/{design_id}/presets/{preset_id}/in-image")
def set_in_image(design_id: int, preset_id: int, body: bool | None = Body(None, embed=True),
                 cta: bool | None = Body(None, embed=True), db: Session = Depends(get_db)):
    """지면의 이미지 안 문구 설정(본문·CTA)을 브리프에 저장한다. 다음 합성('다시 합성')부터 적용. 분리 자산형은 400."""
    d = _design(db, design_id)
    if preset_id not in d.preset_version_ids:
        raise HTTPException(404, "이 시안에 포함된 규격이 아닙니다")
    pv = db.get(PresetVersion, preset_id)
    if not in_image_default(pv)["available"]:
        raise HTTPException(400, "분리 자산형 지면은 이미지에 문구를 넣지 않습니다")
    cur = in_image_for(d, pv)
    saved = dict(((d.brief or {}).get("in_image") or {}))
    saved[str(preset_id)] = {"body": cur["body"] if body is None else bool(body), "cta": cur["cta"] if cta is None else bool(cta)}
    d.brief = {**(d.brief or {}), "in_image": saved}
    db.commit()
    return {"preset_id": preset_id, **in_image_for(d, pv)}


@router.get("/designs/{design_id}/api-calls")
def design_api_calls(design_id: int, db: Session = Depends(get_db)):
    """이 시안이 지금까지 부른 모델 호출 집계와 최근 내역. 어느 단계에서 호출이 몰리는지 보기 위한 것."""
    if not db.get(Design, design_id):
        raise HTTPException(404, "시안을 찾을 수 없습니다")
    return {**calllog.summary(design_id=design_id), "recent": calllog.recent(design_id)}


@router.get("/designs/{design_id}/images")
def list_images(design_id: int, db: Session = Depends(get_db)):
    d = _design(db, design_id)
    out = []
    for pid in d.preset_version_ids:
        out.append({"preset_id": pid, "background": _img_out(_current_bg(db, design_id, pid)),
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
    """이 지면에 편집 화면에서 '저장'한 내용이 지금도 살아 있는가. 저장 시점마다 DesignSnapshot 이 남지만 스냅샷은 되돌리기 뒤에도
    남으므로(실행 취소용), 스냅샷이 있고 **가장 최근 합성본이 편집본(layout.edited)일 때만** 저장한 편집으로 본다.
    '처음 상태로 되돌리기'는 편집본이 아닌 새 합성본을 남기므로 그 뒤에는 다시 합성할 수 있다."""
    latest = db.scalars(select(GeneratedImage).where(
        GeneratedImage.design_id == design_id, GeneratedImage.preset_version_id == preset_id,
        GeneratedImage.kind == "composite").order_by(GeneratedImage.id.desc())).first()
    if latest is not None and not (latest.layout or {}).get("edited"):
        return False
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


def _product_image(db: Session, d: Design) -> tuple[Image.Image | None, int | None]:
    """캠페인 입력의 제품 누끼 이미지(투명 PNG)와 자산 id. 없거나 파일이 사라졌거나 images.edit 을 못 쓰는 공급자면 (None, None)."""
    aid = (d.brief or {}).get("product_image_asset_id")
    if not aid or not imagegen.can_edit():
        return None, None
    a = db.get(Asset, aid)
    if not a or not storage.exists(a.file_path):
        return None, None
    im = Image.open(storage.local(a.file_path)).convert("RGBA")
    im.thumbnail((1024, 1024), Image.LANCZOS)
    return im, aid


def _cutout_parts(db: Session, bg_row: GeneratedImage) -> dict:
    """투명 누끼를 코드로 붙인 배경이면 지면마다 누끼를 다시 붙일 재료 {clean, product, placed}(compose.crop_scene 인자), 아니면 {}.
    누끼는 배경을 만들 때 쓴 자산(layout.product_asset_id)을 _product_image 와 같은 크기로 읽는다(그 뒤 캠페인 제품을 바꿔도 이 배경과 짝이 맞게)."""
    lay = bg_row.layout or {}
    clean, placed, aid = lay.get("clean_file"), lay.get("product_placed"), lay.get("product_asset_id")
    if lay.get("product_mode") != "composite" or not (clean and placed and aid) or not storage.exists(clean):
        return {}
    a = db.get(Asset, aid)
    if not a or not storage.exists(a.file_path):
        return {}
    product = Image.open(storage.local(a.file_path)).convert("RGBA")
    product.thumbnail((1024, 1024), Image.LANCZOS)
    return {"clean": Image.open(storage.local(clean)), "product": product, "placed": placed}


def layout_name(d: Design) -> str:
    """캠페인 입력의 레이아웃. 예전 캠페인처럼 값이 없거나 지금 없는 이름이면 기본."""
    v = (d.brief or {}).get("layout")
    return v if v in compose.LAYOUTS else compose.DEFAULT_LAYOUT


def layout_ref(name: str) -> dict:
    """기록용 {name, version}: 배경 행과 합성 보고서에 남겨 어떤 배치로 만들었는지 본다."""
    return {"name": name, "version": compose.layout_tokens(compose.VERTICAL, name)["version"]}


def product_scale(d: Design) -> str:
    """캠페인 입력의 제품 크기(크게|보통|작게). 예전 캠페인처럼 값이 없으면 크게."""
    v = (d.brief or {}).get("product_scale")
    return v if v in compose.PRODUCT_SCALES else compose.DEFAULT_PRODUCT_SCALE


def _bg_prompt(db: Session, d: Design, template: str, force_ratio: bool = False, variant: bool = False) -> dict:
    """배경 프롬프트와 그 재료. 생성(_generate_one)과 대기 화면의 주문서 미리보기(background_prompt_preview)가 같은 문장을 쓴다.
    반환: prompt(variant 면 same_scene 로 감싼 것), style, reason, single, product_img, product_id, pmode, composite, bv, brand, settings."""
    bv = db.get(BrandVersion, d.brand_version_id)
    brand = db.get(Brand, d.brand_id)
    style, reason = imagegen.pick_style(bv.image_tone or {}, d.brief or {})
    st = imagegen.settings_for(d.brief)
    # 1장 방식: 공용 구도, 비율군 크기 그대로(1024×1536 등). force_ratio 는 1장 방식에서 손실이 큰 비율군을 그 1장을
    # 참조해 비율군 구도로 1장 더 만들 때(크롭공용 대신 비율군 구도, 참조 변형 허용)
    single = st["bg_mode"] == "single" and not force_ratio
    product_img, product_id = _product_image(db, d)
    pmode = imagegen.product_mode(product_img) if imagegen.PROVIDER == "openai" else None
    composite = pmode == "composite"
    # 합성 방식이면 생성 크기(_generate_one 과 같은 값)에서 누끼가 실제로 놓일 상자를 미리 계산해 그 자리만 비우게 한다
    gw, gh = imagegen.size_for(template, per_ratio=True if st["bg_mode"] == "single" else None)
    lay = layout_name(d)
    placed = compose.product_box(gw, gh, product_img, template, product_scale(d), lay) if composite else None
    prompt = imagegen.build_prompt(brand.name, bv.image_tone or {}, d.brief, template, style, single=single,
                                   product=product_img is not None, variant=variant, composite=composite,
                                   colors=bv.colors, placed=placed, layout=lay)
    if variant:
        prompt = imagegen.build_variant_prompt(prompt)
    return {"prompt": prompt, "style": style, "reason": reason, "single": single, "product_img": product_img,
            "product_id": product_id, "pmode": pmode, "composite": composite, "bv": bv, "brand": brand, "settings": st}


def _generate_one(db: Session, d: Design, template: str, reference: GeneratedImage | None = None,
                  force_ratio: bool = False) -> tuple[str, Image.Image, str, dict]:
    """템플릿(비율군) 하나의 배경을 생성해 저장한다. 반환: (저장소 키, 이미지, 프롬프트, 기록용 layout).
    품질은 항상 imagegen.OPENAI_QUALITY(기본 medium)이며 layout.quality 에 남긴다.
    생성 크기는 imagegen.size_for(비율군별이면 1024×1536 / 1536×1024 / 1024²), 형태는 브리프 image_style(자동이면 톤·설명으로).
    reference(다른 비율군의 배경 행)를 주고 변형이 가능하면 그 이미지를 참조로 붙여 '같은 장면'을 이 비율의 구도로 다시 만든다.
    캠페인에 제품 누끼 이미지가 있으면(OpenAI) 참조 이미지로 함께 붙여 장면의 주 피사체로 그리게 한다.
    프롬프트는 _bg_prompt 가 만든다."""
    st = imagegen.settings_for(d.brief)
    gw, gh = imagegen.size_for(template, per_ratio=True if st["bg_mode"] == "single" else None)
    quality = imagegen.OPENAI_QUALITY
    provider = imagegen.PROVIDER
    ref_img = None
    # 참조 변형(images.edit)은 OpenAI 로 만들 때만: 비율군 변형은 can_vary, 1장 방식의 보완 변형(force_ratio)은 can_edit
    if reference is not None and storage.exists(reference.file_path) and provider == "openai" \
            and ((imagegen.VARIANT and imagegen.can_edit()) if force_ratio else imagegen.can_vary(st["bg_mode"])):
        # 제품을 합성한 배경이면 제품이 없는 원본(clean_file)을 참조한다(제품을 다시 그리지 않고 장면만 이어받는다)
        clean = (reference.layout or {}).get("clean_file")
        ref_img = Image.open(storage.local(clean if clean and storage.exists(clean) else reference.file_path))
    bp = _bg_prompt(db, d, template, force_ratio=force_ratio, variant=ref_img is not None)
    prompt, style, reason, single = bp["prompt"], bp["style"], bp["reason"], bp["single"]
    product_img, product_id, pmode, composite, bv = bp["product_img"], bp["product_id"], bp["pmode"], bp["composite"], bp["bv"]
    try:
        if composite:        # 제품 없는 빈 배경만 생성(참조는 장면 하나) → 아래에서 원본 누끼를 붙인다
            bg = (imagegen.generate_variant(prompt, gw, gh, ref_img, quality, model=st["model"]) if ref_img is not None
                  else imagegen.generate_background(prompt, gw, gh, quality, model=st["model"], provider=provider))
        elif ref_img is not None or product_img is not None:
            bg = imagegen.generate_variant(prompt, gw, gh, ref_img, quality, model=st["model"], product=product_img)
        else:
            bg = imagegen.generate_background(prompt, gw, gh, quality, model=st["model"], provider=provider)
    except imagegen.QuotaExceeded as e:
        raise HTTPException(429, str(e))
    except Exception as e:
        raise HTTPException(502, _gen_error(e))
    extra: dict = {}
    if composite:
        clean_key = storage.put_image(f"bgclean_{uuid.uuid4().hex[:10]}.png", bg)
        bg, placed = compose.paste_product(bg, product_img, template, product_scale(d), layout_name(d))
        extra = {"clean_file": clean_key, "product_placed": placed}
    path = storage.put_image(f"bg_{uuid.uuid4().hex[:10]}.png", bg)
    layout = {"template": template, "size": [bg.width, bg.height], "style": style, "style_reason": reason,
              "quality": quality, "mode": st["bg_mode"], "provider": provider,
              **({"single_extra": True} if force_ratio else {}),
              **({"product_asset_id": product_id, "product_mode": pmode} if product_img is not None else {}), **extra,
              "model": st["model"] if provider == "openai" else imagegen.CF_MODEL,
              "layout": layout_ref(layout_name(d)),
              # 장면 id: 다른 비율군 참조 변형이면 참조의 장면(같은 장면), 독립 생성이면 이 파일이 새 장면의 시작
              "scene": (_scene(reference) or Path(path).stem) if ref_img is not None else Path(path).stem,
              "prompts": imagegen.prompt_refs(template, style, variant=ref_img is not None, single=single,
                                              product=product_img is not None, composite=composite, colors=bv.colors)}
    if ref_img is not None:
        layout["reference"] = {"background_id": reference.id, "file": Path(reference.file_path).name,
                               "template": (reference.layout or {}).get("template")}
    return path, bg, prompt, layout


def _bg_row(d: Design, pid: int, path: Path | str, bg_w: int, bg_h: int, prompt: str | None, bv_id: int,
            layout: dict) -> GeneratedImage:
    return GeneratedImage(design_id=d.id, preset_version_id=pid, kind="background", file_path=str(path),
                          width=bg_w, height=bg_h, prompt=prompt, brand_version_id=bv_id, layout=dict(layout))


def create_shared_background(db: Session, d: Design,
                             in_use: list[int] | None = None) -> tuple[dict[int, GeneratedImage], list[str]]:
    """배경을 비율군(가로형/정사각/세로형)마다 1장씩 만들고, 같은 비율군의 규격은 같은 파일로 연결한다.
    반환: ({preset_id: 배경 행}, 미뤄 둔 비율군 이름들).
    첫 비율군(시안의 첫 규격 기준)은 프롬프트로 생성하고, 나머지 비율군은 그 이미지를 참조로 붙여 **같은 장면**을
    각 비율의 구도로 다시 만든다(images.edit, IMAGE_BG_VARIANT=0 이면 독립 생성). 비용은 만든 비율군 수만큼 든다.
    in_use 를 주면 그 지면들이 속한 비율군만 지금 만들고, 나머지 비율군은 미뤄 둔다(그 지면을 합성할 때
    make_image 가 현재 장면을 참조해 1장 만든다). None 이면 모든 비율군을 만든다.
    IMAGE_BG_PER_RATIO=0 이면 정사각 1장을 모두가 공유."""
    sizes = [(pid, pv.production_width, pv.production_height) for pid in d.preset_version_ids
             if (pv := db.get(PresetVersion, pid)) and pv.production_width]
    if not sizes:
        raise HTTPException(400, "제작 크기가 정해진 규격이 없습니다")
    bv = db.get(BrandVersion, d.brand_version_id)
    groups: dict[str, list[int]] = {}
    for pid, w, h in sizes:
        groups.setdefault(compose.template_for(w, h), []).append(pid)
    single = imagegen.settings_for(d.brief)["bg_mode"] == "single"
    if single:
        # 1장 방식: 가장 적게 잘리는 비율군 한 장을 모든 지면이 잘라 쓴다. 다만 그 한 장을 잘라서 피사체가 많이 사라지는
        # 비율군(crop_keep < IMAGE_SINGLE_MIN_KEEP, 보통 정사각→가로형)은 그 1장을 참조해 비율군 구도로 1장 더 만든다.
        best = imagegen.best_single_template([(w, h) for _, w, h in sizes])
        gen = imagegen.TEMPLATE_SIZES[best]
        lossy = {t: p for t, p in groups.items()
                 if t != best and any(imagegen.lossy_single(gen, (w, h)) for pid, w, h in sizes if pid in p)}
        groups = {best: [pid for pid, _, _ in sizes if not any(pid in p for p in lossy.values())], **lossy}
        if in_use is not None:            # 미루기는 손실 큰 비율군에만 적용(한 장은 항상 지금 만든다)
            in_use = list(set(in_use) | set(groups[best]))
    deferred: list[str] = []
    if in_use is not None:
        wanted = set(in_use)
        now = {t: p for t, p in groups.items() if wanted & set(p)}
        if not now:                       # 사용 중인 지면이 없으면 첫 비율군만 만든다
            first = next(iter(groups))
            now = {first: groups[first]}
        deferred = [t for t in groups if t not in now]
        groups = now
    rows: dict[int, GeneratedImage] = {}
    master: GeneratedImage | None = None
    for template, pids in groups.items():
        try:
            path, bg, prompt, layout = _generate_one(db, d, template, reference=master,
                                                     force_ratio=single and master is not None)
        except HTTPException as e:
            if master is None:
                raise
            # 변형 생성이 실패해도 이미 만든(과금된) 첫 배경을 버리지 않는다: 이 비율군은 첫 배경을 쓰고 사유를 남긴다.
            # 1장 방식의 손실 큰 비율군이면 잘라 쓰지 않고 '블러 채우기'(전체를 넣고 남는 자리는 흐린 확대본)로 합성한다
            log.warning("비율군 %s 변형 실패 → 첫 배경으로 대체: %s", template, e.detail)
            layout = {**(master.layout or {}), "template": template, "fallback": str(e.detail), **({"fill": "blur"} if single else {})}
            for pid in pids:
                rows[pid] = _bg_row(d, pid, master.file_path, master.width, master.height, master.prompt, bv.id, layout)
                db.add(rows[pid])
            db.flush()
            continue
        if master is not None:
            # 한 번의 재생성에서 만든 배경은 참조 변형이든 독립 생성(FLUX, IMAGE_BG_VARIANT=0)이든 같은 장면으로 묶는다.
            # 그래야 목록·합성이 마지막 비율군만 '현재 장면'으로 보고 나머지를 숨기지 않는다.
            layout = {**layout, "scene": _scene(master) or layout.get("scene")}
        for pid in pids:
            rows[pid] = _bg_row(d, pid, path, bg.width, bg.height, prompt, bv.id, layout)
            db.add(rows[pid])
        db.flush()
        if master is None:
            master = rows[pids[0]]
    return rows, deferred


@router.get("/designs/{design_id}/background/prompt-preview")
def background_prompt_preview(design_id: int, preset_id: int | None = None, db: Session = Depends(get_db)):
    """대기 화면의 'AI 주문서': 이 지면(없으면 시안의 첫 지면) 비율군의 배경을 새로 그릴 때 보내는 프롬프트를 블록으로.
    생성과 같은 _bg_prompt 로 만들며 이미지 모델은 부르지 않는다. 다른 비율군을 참조하는 변형(same_scene)은 앞에 안내 문장이
    더 붙지만 여기서는 기준 문장만 보여 준다."""
    d = _design(db, design_id)
    ids = [preset_id] if preset_id is not None else list(d.preset_version_ids)
    pv = next((p for p in (db.get(PresetVersion, i) for i in ids) if p and p.production_width), None)
    if pv is None:
        raise HTTPException(404, "배경을 만들 지면이 없습니다")
    template = compose.template_for(pv.production_width, pv.production_height)
    bp = _bg_prompt(db, d, template)
    return {"template": template, "style": bp["style"], "single": bp["single"], "composite": bp["composite"],
            "model": bp["settings"]["model"], "blocks": imagegen.prompt_blocks(bp["prompt"]),
            "colors": [{"role": c.get("role") or "", "hex": c["hex"].upper()} for c in (bp["bv"].colors or []) if c.get("hex")]}


@router.post("/designs/{design_id}/background/regenerate")
def regenerate_background(design_id: int, style: str | None = Body(None, embed=True), db: Session = Depends(get_db)):
    """시안 전체의 배경을 비율군마다 새로 생성하고, 같은 비율군 매체가 같은 원본을 공유하도록 연결한다.
    style 을 주면 브리프의 배경 형태(image_style)를 그 값으로 바꾼 뒤 생성한다.
    품질은 항상 IMAGE_QUALITY(기본 medium). 검수 완료된 캠페인은 409.
    배경은 **합성본이 있는 지면의 비율군**만 지금 만든다(합성본이 하나도 없으면 모든 비율군). 나머지 비율군은
    그 지면을 합성할 때 현재 장면을 참조해 만든다 → 쓰지 않는 비율군의 호출이 없어진다."""
    d = _design(db, design_id)
    ensure_unlocked(d)
    if style is not None:
        style = style.strip() or imagegen.AUTO_STYLE
        if style != imagegen.AUTO_STYLE and style not in imagegen.STYLES:
            raise HTTPException(400, f"배경 형태는 {[imagegen.AUTO_STYLE, *imagegen.STYLES]} 중 하나")
        d.brief = {**(d.brief or {}), "image_style": style}
        db.flush()
    composed = [pid for pid in d.preset_version_ids if _latest(db, design_id, pid, "composite") is not None]
    rows, deferred = create_shared_background(db, d, in_use=composed or None)
    # 편집 화면에서 저장한 지면은 자동 합성(make_image)이 409 로 막히므로, 편집 요소를 유지한 채 새 배경으로 여기서 다시 그린다
    from .routers_edit import rerender_edited     # 순환 import 회피 (routers_edit 가 이 모듈을 import 한다)
    applied, skipped = rerender_edited(db, d)
    db.commit()
    first = next(iter(rows.values()))
    fallbacks = sorted({r.layout.get("fallback") for r in rows.values() if r.layout.get("fallback")})
    extra_groups = sorted({r.layout.get("template") for r in rows.values() if r.layout.get("single_extra")})
    blur_groups = sorted({r.layout.get("template") for r in rows.values() if r.layout.get("fill") == "blur"})
    return {"preset_ids": list(rows), "generated": len({r.file_path for r in rows.values()}), "deferred": deferred,
            "single_extra": extra_groups, "blur_fill": blur_groups,
            "applied": applied, "skipped": skipped,
            "style": first.layout.get("style"), "style_reason": first.layout.get("style_reason"),
            "quality": first.layout.get("quality"),
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
    # 다른 행이 같은 파일을 쓰지 않을 때만 파일 삭제 (storage 키만)
    still = {p for (p,) in db.execute(select(GeneratedImage.file_path)).all()}
    still |= {p for row in db.execute(select(IconAsset.png_path, IconAsset.source_path)).all() for p in row if p}
    removed = 0
    for p in paths - still:
        if storage.delete(p):
            removed += 1
        storage.delete(_thumb_key(p))                  # 이 시안의 축소본 캐시(파일 이름 기준)도 정리
    db.commit()
    return {"deleted_images": len(imgs), "deleted_files": removed, "copy_cleared": include_copy,
            "icons_deleted": icons_deleted}


@router.post("/designs/{design_id}/presets/{preset_id}/image")
def make_image(design_id: int, preset_id: int, regenerate_background: bool = Body(False, embed=True),
               db: Session = Depends(get_db)):
    return compose_preset(db, design_id, preset_id, regenerate_background)


def compose_preset(db: Session, design_id: int, preset_id: int, regenerate_background: bool = False,
                   discard_edits: bool = False) -> dict:
    """지면 하나의 배경(없으면 생성)에 현재 카피·지면 설정(이미지 안 본문·CTA 등)으로 문구를 합성한다.
    discard_edits 면 편집 화면에서 저장한 내용도 버리고 자동 합성으로 되돌린다('처음 상태로 되돌리기'). 아니면 저장한 편집이 있을 때 409."""
    d = _design(db, design_id)
    ensure_unlocked(d)
    if preset_id not in d.preset_version_ids:
        raise HTTPException(404, "이 시안에 포함된 규격이 아닙니다")
    pv = db.get(PresetVersion, preset_id)
    if not pv or not pv.production_width:
        raise HTTPException(400, "제작 크기가 정해지지 않은 규격입니다")
    w, h = pv.production_width, pv.production_height
    els = db.scalars(select(Element).where(Element.design_id == design_id, Element.preset_version_id == preset_id)).all()
    if els:
        if _edit_saved(db, design_id, preset_id) and not discard_edits:
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
    st = imagegen.settings_for(d.brief)
    single = st["bg_mode"] == "single"
    tpl = compose.template_for(w, h)
    bg_row = None
    single_fill = False      # 1장 방식에서 변형 없이 손실 큰 비율군을 쓸 때: 블러 채우기로 합성
    single_master = None     # 1장 방식의 현재 장면 원본(변형의 참조)
    if not regenerate_background:
        latest = _latest_design_bg(db, design_id)
        same = _latest_design_bg(db, design_id, tpl)
        if single:
            # 1장 방식: 같은 비율군 배경(원본이든 손실 보완 변형이든)이 있으면 그대로. 없으면 원본(보완 변형이 아닌 것)을 잘라 쓰되,
            # 손실이 크면(crop_keep < 기준) 원본을 참조한 변형을 1장 만들고, 변형이 불가능하면 블러 채우기로 쓴다
            single_master = _single_master(db, design_id)
            m = single_master
            if same is not None:
                src = same
            elif m is not None and imagegen.lossy_single((m.width, m.height), (w, h)) \
                    and imagegen.VARIANT and imagegen.can_edit():
                src = None                                   # 아래에서 변형 생성
            else:
                src = m
                single_fill = m is not None and imagegen.lossy_single((m.width, m.height), (w, h))
        else:
            # 같은 비율군 배경이 있으면 그대로 연결(생성 호출 없음). 없고 다른 비율군 배경만 있으면: 변형 생성이 가능할 때는
            # 그 장면을 참조해 이 비율군 배경을 1장 만들고(비율군마다 1장 원칙), 불가능하면 예전처럼 잘라 쓴다
            src = same or (None if imagegen.can_vary(st["bg_mode"]) else latest)
        if src is not None and storage.exists(src.file_path):
            bg_row = _latest(db, design_id, preset_id, "background")
            if bg_row is None or bg_row.file_path != src.file_path:   # 이 규격에 연결(파일은 공유, 생성 호출 없음)
                bg_row = _bg_row(d, preset_id, src.file_path, src.width, src.height, src.prompt,
                                 src.brand_version_id, {**(src.layout or {}), **({"fill": "blur"} if single_fill else {})})
                db.add(bg_row)
                db.flush()
            elif single_fill and not (bg_row.layout or {}).get("fill"):
                bg_row.layout = {**(bg_row.layout or {}), "fill": "blur"}
                db.flush()
    if bg_row is None:
        # 새로 만들 때 시안에 다른 배경이 있으면 그 장면을 참조해 통일한다. 변형이 실패하면 그 배경을 잘라 쓴다(폴백)
        ref = _latest_design_bg(db, design_id)
        gen_tpl, force_ratio = tpl, False
        if single:
            if single_master is not None and not regenerate_background:
                # 1장 방식에서 손실 큰 비율군: 원본을 참조해 이 비율군 구도로 1장 더 (실패하면 아래에서 블러 채우기 폴백)
                ref, force_ratio = single_master, True
            else:
                # 배경이 없거나 다시 만들기: 모든 지면이 잘라 쓸 비율군 한 장을 공용 구도로 만든다
                sizes = [(p2.production_width, p2.production_height) for pid2 in d.preset_version_ids
                         if (p2 := db.get(PresetVersion, pid2)) and p2.production_width]
                gen_tpl, ref = imagegen.best_single_template(sizes), None
        try:
            path, bg, prompt, layout = _generate_one(db, d, gen_tpl, reference=ref if ref and ref.file_path else None,
                                                     force_ratio=force_ratio)
            bg_row = _bg_row(d, preset_id, path, bg.width, bg.height, prompt, bv.id, layout)
            if single and not force_ratio and imagegen.lossy_single((bg.width, bg.height), (w, h)):
                # 방금 만든 원본을 이 지면이 많이 잘라야 하면 원본을 참조한 비율군 변형을 바로 1장 더 (실패하면 블러 채우기)
                db.add(bg_row); db.flush()
                if imagegen.VARIANT and imagegen.can_edit():
                    try:
                        p2, bg2, pr2, lay2 = _generate_one(db, d, tpl, reference=bg_row, force_ratio=True)
                        bg_row = _bg_row(d, preset_id, p2, bg2.width, bg2.height, pr2, bv.id, lay2)
                    except HTTPException as e2:
                        log.warning("1장 방식 보완 변형 실패 → 블러 채우기: %s", e2.detail)
                        bg_row.layout = {**(bg_row.layout or {}), "fill": "blur", "fallback": str(e2.detail)}
                else:
                    bg_row.layout = {**(bg_row.layout or {}), "fill": "blur"}
        except HTTPException as e:
            if ref is None or regenerate_background or not storage.exists(ref.file_path):
                raise
            log.warning("비율군 %s 변형 실패 → 기존 배경으로 대체: %s", tpl, e.detail)
            bg_row = _bg_row(d, preset_id, ref.file_path, ref.width, ref.height, ref.prompt, ref.brand_version_id,
                             {**(ref.layout or {}), "template": tpl, "fallback": str(e.detail), **({"fill": "blur"} if force_ratio else {})})
        db.add(bg_row)
        db.flush()
    bg_img = Image.open(storage.local(bg_row.file_path))
    # 블러 채우기: 1장 방식에서 손실 큰 비율군의 변형을 못 만들었을 때. 전체를 넣고 남는 자리는 흐린 확대본 (피사체 손실 없음)
    fill_blur = (bg_row.layout or {}).get("fill") == "blur"
    # 누끼를 붙인 배경은 붙인 채로 자르면 지면 비율에 따라 제품이 잘린다 → 누끼 없는 원본을 자르고 지면마다 다시 붙인다
    parts = _cutout_parts(db, bg_row)

    if separate:
        # 분리 자산형: 텍스트 없는 이미지만. 로고·카피는 내보내기에서 별도 파일로 제공한다.
        out_img, crop, product_fit = compose.crop_scene(bg_img, w, h, layout_name(d), fill_blur, **parts)
        report = {"template": compose.template_for(w, h), "canvas": [w, h], "separate_assets": True,
                  "crop": crop, "warnings": [], "elements": [], **({"product_fit": product_fit} if product_fit else {})}
        kind_path = storage.put_image(f"sep_{uuid.uuid4().hex[:10]}.png", out_img)
        row = GeneratedImage(design_id=design_id, preset_version_id=preset_id, kind="composite",
                             file_path=kind_path, width=w, height=h, brand_version_id=bv.id, layout=report)
    else:
        logo = None
        if bv.logo_asset_id:
            a = db.get(Asset, bv.logo_asset_id)
            if a and storage.exists(a.file_path):
                logo = compose.load_logo(a.file_path, a.mime)
        text_hex = _color(bv, "텍스트색")
        scrim_hex = _color(bv, "배경색")
        ii = in_image_for(d, pv)        # 이미지 안에 본문·CTA 를 넣을지 (업로드형 배너 기본 켬)
        out_img, report = compose.compose(
            bg_img, w, h, sel.headline, _font_path(bv, "제목", db), logo,
            pv.safe_area, (bv.logo_margin or {}).get("percent"), text_hex, scrim_hex, fill_blur=fill_blur,
            body=sel.body if ii["body"] else None, cta=sel.cta if ii["cta"] else None,
            body_font_path=_font_path(bv, "본문", db), accent_hex=_color(bv, "주조색"),
            corner=(bv.icon_style or {}).get("corner") or "둥근", logo_position=bv.logo_position or "좌상단",
            layout=layout_name(d), **parts)
        report["in_image_setting"] = {"body": ii["body"], "cta": ii["cta"], "default": ii["default"]}
        # 배경은 만든 때의 레이아웃 구도로 그려져 있다. 그 뒤 레이아웃을 바꿨으면 문구 자리와 피사체가 어긋날 수 있다
        bg_lay = ((bg_row.layout or {}).get("layout") or {}).get("name") or compose.DEFAULT_LAYOUT
        if bg_lay != layout_name(d):
            report["warnings"].append(f"배경은 '{bg_lay}' 레이아웃 구도로 만들어졌습니다. "
                                      f"'{layout_name(d)}' 구도에 맞추려면 배경을 새로 생성하세요")
        if bv.logo_asset_id and logo is None:
            report["warnings"].append("로고 파일을 찾지 못해 합성하지 못했습니다")
        if not bv.logo_asset_id:
            report["warnings"].append("브랜드에 로고가 등록되어 있지 않습니다")
        path = storage.put_image(f"cmp_{uuid.uuid4().hex[:10]}.png", out_img)
        row = GeneratedImage(design_id=design_id, preset_version_id=preset_id, kind="composite",
                             file_path=path, width=w, height=h, copy_variant_id=sel.id,
                             brand_version_id=bv.id, layout=report)
    db.add(row)
    invalidate_reviews(db, design_id, preset_id)
    db.commit()
    return {"background": _img_out(bg_row), "composite": _img_out(row)}


@router.get("/generated/{image_id}/file")
def image_file(image_id: int, db: Session = Depends(get_db)):
    g = db.get(GeneratedImage, image_id)
    if not g or not storage.exists(g.file_path):
        raise HTTPException(404, "이미지를 찾을 수 없습니다")
    return storage.response(g.file_path, "image/png")


THUMB_SIDE = 360


def _thumb_key(file_path: str) -> str:
    return f"thumbs/{Path(file_path).stem}_{THUMB_SIDE}.jpg"   # 파일 이름 기준(행 id 재사용 대비)


@router.get("/generated/{image_id}/thumb")
def image_thumb(image_id: int, db: Session = Depends(get_db)):
    """썸네일 그리드용 축소본(긴 변 360px JPEG). 생성 결과는 바뀌지 않으므로 id 기준으로 파일 캐시한다."""
    g = db.get(GeneratedImage, image_id)
    if not g or not storage.exists(g.file_path):
        raise HTTPException(404, "이미지를 찾을 수 없습니다")
    out = _thumb_key(g.file_path)
    if not storage.exists(out):
        im = Image.open(storage.local(g.file_path)).convert("RGB")
        im.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
        storage.put_image(out, im, "JPEG", quality=82)
    return storage.response(out, "image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
