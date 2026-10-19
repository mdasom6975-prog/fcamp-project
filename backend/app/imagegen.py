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

from . import prompts
from .copywriter import BASE_DIR, client  # (load_dotenv 가 copywriter import 시점에 실행된다)

PROVIDER = os.getenv("IMAGE_PROVIDER", "openai").strip().lower()      # openai | cloudflare
# OpenAI 기본값: gpt-image-1.5 + medium 품질(비교 테스트 결과 반영, 장당 약 60원)
# 정사각 1024 로 만들고 지면 비율은 cover_crop 이 맞춘다
OPENAI_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1.5")
OPENAI_QUALITY = os.getenv("IMAGE_QUALITY", "medium")                 # low | medium | high | auto
OPENAI_SIZE = os.getenv("IMAGE_SIZE", "1024x1024")
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


def usage() -> dict:
    with _lock:
        u = _load()
    p = u["providers"].get(PROVIDER, {})
    used = p.get("count", 0)
    return {"date": u["date"], "provider": PROVIDER, "used": used, "limit": DAILY_LIMIT,
            "remaining": max(0, DAILY_LIMIT - used), "exhausted": bool(p.get("exhausted")) or used >= DAILY_LIMIT}


def _bump(exhausted: bool = False) -> None:
    with _lock:
        u = _load()
        p = u["providers"].setdefault(PROVIDER, {"count": 0, "exhausted": False})
        if exhausted:
            p["exhausted"] = True
        else:
            p["count"] += 1
        _save(u)


def build_prompt(brand_name: str, tone: dict, brief: dict, template: str) -> str:
    """입력 내용을 번역 없이 한국어 그대로 프롬프트로 만든다. 브랜드명·문구는 글자로 그려지지 않게 넣지 않는다.

    문장은 prompts/image/background.md, 템플릿별 구도는 prompts/image/composition/<가로형|정사각|세로형>.md.
    한 장을 규격 비율로 잘라(cover_crop) 쓰므로 구도 규칙은 잘려도 남는 중앙 영역에 주 피사체를 두게 한다.
    """
    composition = prompts.render(f"image/composition/{template}")
    return prompts.render("image/background", product_name=brief.get("product_name"),
                          description=brief.get("description") or "",
                          key_message=brief.get("key_message") or "지정 없음", target=brief.get("target") or "지정 없음",
                          tone=tone.get("description") or "지정 없음", composition=composition)


def _generate_openai(prompt: str) -> Image.Image:
    import openai
    try:
        r = client().with_options(timeout=240.0, max_retries=0).images.generate(
            model=OPENAI_MODEL, prompt=prompt, size=OPENAI_SIZE, quality=OPENAI_QUALITY, n=1)
    except openai.RateLimitError as e:   # 분당 제한이거나 크레딧 부족. 하루 소진으로 기록하지 않는다
        code = getattr(e, "code", None) or ""
        if "quota" in str(code) or "billing" in str(e).lower():
            raise QuotaExceeded("OpenAI 크레딧(결제 한도)이 부족합니다. OpenAI 결제 설정을 확인해 주세요")
        raise QuotaExceeded("OpenAI 요청 한도에 걸렸습니다. 잠시 후 다시 시도해 주세요")
    _bump()
    data = base64.b64decode(r.data[0].b64_json)
    return Image.open(io.BytesIO(data)).convert("RGB")


def _generate_cloudflare(prompt: str) -> Image.Image:
    """FLUX schnell 은 크기 지정이 없어 정사각으로 나온다."""
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    token = os.getenv("CLOUDFLARE_API_TOKEN")
    if not account or not token:
        raise RuntimeError("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN 이 .env 에 없습니다")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{CF_MODEL}"
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


def generate_background(prompt: str, w: int, h: int) -> Image.Image:
    """공급자별 생성. 지면 비율 맞춤(리사이즈)은 compose.cover_crop 이 맡는다."""
    if PROVIDER not in ("openai", "cloudflare"):
        raise RuntimeError(f"IMAGE_PROVIDER 값이 올바르지 않습니다 ({PROVIDER}). openai 또는 cloudflare")
    if usage()["exhausted"]:
        raise QuotaExceeded(f"오늘 설정한 이미지 생성 한도({DAILY_LIMIT}회)를 모두 사용했습니다. "
                            "한도를 올리려면 IMAGE_DAILY_LIMIT 을 조정하세요")
    return _generate_openai(prompt) if PROVIDER == "openai" else _generate_cloudflare(prompt)
