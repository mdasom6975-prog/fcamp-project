"""배경 이미지 생성. IMAGE_PROVIDER 로 OpenAI(기본) / Cloudflare Workers AI(FLUX.1 schnell)를 전환한다.
텍스트·로고는 그리지 않게 하고 합성은 compose 에서 한다."""
import base64
import io
import os
import re
import threading
from datetime import datetime, timezone

import httpx
from PIL import Image

from . import calllog, compose, prompts, state
from .copywriter import client  # (load_dotenv 가 copywriter import 시점에 실행된다)

PROVIDER = os.getenv("IMAGE_PROVIDER", "openai").strip().lower()      # openai | cloudflare
# OpenAI 기본값: gpt-image-1.5 + medium 품질(비교 테스트 결과 반영, 정사각 장당 약 60원)
OPENAI_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1.5")
OPENAI_QUALITY = os.getenv("IMAGE_QUALITY", "medium")                 # low | medium | high | auto — 모든 배경 생성에 쓰는 단일 품질
OPENAI_SIZE = os.getenv("IMAGE_SIZE", "1024x1024")                    # 비율군별 생성을 끈 경우(또는 3D 아이콘)의 크기
# 배경을 비율군(가로형/정사각/세로형)마다 모델 지원 크기로 따로 만든다. 0 이면 예전처럼 정사각 1장을 모든 규격이 공유.
PER_RATIO = os.getenv("IMAGE_BG_PER_RATIO", "1") != "0"
# 비율군이 여럿이면 첫 비율군 배경을 참조 이미지로 붙여(images.edit) 나머지를 '같은 장면'으로 만든다. 0 이면 비율군마다 독립 생성.
VARIANT = os.getenv("IMAGE_BG_VARIANT", "1") != "0"
VARIANT_FIDELITY = os.getenv("IMAGE_VARIANT_FIDELITY", "high")      # high | low — 참조 이미지를 얼마나 그대로 지킬지
# 템플릿 → gpt-image 지원 크기. 비정사각은 픽셀이 1.5배라 출력 토큰(비용)도 약 1.5배다.
TEMPLATE_SIZES = {"세로형": (1024, 1536), "가로형": (1536, 1024), "정사각": (1024, 1024)}
# 사용자가 시안마다 고를 수 있는 이미지 모델 목록(IMAGE_MODELS, 쉼표 구분). 비용·품질 비교용. 기본 모델은 항상 포함.
# 환경변수가 없으면 gpt-image 계열 후보를 기본으로 둔다(계정에서 못 쓰는 모델은 생성 시 공급자 오류 문구로 알 수 있다).
DEFAULT_MODEL_CHOICES = ["gpt-image-1", "gpt-image-1.5", "gpt-image-2.5-flare"]
MODELS = [m.strip() for m in os.getenv("IMAGE_MODELS", ",".join(DEFAULT_MODEL_CHOICES)).split(",") if m.strip()]
if OPENAI_MODEL not in MODELS:
    MODELS.insert(0, OPENAI_MODEL)
# 배경 방식: per_ratio = 비율군마다 1장(1~3회, 변형은 images.edit) / single = 1장을 모든 지면이 잘라 씀(1회, edit 없음)
BG_MODES = ("per_ratio", "single")


def default_bg_mode() -> str:
    return "per_ratio" if PER_RATIO else "single"
SINGLE_COMPOSITION = "크롭공용"      # prompts/image/composition/크롭공용.md — 어느 방향으로 잘려도 되는 구도


def settings_for(brief: dict | None) -> dict:
    """시안 브리프의 생성 설정 {model, bg_mode}. 없거나 허용 목록 밖이면 환경변수 기본값."""
    b = brief or {}
    model = b.get("image_model") if b.get("image_model") in MODELS else OPENAI_MODEL
    mode = b.get("bg_mode") if b.get("bg_mode") in BG_MODES else default_bg_mode()
    return {"model": model, "bg_mode": mode}


def retained_fraction(gen: tuple[int, int], target: tuple[int, int]) -> float:
    """생성 크기 gen 을 target 비율로 cover-crop 할 때 남는 면적 비율(0~1)."""
    (W, H), (w, h) = gen, target
    s = max(w / W, h / H)
    return (w / s) * (h / s) / (W * H)


# 1장 방식에서 '잘라 써도 되는' 기준. 세로가 잘리면(정사각→가로형) 피사체 자체가 없어지므로 손실을 그대로 세고,
# 가로가 잘리면(정사각→세로형) 중앙의 피사체는 남으므로 손실을 절반만 센다. 이 값보다 적게 남는 비율군은 1장을 참조한 변형을 1장 더 만든다.
SINGLE_MIN_KEEP = float(os.getenv("IMAGE_SINGLE_MIN_KEEP", "0.6"))
SIDE_LOSS_WEIGHT = 0.5


def crop_keep(gen: tuple[int, int], target: tuple[int, int]) -> float:
    """1장 방식의 체감 보존율(0~1). cover-crop 으로 남는 비율에 잘리는 축의 가중치를 적용한다."""
    (W, H), (w, h) = gen, target
    keep = retained_fraction(gen, target)
    cuts_height = (w / h) > (W / H)       # 목표가 더 넓으면 위아래가 잘린다
    return 1 - (1 - keep) * (1.0 if cuts_height else SIDE_LOSS_WEIGHT)


def lossy_single(gen: tuple[int, int], target: tuple[int, int]) -> bool:
    return crop_keep(gen, target) < SINGLE_MIN_KEEP


def best_single_template(sizes: list[tuple[int, int]]) -> str:
    """1장 방식에서 쓸 생성 비율군: 모든 지면으로 잘랐을 때 '가장 많이 잘리는 지면'의 손실이 가장 작은 것.
    같으면 정사각(비용이 2/3)을 고른다. 세로형만 있으면 세로형, 가로형만이면 가로형, 섞이면 보통 정사각이 된다."""
    if not sizes:
        return "정사각"
    def worst(t: str) -> float:
        return min(retained_fraction(TEMPLATE_SIZES[t], s) for s in sizes)
    order = ("정사각", "세로형", "가로형")       # 동률이면 앞쪽(정사각) 우선
    return max(order, key=lambda t: (round(worst(t), 6), -order.index(t)))
# 배경 형태. 키는 사용자에게 보이는 이름, 값은 prompts/image/style/<파일>.md
STYLES = {"실사 인물": "실사인물", "실사 제품·공간": "실사제품", "플랫 일러스트": "플랫일러스트",
          "3D 렌더": "3D렌더", "그래픽 패턴": "그래픽패턴"}
AUTO_STYLE = "자동"
# 사람이 나오면 안 되는 형태. 이 형태면 프롬프트의 사람 관련 문장을 '그리지 않는다'로 바꾸고 끝에서 한 번 더 말한다(people_vars)
NO_PEOPLE_STYLES = ("실사 제품·공간", "3D 렌더", "그래픽 패턴")
DEFAULT_STYLE = "실사 제품·공간"
# 자동 선택 키워드(브랜드 image_tone 설명 + 브리프 설명에서 찾는다). 앞에 있는 규칙이 우선.
_STYLE_RULES = [("플랫 일러스트", ("일러스트", "드로잉", "벡터", "만화")),
                ("3D 렌더", ("3d", "3D", "렌더", "입체", "레진", "클레이")),
                ("그래픽 패턴", ("패턴", "그래픽", "추상", "도형", "타이포")),
                ("실사 인물", ("인물", "사람", "모델", "가족", "커플"))]
CF_MODEL = os.getenv("CLOUDFLARE_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
CF_STEPS = 4              # schnell 은 1~8, 4면 충분
CF_MAX_PROMPT_CHARS = 2000  # Workers AI 프롬프트 상한(2048) 보다 약간 작게

# 하루 생성 상한(UTC 기준). Cloudflare 는 무료 한도 추정치, OpenAI 는 비용 폭주를 막는 안전장치다.
DAILY_LIMIT = int(os.getenv("IMAGE_DAILY_LIMIT", "50"))
USAGE_KEY = "image_usage"   # KV 테이블의 키(예전 data/image_usage.json)
_lock = threading.Lock()


class QuotaExceeded(Exception):
    """일일 한도 또는 공급자 한도/크레딧 초과. 메시지는 그대로 사용자에게 보여 준다."""


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load() -> dict:
    """{date, providers: {이름: {count, exhausted}}}. 날짜가 바뀌면 리셋하고, 옛 형식은 cloudflare 로 이관한다."""
    u = state.get(USAGE_KEY) or {}
    if u.get("date") != _today():
        return {"date": _today(), "providers": {}}
    if "providers" not in u:
        u = {"date": u["date"], "providers": {"cloudflare": {"count": u.get("count", 0),
                                                              "exhausted": u.get("exhausted", False)}}}
    return u


def usage(provider: str = PROVIDER) -> dict:
    with _lock:
        u = _load()
    p = u["providers"].get(provider, {})
    used = p.get("count", 0)
    return {"date": u["date"], "provider": provider, "used": used, "limit": DAILY_LIMIT,
            "remaining": max(0, DAILY_LIMIT - used), "exhausted": bool(p.get("exhausted")) or used >= DAILY_LIMIT}


def _bump(exhausted: bool = False, provider: str = PROVIDER) -> None:
    def bump(u: dict) -> dict:
        if u.get("date") != _today():
            u = {"date": _today(), "providers": {}}
        p = u.setdefault("providers", {}).setdefault(provider, {"count": 0, "exhausted": False})
        if exhausted:
            p["exhausted"] = True
        else:
            p["count"] += 1
        return u

    with _lock:
        state.update(USAGE_KEY, bump)


def size_for(template: str, per_ratio: bool | None = None) -> tuple[int, int]:
    """템플릿(비율군)에 맞는 생성 크기. per_ratio(기본 환경변수 IMAGE_BG_PER_RATIO)가 꺼졌거나 FLUX(크기 지정 없음)면
    환경변수 크기(기본 정사각). 1장 방식은 per_ratio=True 로 불러 비율군 크기를 그대로 쓴다."""
    if (PER_RATIO if per_ratio is None else per_ratio) and PROVIDER == "openai" and template in TEMPLATE_SIZES:
        return TEMPLATE_SIZES[template]
    w, h = OPENAI_SIZE.lower().split("x")
    return int(w), int(h)


def pick_style(tone: dict, brief: dict) -> tuple[str, str]:
    """배경 형태를 정한다. 브리프의 image_style 이 '자동'이 아니면 그대로, 자동이면 브랜드 톤·브리프 설명의 키워드로 고른다.
    반환: (형태 이름, 이유). 기본은 사람이 없는 '실사 제품·공간'."""
    chosen = (brief.get("image_style") or AUTO_STYLE).strip()
    if chosen in STYLES:
        return chosen, "브리프에서 지정"
    text = f"{tone.get('description') or ''} {brief.get('description') or ''}"
    for style, words in _STYLE_RULES:
        hit = next((w for w in words if w.lower() in text.lower()), None)
        if hit:
            return style, f"자동: 브랜드 톤·브리프 설명에 '{hit}'"
    return DEFAULT_STYLE, "자동: 기본값(사람 없는 제품·공간)"


# 제품 처리 방식: composite = 제품 없는 배경을 만들고 원본 누끼를 코드로 붙임(투명 누끼일 때만, 변형 없음·저렴) / reference = 누끼를 참조 이미지로 첨부해 AI 가 그리게 함
PRODUCT_MODE = os.getenv("IMAGE_PRODUCT_MODE", "composite").strip().lower()


def product_mode(product: Image.Image | None) -> str | None:
    """이 제품 이미지를 어떻게 다룰지: None(제품 없음) / 'composite' / 'reference'. 불투명 사진은 오려 붙일 수 없어 reference."""
    if product is None:
        return None
    return "composite" if PRODUCT_MODE == "composite" and compose.has_cutout(product) else "reference"


PRODUCT_ATTACH = "첨부한 이미지는 제품 원본 사진 한 장이다."
PRODUCT_ATTACH_VARIANT = ("첨부한 이미지는 두 장이다. 1번은 장면 참조, 2번은 제품 원본이다. 1번에 그려진 상품은 이전에 다시 그린 것이라 "
                          "모양이 부정확하므로 따라 그리지 말고, 1번에서는 장면·소품·조명·색만 참조한다. 상품은 반드시 2번 제품 원본을 따른다.")


def brand_color_line(colors: list[dict] | None) -> str:
    """background.md 의 {{brand_colors}} 값. 강조색(없으면 주조색)은 포인트로만, 큰 면은 브랜드 배경색(없으면 밝은 중성색).
    문장은 prompts/image/_brand_colors.md, 분기는 여기서 계산한다. 강조·주조색이 모두 없으면 빈칸(1.7 과 같은 프롬프트)."""
    by_role = {c.get("role"): c["hex"] for c in colors or [] if c.get("hex")}
    accent = by_role.get("강조색") or by_role.get("주조색")
    if not accent:
        return ""
    sub = [f"{by_role[r]}({r})" for r in ("주조색", "보조색") if r in by_role and by_role[r] != accent]
    field = (f"{by_role['배경색']}(브랜드 배경색) 계열의 색" if by_role.get("배경색")
             else "흰색, 밝은 회색, 연한 크림 같은 밝은 중성색")
    return "\n" + prompts.render("image/_brand_colors", accent=accent, field=field,
                                 sub=f"{', '.join(sub)} 는 쓰더라도 아주 작게 쓴다. " if sub else "")


def people_vars(style: str) -> dict:
    """background.md·product_slot.md 의 사람 관련 변수. 사람 없는 형태(NO_PEOPLE_STYLES)면 금지 문장, 인물 형태면 예전 문장·빈칸.
    분기는 여기서 계산해 md 에는 조건문을 두지 않는다."""
    if style in NO_PEOPLE_STYLES:
        return {"target_label": "타깃(그리지 않음, 분위기 참고)",
                "tone_people": " 톤 문장에 인물이 나와도 이 형태에서는 사람을 그리지 않고 색감·조명·질감만 따른다.",
                "repeat_people": " 사람·얼굴·손·팔 등 신체 일부는 한 명도, 한 조각도 없다.",
                "people": "사람·얼굴·손·팔·신체 일부를 화면 어디에도 그리지 않는다. 가방·옷처럼 사람이 쓰는 물건도 사람 없이 놓인 모습으로만 그린다."}
    return {"target_label": "타깃", "tone_people": "", "repeat_people": "",
            "people": "사람이 등장하는 장면이면 사람은 구역 바깥(옆이나 뒤편, 초점이 흐린 거리)에 두고, 사람의 손·팔·몸이 구역 안으로 "
                      "들어오거나 상품을 든 모습을 그리지 않는다. 구역 위쪽도 사람의 머리나 몸이 걸치지 않게 한다."}


def build_prompt(brand_name: str, tone: dict, brief: dict, template: str, style: str | None = None,
                 single: bool = False, product: bool = False, variant: bool = False, composite: bool = False,
                 colors: list[dict] | None = None, placed: dict | None = None) -> str:
    """입력 내용을 번역 없이 한국어 그대로 프롬프트로 만든다. 브랜드명·문구는 글자로 그려지지 않게 넣지 않는다.

    문장은 prompts/image/background.md, 템플릿별 구도는 prompts/image/composition/<가로형|정사각|세로형>.md,
    표현 방식(배경 형태)은 prompts/image/style/<형태>.md (style 이 None 이면 pick_style 로 정한다).
    product=True 면 제품 누끼 이미지를 참조 이미지로 함께 보내므로 prompts/image/product_ref.md 문장을 덧붙인다.
    variant=True 면 다른 비율군 배경(1번)이 제품 원본(2번) 앞에 함께 첨부되므로, 1번 속 상품은 이전에 그려진 것이라
    무시하고 제품 원본만 따르게 첨부 이미지의 역할을 적는다 (generate_variant 의 첨부 순서와 맞춘다).
    composite=True 면 제품을 이미지에 그리지 않고 제품 구역(compose.PRODUCT_SLOT)을 비워 두게 한다(product_slot.md). 제품 누끼는 생성 뒤 코드가
    그 자리에 그대로 붙이므로 참조 이미지로 첨부하지 않는다(product 는 무시).
    비율군별 생성이라도 규격 비율로 조금은 잘라(cover_crop) 쓰므로 구도 규칙은 중앙 영역에 주 피사체를 두게 한다.
    colors 는 브랜드 허용 색상(BrandVersion.colors). 주면 [브랜드 톤] 뒤에 [브랜드 색] 문장이 붙는다(brand_color_line).
    placed 는 생성 크기 기준 compose.product_box 결과. composite 일 때 주면 고정 구역 대신 누끼가 실제로 놓일 자리만 비운다(product_slot_vars).
    """
    style = style or pick_style(tone, brief)[0]
    if style not in STYLES:
        raise ValueError(f"배경 형태는 {list(STYLES)} 중 하나여야 합니다: {style}")
    target = brief.get("target") or "지정 없음"
    # single: 한 장을 모든 지면이 잘라 쓰므로 비율군 구도 대신 '어느 방향으로 잘려도 되는' 공용 구도를 쓴다
    composition = prompts.render(f"image/composition/{SINGLE_COMPOSITION if single else template}")
    style_text = prompts.render(f"image/style/{STYLES[style]}", target=target)
    # composite: 상품명·설명이 장면에 들어가면 모델이 비워 둔 구역에 같은 종류의 물건을 그린다 → 중립어로 바꾸고 설명은 뺀다
    text = prompts.render("image/background", product_name="상품(나중에 합성, 그리지 않음)" if composite else brief.get("product_name"),
                          description="" if composite else brief.get("description") or "",
                          key_message=brief.get("key_message") or "지정 없음", target=target,
                          tone=tone.get("description") or "지정 없음", composition=composition, style=style_text,
                          brand_colors=brand_color_line(colors), **people_vars(style))
    if composite:
        text = prompts.render("image/product_slot", base=text, people=people_vars(style)["people"],
                              **product_slot_vars(template, placed))
    elif product:
        attach = PRODUCT_ATTACH_VARIANT if variant else PRODUCT_ATTACH
        text = prompts.render("image/product_ref", product_name=brief.get("product_name"), attach=attach, base=text)
    return text


SLOT_MARGIN = 0.03    # 누끼 상자 바깥으로 더 비워 두는 여유(캔버스 대비, 사방). 접지 그림자·가장자리 소품 겹침 방지


def product_slot_vars(template: str, placed: dict | None = None) -> dict:
    """product_slot.md 의 {{slot}}·{{shape}}. placed(compose.product_box 결과)가 있으면 누끼가 실제로 놓일 상자에 사방 SLOT_MARGIN 을
    더한 자리와 상품 모양·바닥 높이를, 없으면 비율군 고정 구역(compose.PRODUCT_SLOT)과 빈 shape 를 준다.
    길쭉한 병이나 저해상도 누끼는 고정 구역의 일부만 차지하므로, 실제 자리만 비워야 나머지가 빈 받침으로 남지 않는다."""
    if not placed:
        x0, y0, x1, y1 = (round(v * 100) for v in compose.PRODUCT_SLOT[template])
        return {"slot": f"가로 {x0}~{x1}%, 세로 {y0}~{y1}%", "shape": ""}
    W, H = placed["canvas"]
    bx0, by0, bx1, by1 = placed["box"]
    x0, y0 = max(0.0, bx0 / W - SLOT_MARGIN), max(0.0, by0 / H - SLOT_MARGIN)
    x1, y1 = min(1.0, bx1 / W + SLOT_MARGIN), min(1.0, by1 / H + SLOT_MARGIN)
    r = (bx1 - bx0) / max(1, by1 - by0)
    kind = "세로로 긴 물건" if r < 0.8 else "가로로 넓은 물건" if r > 1.25 else "정사각에 가까운 물건"
    ratio = f"1:{1 / r:.1f}" if r < 1 else f"{r:.1f}:1"
    shape = (f" 이 자리에 놓일 상품은 {kind}(가로:세로 {ratio})으로, 화면 가로의 약 {round((bx1 - bx0) / W * 100)}%, "
             f"세로의 약 {round((by1 - by0) / H * 100)}%를 차지하며 바닥이 세로 {round(by1 / H * 100)}% 높이에 닿는다. "
             "받침의 윗면은 그 높이에 맞추고, 이 자리 바깥은 비워 두지 말고 [장면]의 공간·소품으로 자연스럽게 채운다.")
    return {"slot": f"가로 {round(x0 * 100)}~{round(x1 * 100)}%, 세로 {round(y0 * 100)}~{round(y1 * 100)}%", "shape": shape}


def prompt_blocks(text: str) -> list[dict]:
    """프롬프트를 '[태그] 본문' 블록으로 나눈다(대기 화면의 주문서 미리보기용). 태그 없이 이어지는 줄은 앞 블록에 붙인다."""
    blocks: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = re.match(r"\s*\[([^\]]+)\]\s*(.*)", line)
        if m:
            blocks.append({"tag": m.group(1).strip(), "text": m.group(2).strip()})
        elif blocks:
            blocks[-1]["text"] = f"{blocks[-1]['text']}\n{line.strip()}".strip()
        else:
            blocks.append({"tag": "", "text": line.strip()})
    return blocks


def build_variant_prompt(base_prompt: str) -> str:
    """참조 이미지(첫 비율군 배경)와 함께 보내는 변형 프롬프트. 문장은 prompts/image/same_scene.md."""
    return prompts.render("image/same_scene", base=base_prompt)


def prompt_refs(template: str, style: str, variant: bool = False, single: bool = False,
                product: bool = False, composite: bool = False, colors: list[dict] | None = None) -> list[str]:
    """결과 기록용: 이 배경을 만든 프롬프트 파일들의 이름@버전#해시."""
    refs = [prompts.ref("image/background"), prompts.ref(f"image/composition/{SINGLE_COMPOSITION if single else template}"),
            prompts.ref(f"image/style/{STYLES[style]}"), prompts.ref("image/_common"), prompts.ref("legal/_image_common")]
    if brand_color_line(colors):
        refs.append(prompts.ref("image/_brand_colors"))
    refs = ([prompts.ref("image/same_scene")] + refs) if variant else refs
    if composite:
        return [prompts.ref("image/product_slot")] + refs
    return ([prompts.ref("image/product_ref")] + refs) if product else refs


def _openai_png(prompt: str, model: str, quality: str, size: str = OPENAI_SIZE, kind: str = "background",
                **extra) -> bytes:
    """OpenAI 이미지 1장을 PNG 바이트로. extra 는 background·output_format 등 추가 인자. kind 는 호출 기록용."""
    import openai
    try:
        with calllog.timed("images.generate", kind, "openai", model, size=size, quality=quality) as info:
            info["prompt_text"] = {"prompt": prompt}
            r = client().with_options(timeout=240.0, max_retries=0).images.generate(
                model=model, prompt=prompt, size=size, quality=quality, n=1, **extra)
            calllog.note_image_usage(info, r)      # 토큰 사용량 → 실제 비용 계산(pricing)
    except openai.RateLimitError as e:   # 분당 제한이거나 크레딧 부족. 하루 소진으로 기록하지 않는다
        code = getattr(e, "code", None) or ""
        if "quota" in str(code) or "billing" in str(e).lower():
            raise QuotaExceeded("OpenAI 크레딧(결제 한도)이 부족합니다. OpenAI 결제 설정을 확인해 주세요")
        raise QuotaExceeded("OpenAI 요청 한도에 걸렸습니다. 잠시 후 다시 시도해 주세요")
    _bump(provider="openai")
    return base64.b64decode(r.data[0].b64_json)


def _generate_openai(prompt: str, size: str = OPENAI_SIZE, quality: str = OPENAI_QUALITY,
                     model: str = OPENAI_MODEL, kind: str = "background") -> Image.Image:
    return Image.open(io.BytesIO(_openai_png(prompt, model, quality, size=size, kind=kind))).convert("RGB")


def pricing_rate() -> float:
    from . import pricing
    return pricing.USD_KRW


def model_options() -> list[dict]:
    """프론트 선택용: 모델별 정사각 1장의 추정 비용. 요금표에 없는 모델은 estimated=True."""
    from . import pricing
    out = []
    for m in MODELS:
        row = {"ok": True, "api": "images.generate", "provider": "openai", "model": m, "size": "1024x1024"}
        usd, est = pricing.cost_of({**row, "quality": OPENAI_QUALITY})
        out.append({"model": m, "default": m == OPENAI_MODEL, "usd": usd, "estimated": est,
                    "input_fidelity": fidelity_supported(m)})
    return out


def generate_transparent(prompt: str, model: str | None = None, quality: str | None = None,
                         kind: str = "icon", size: str | None = None) -> Image.Image:
    """투명 배경(알파) PNG 1장 — 3D 아이콘용. 공급자는 OpenAI 고정(FLUX 는 투명 출력이 없다).
    일일 한도·크레딧 처리는 배경 생성과 같고, 카운터는 openai 로 기록한다."""
    if usage("openai")["exhausted"]:
        raise QuotaExceeded(f"오늘 설정한 이미지 생성 한도({DAILY_LIMIT}회)를 모두 사용했습니다. "
                            "한도를 올리려면 IMAGE_DAILY_LIMIT 을 조정하세요")
    data = _openai_png(prompt, model or OPENAI_MODEL, quality or OPENAI_QUALITY, size=size or OPENAI_SIZE, kind=kind,
                       background="transparent", output_format="png")
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _generate_cloudflare(prompt: str) -> Image.Image:
    """FLUX schnell 은 크기 지정이 없어 정사각으로 나온다."""
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not account or not token:
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN 이 .env 에 없습니다")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{CF_MODEL}"
    with calllog.timed("images.generate", "background", "cloudflare", CF_MODEL) as info:
        info["prompt_text"] = {"prompt": prompt[:CF_MAX_PROMPT_CHARS]}
        r = httpx.post(url, headers={"Authorization": f"Bearer {token}"}, timeout=120.0,
                       json={"prompt": prompt[:CF_MAX_PROMPT_CHARS], "steps": CF_STEPS})
    # 429 또는 일일 무료 할당량 초과 코드(4006)
    if r.status_code == 429 or (r.status_code in (400, 403) and "4006" in r.text):
        _bump(exhausted=True)
        raise QuotaExceeded("Workers AI 일일 무료 한도를 초과했습니다. 내일(UTC 0시, 한국시간 오전 9시) 이후 다시 시도해 주세요")
    r.raise_for_status()
    _bump()
    data = base64.b64decode(r.json()["result"]["image"])
    return Image.open(io.BytesIO(data)).convert("RGB")


def can_edit() -> bool:
    """참조 이미지를 붙이는 images.edit 을 쓸 수 있는 공급자인가(OpenAI). 비율군 변형·제품 누끼 참조에 쓴다."""
    return PROVIDER == "openai"


def can_vary(bg_mode: str | None = None) -> bool:
    """다른 비율군을 참조 변형(images.edit)으로 만들 수 있는가. 공급자가 OpenAI + 비율군별 방식 + 변형 켜짐.
    1장 방식(single)은 비율군 변형이 없다(잘라 쓴다)."""
    mode = bg_mode or default_bg_mode()
    return VARIANT and mode == "per_ratio" and PROVIDER == "openai"


# 모델이 input_fidelity 를 거절한 사실을 모델별로 파일에 기억한다. 메모리 플래그만 쓰면 서버를 재시작할 때마다
# 첫 변형 호출이 한 번 거절당한 뒤(과금 없음, 약 2초) 재시도하므로, 프로세스가 바뀌어도 처음부터 보내지 않게 한다.
CAPS_KEY = "model_caps"
_caps_cache: dict | None = None


def _caps() -> dict:
    """{모델명: {"input_fidelity": bool}}. 파일이 없거나 깨졌으면 빈 dict."""
    global _caps_cache
    if _caps_cache is None:
        _caps_cache = state.get(CAPS_KEY) or {}
        if not isinstance(_caps_cache, dict):
            _caps_cache = {}
    return _caps_cache


def fidelity_supported(model: str = OPENAI_MODEL) -> bool:
    """이 모델에 input_fidelity 를 보내도 되는가. 거절 기록이 없으면 True(한 번 보내 보고 거절되면 기록한다)."""
    return (_caps().get(model) or {}).get("input_fidelity", True) is not False


def _remember_fidelity(model: str, supported: bool) -> None:
    caps = _caps()
    caps[model] = {**(caps.get(model) or {}), "input_fidelity": supported}
    try:
        state.put(CAPS_KEY, caps)
    except Exception:
        pass                      # 기록 실패는 다음 프로세스에서 한 번 더 거절당하는 것 외에 영향이 없다


def error_info(e: Exception) -> dict:
    """OpenAI 오류의 {message, param, code}. SDK 는 응답의 error 안쪽 dict 를 e.body 로 주지만(래퍼 없음), 래퍼가 있는 경우도 받는다."""
    body = getattr(e, "body", None)
    if not isinstance(body, dict):
        return {}
    inner = body.get("error")
    return inner if isinstance(inner, dict) else body


def _is_fidelity_error(e: Exception) -> bool:
    err = error_info(e)
    return err.get("param") == "input_fidelity" or "input_fidelity" in str(err.get("message") or "")


def generate_variant(prompt: str, w: int, h: int, reference: Image.Image | None,
                     quality: str = OPENAI_QUALITY, model: str | None = None,
                     product: Image.Image | None = None, kind: str | None = None) -> Image.Image:
    """참조 이미지와 같은 장면을 (w, h) 크기·다른 구도로 다시 만든다 (OpenAI images.edit). 비용·한도는 생성 1장과 같다.
    reference 는 다른 비율군의 배경(없으면 None), product 는 제품 누끼 이미지(투명 PNG, 없으면 None).
    둘 다 주면 [배경, 제품] 순서로 첨부한다. 하나는 있어야 한다. kind 는 호출 기록용(없으면 variant/product)."""
    import openai
    if usage("openai")["exhausted"]:
        raise QuotaExceeded(f"오늘 설정한 이미지 생성 한도({DAILY_LIMIT}회)를 모두 사용했습니다. "
                            "한도를 올리려면 IMAGE_DAILY_LIMIT 을 조정하세요")
    files = []
    for name, img, mode in (("reference.png", reference, "RGB"), ("product.png", product, "RGBA")):
        if img is not None:
            b = io.BytesIO()
            img.convert(mode).save(b, format="PNG")
            files.append((name, b.getvalue(), "image/png"))
    if not files:
        raise ValueError("참조 이미지나 제품 이미지가 필요합니다")
    model = model or OPENAI_MODEL
    extra = {"input_fidelity": VARIANT_FIDELITY} if (VARIANT_FIDELITY and fidelity_supported(model)) else {}
    def _edit(**kw):
        k = kind or ("variant" if reference is not None else "product")
        with calllog.timed("images.edit", k, "openai", model, size=f"{w}x{h}", quality=quality) as info:
            info["prompt_text"] = {"prompt": prompt}
            r = client().with_options(timeout=240.0, max_retries=0).images.edit(
                model=model, image=files if len(files) > 1 else files[0], prompt=prompt, size=f"{w}x{h}", quality=quality, n=1, **kw)
            calllog.note_image_usage(info, r)
            return r
    try:
        try:
            r = _edit(**extra)
        except openai.BadRequestError as e:
            # 일부 모델(gpt-image-2.5 계열)은 input_fidelity 를 받지 않는다 → 그 인자만 빼고 한 번 더 (검증 단계 실패라 과금 없음)
            if extra and _is_fidelity_error(e):
                _remember_fidelity(model, False)      # 이후 호출·다음 프로세스부터는 처음부터 보내지 않는다
                r = _edit()
            else:
                raise
    except openai.RateLimitError as e:
        code = getattr(e, "code", None) or ""
        if "quota" in str(code) or "billing" in str(e).lower():
            raise QuotaExceeded("OpenAI 크레딧(결제 한도)이 부족합니다. OpenAI 결제 설정을 확인해 주세요")
        raise QuotaExceeded("OpenAI 요청 한도에 걸렸습니다. 잠시 후 다시 시도해 주세요")
    _bump(provider="openai")
    return Image.open(io.BytesIO(base64.b64decode(r.data[0].b64_json))).convert("RGB")


def generate_background(prompt: str, w: int, h: int, quality: str = OPENAI_QUALITY,
                        model: str | None = None, provider: str | None = None, kind: str = "background") -> Image.Image:
    """공급자별 생성. kind 는 호출 기록용(OpenAI, 핀 뱃지는 'pin'). (w, h) 는 생성 크기(size_for 결과). 지면 비율의 나머지 차이는 compose.cover_crop 이 맞춘다.
    quality·model 은 OpenAI 에만 적용된다(FLUX 는 품질 단계·크기 지정이 없어 정사각으로 나온다). provider 기본은 PROVIDER."""
    provider = (provider or PROVIDER).strip().lower()
    if provider not in ("openai", "cloudflare"):
        raise RuntimeError(f"이미지 공급자 값이 올바르지 않습니다 ({provider}). openai 또는 cloudflare")
    if usage(provider)["exhausted"]:
        raise QuotaExceeded(f"오늘 설정한 이미지 생성 한도({DAILY_LIMIT}회)를 모두 사용했습니다. "
                            "한도를 올리려면 IMAGE_DAILY_LIMIT 을 조정하세요")
    return (_generate_openai(prompt, f"{w}x{h}", quality, model or OPENAI_MODEL, kind=kind) if provider == "openai"
            else _generate_cloudflare(prompt))
