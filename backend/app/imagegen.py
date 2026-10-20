"""배경 이미지 생성. IMAGE_PROVIDER 로 OpenAI(기본) / Cloudflare Workers AI(FLUX.1 schnell)를 전환한다.
텍스트·로고는 그리지 않게 하고 합성은 compose 에서 한다."""
import base64
import io
import json
import os
import threading
from datetime import datetime, timezone

import httpx
from PIL import Image

from . import calllog, prompts
from .copywriter import BASE_DIR, client  # (load_dotenv 가 copywriter import 시점에 실행된다)

PROVIDER = os.getenv("IMAGE_PROVIDER", "openai").strip().lower()      # openai | cloudflare
# OpenAI 기본값: gpt-image-1.5 + medium 품질(비교 테스트 결과 반영, 정사각 장당 약 60원)
OPENAI_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1.5")
OPENAI_QUALITY = os.getenv("IMAGE_QUALITY", "medium")                 # low | medium | high | auto — 확정 품질(내보낼 배경)
# 초안 품질: 배경 형태·구도를 고르는 동안 쓰는 값. 마음에 들면 '확정 품질로 배경 생성' 1회만 OPENAI_QUALITY 로 만든다.
# low 는 medium 의 약 1/4 비용(gpt-image 계열). 같은 값으로 두면 초안/확정 구분이 사라진다.
DRAFT_QUALITY = os.getenv("IMAGE_QUALITY_DRAFT", "low")
OPENAI_SIZE = os.getenv("IMAGE_SIZE", "1024x1024")                    # 비율군별 생성을 끈 경우(또는 3D 아이콘)의 크기
# 배경을 비율군(가로형/정사각/세로형)마다 모델 지원 크기로 따로 만든다. 0 이면 예전처럼 정사각 1장을 모든 규격이 공유.
PER_RATIO = os.getenv("IMAGE_BG_PER_RATIO", "1") != "0"
# 비율군이 여럿이면 첫 비율군 배경을 참조 이미지로 붙여(images.edit) 나머지를 '같은 장면'으로 만든다. 0 이면 비율군마다 독립 생성.
VARIANT = os.getenv("IMAGE_BG_VARIANT", "1") != "0"
VARIANT_FIDELITY = os.getenv("IMAGE_VARIANT_FIDELITY", "high")      # high | low — 참조 이미지를 얼마나 그대로 지킬지
# 템플릿 → gpt-image 지원 크기. 비정사각은 픽셀이 1.5배라 출력 토큰(비용)도 약 1.5배다.
TEMPLATE_SIZES = {"세로형": (1024, 1536), "가로형": (1536, 1024), "정사각": (1024, 1024)}
# 배경 형태. 키는 사용자에게 보이는 이름, 값은 prompts/image/style/<파일>.md
STYLES = {"실사 인물": "실사인물", "실사 제품·공간": "실사제품", "플랫 일러스트": "플랫일러스트",
          "3D 렌더": "3D렌더", "그래픽 패턴": "그래픽패턴"}
AUTO_STYLE = "자동"
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
DAILY_LIMIT = int(os.getenv("IMAGE_DAILY_LIMIT", "150"))
USAGE_FILE = BASE_DIR / "data" / "image_usage.json"
_lock = threading.Lock()


class QuotaExceeded(Exception):
    """일일 한도 또는 공급자 한도/크레딧 초과. 메시지는 그대로 사용자에게 보여 준다."""


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load() -> dict:
    """{date, providers: {이름: {count, exhausted}}}. 날짜가 바뀌면 리셋하고, 옛 형식은 cloudflare 로 이관한다."""
    try:
        u = json.loads(USAGE_FILE.read_text())
    except (OSError, ValueError):
        u = {}
    if u.get("date") != _today():
        return {"date": _today(), "providers": {}}
    if "providers" not in u:
        u = {"date": u["date"], "providers": {"cloudflare": {"count": u.get("count", 0),
                                                              "exhausted": u.get("exhausted", False)}}}
    return u


def _save(u: dict) -> None:
    USAGE_FILE.parent.mkdir(exist_ok=True)
    USAGE_FILE.write_text(json.dumps(u))


def usage(provider: str = PROVIDER) -> dict:
    with _lock:
        u = _load()
    p = u["providers"].get(provider, {})
    used = p.get("count", 0)
    return {"date": u["date"], "provider": provider, "used": used, "limit": DAILY_LIMIT,
            "remaining": max(0, DAILY_LIMIT - used), "exhausted": bool(p.get("exhausted")) or used >= DAILY_LIMIT}


def _bump(exhausted: bool = False, provider: str = PROVIDER) -> None:
    with _lock:
        u = _load()
        p = u["providers"].setdefault(provider, {"count": 0, "exhausted": False})
        if exhausted:
            p["exhausted"] = True
        else:
            p["count"] += 1
        _save(u)


def size_for(template: str) -> tuple[int, int]:
    """템플릿(비율군)에 맞는 생성 크기. 비율군별 생성을 껐거나 FLUX(크기 지정 없음)면 환경변수 크기(기본 정사각)."""
    if PER_RATIO and PROVIDER == "openai" and template in TEMPLATE_SIZES:
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


def build_prompt(brand_name: str, tone: dict, brief: dict, template: str, style: str | None = None) -> str:
    """입력 내용을 번역 없이 한국어 그대로 프롬프트로 만든다. 브랜드명·문구는 글자로 그려지지 않게 넣지 않는다.

    문장은 prompts/image/background.md, 템플릿별 구도는 prompts/image/composition/<가로형|정사각|세로형>.md,
    표현 방식(배경 형태)은 prompts/image/style/<형태>.md (style 이 None 이면 pick_style 로 정한다).
    비율군별 생성이라도 규격 비율로 조금은 잘라(cover_crop) 쓰므로 구도 규칙은 중앙 영역에 주 피사체를 두게 한다.
    """
    style = style or pick_style(tone, brief)[0]
    if style not in STYLES:
        raise ValueError(f"배경 형태는 {list(STYLES)} 중 하나여야 합니다: {style}")
    target = brief.get("target") or "지정 없음"
    composition = prompts.render(f"image/composition/{template}")
    style_text = prompts.render(f"image/style/{STYLES[style]}", target=target)
    return prompts.render("image/background", product_name=brief.get("product_name"),
                          description=brief.get("description") or "",
                          key_message=brief.get("key_message") or "지정 없음", target=target,
                          tone=tone.get("description") or "지정 없음", composition=composition, style=style_text)


def build_variant_prompt(base_prompt: str) -> str:
    """참조 이미지(첫 비율군 배경)와 함께 보내는 변형 프롬프트. 문장은 prompts/image/same_scene.md."""
    return prompts.render("image/same_scene", base=base_prompt)


def prompt_refs(template: str, style: str, variant: bool = False) -> list[str]:
    """결과 기록용: 이 배경을 만든 프롬프트 파일들의 이름@버전#해시."""
    refs = [prompts.ref("image/background"), prompts.ref(f"image/composition/{template}"),
            prompts.ref(f"image/style/{STYLES[style]}"), prompts.ref("image/_common")]
    return ([prompts.ref("image/same_scene")] + refs) if variant else refs


def _openai_png(prompt: str, model: str, quality: str, size: str = OPENAI_SIZE, kind: str = "background",
                **extra) -> bytes:
    """OpenAI 이미지 1장을 PNG 바이트로. extra 는 background·output_format 등 추가 인자. kind 는 호출 기록용."""
    import openai
    try:
        with calllog.timed("images.generate", kind, "openai", model, size=size, quality=quality):
            r = client().with_options(timeout=240.0, max_retries=0).images.generate(
                model=model, prompt=prompt, size=size, quality=quality, n=1, **extra)
    except openai.RateLimitError as e:   # 분당 제한이거나 크레딧 부족. 하루 소진으로 기록하지 않는다
        code = getattr(e, "code", None) or ""
        if "quota" in str(code) or "billing" in str(e).lower():
            raise QuotaExceeded("OpenAI 크레딧(결제 한도)이 부족합니다. OpenAI 결제 설정을 확인해 주세요")
        raise QuotaExceeded("OpenAI 요청 한도에 걸렸습니다. 잠시 후 다시 시도해 주세요")
    _bump(provider="openai")
    return base64.b64decode(r.data[0].b64_json)


def quality_for(final: bool) -> str:
    """배경 생성 품질. final 이면 확정(OPENAI_QUALITY), 아니면 초안(DRAFT_QUALITY)."""
    return OPENAI_QUALITY if final else DRAFT_QUALITY


def is_draft(quality: str | None) -> bool:
    """기록된 품질이 확정 품질보다 낮은가. 품질을 모르던 예전 배경(None)은 초안으로 보지 않는다."""
    return quality is not None and quality != OPENAI_QUALITY


def _generate_openai(prompt: str, size: str = OPENAI_SIZE, quality: str = OPENAI_QUALITY) -> Image.Image:
    return Image.open(io.BytesIO(_openai_png(prompt, OPENAI_MODEL, quality, size=size))).convert("RGB")


def generate_transparent(prompt: str, model: str | None = None, quality: str | None = None,
                         kind: str = "icon") -> Image.Image:
    """투명 배경(알파) PNG 1장 — 3D 아이콘용. 공급자는 OpenAI 고정(FLUX 는 투명 출력이 없다).
    일일 한도·크레딧 처리는 배경 생성과 같고, 카운터는 openai 로 기록한다."""
    if usage("openai")["exhausted"]:
        raise QuotaExceeded(f"오늘 설정한 이미지 생성 한도({DAILY_LIMIT}회)를 모두 사용했습니다. "
                            "한도를 올리려면 IMAGE_DAILY_LIMIT 을 조정하세요")
    data = _openai_png(prompt, model or OPENAI_MODEL, quality or OPENAI_QUALITY, kind=kind,
                       background="transparent", output_format="png")
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _generate_cloudflare(prompt: str) -> Image.Image:
    """FLUX schnell 은 크기 지정이 없어 정사각으로 나온다."""
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not account or not token:
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN 이 .env 에 없습니다")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{CF_MODEL}"
    with calllog.timed("images.generate", "background", "cloudflare", CF_MODEL):
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


def can_vary() -> bool:
    """참조 이미지 변형(images.edit)을 쓸 수 있는가. OpenAI 공급자 + 비율군별 생성 + 변형 켜짐."""
    return VARIANT and PER_RATIO and PROVIDER == "openai"


_fidelity_supported = True   # 모델이 input_fidelity 를 거절하면 False 로 바꿔 이후 호출에서는 보내지 않는다


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


def generate_variant(prompt: str, w: int, h: int, reference: Image.Image,
                     quality: str = OPENAI_QUALITY) -> Image.Image:
    """참조 이미지와 같은 장면을 (w, h) 크기·다른 구도로 다시 만든다 (OpenAI images.edit). 비용·한도는 생성 1장과 같다."""
    import openai
    if usage("openai")["exhausted"]:
        raise QuotaExceeded(f"오늘 설정한 이미지 생성 한도({DAILY_LIMIT}회)를 모두 사용했습니다. "
                            "한도를 올리려면 IMAGE_DAILY_LIMIT 을 조정하세요")
    buf = io.BytesIO()
    reference.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    buf.name = "reference.png"
    global _fidelity_supported
    extra = {"input_fidelity": VARIANT_FIDELITY} if (VARIANT_FIDELITY and _fidelity_supported) else {}
    def _edit(**kw):
        with calllog.timed("images.edit", "variant", "openai", OPENAI_MODEL, size=f"{w}x{h}", quality=quality):
            return client().with_options(timeout=240.0, max_retries=0).images.edit(
                model=OPENAI_MODEL, image=buf, prompt=prompt, size=f"{w}x{h}", quality=quality, n=1, **kw)
    try:
        try:
            r = _edit(**extra)
        except openai.BadRequestError as e:
            # 일부 모델(gpt-image-2.5 계열)은 input_fidelity 를 받지 않는다 → 그 인자만 빼고 한 번 더 (검증 단계 실패라 과금 없음)
            if extra and _is_fidelity_error(e):
                _fidelity_supported = False
                buf.seek(0)
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


def generate_background(prompt: str, w: int, h: int, quality: str = OPENAI_QUALITY) -> Image.Image:
    """공급자별 생성. (w, h) 는 생성 크기(size_for 결과). 지면 비율의 나머지 차이는 compose.cover_crop 이 맞춘다.
    quality 는 OpenAI 에만 적용된다(FLUX 는 품질 단계가 없다)."""
    if PROVIDER not in ("openai", "cloudflare"):
        raise RuntimeError(f"IMAGE_PROVIDER 값이 올바르지 않습니다 ({PROVIDER}). openai 또는 cloudflare")
    if usage()["exhausted"]:
        raise QuotaExceeded(f"오늘 설정한 이미지 생성 한도({DAILY_LIMIT}회)를 모두 사용했습니다. "
                            "한도를 올리려면 IMAGE_DAILY_LIMIT 을 조정하세요")
    return _generate_openai(prompt, f"{w}x{h}", quality) if PROVIDER == "openai" else _generate_cloudflare(prompt)
