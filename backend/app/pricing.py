"""외부 모델 호출의 추정 비용. 개발자가 화면에서 호출 횟수와 함께 보고 절감 효과를 비교하기 위한 값이다.

요금은 공급자 요금표가 바뀌므로 **추정치**로 취급한다. backend/data/pricing.json 을 두면 그 내용으로 덮어쓴다
(모델명 키 → 같은 구조). 환율은 USD_KRW(기본 1400).

이미지(OpenAI gpt-image 계열)는 장당이 아니라 토큰으로 과금된다(텍스트 입력·이미지 입력·이미지 출력, 100만 토큰당 USD).
호출 기록에 토큰(tokens_out 등, calllog.note_image_usage)이 있으면 image_tokens 표로 실제 비용을 계산하고,
토큰이 없는 예전 호출만 image 표(모델·품질·크기별 장당, gpt-image-1 공개 요금 기준)로 추정한다."""
import json
import os

from .db import BASE_DIR

PRICING_FILE = BASE_DIR / "data" / "pricing.json"
USD_KRW = float(os.getenv("USD_KRW", "1400"))

# 이미지: 모델 → 품질 → 크기 → 장당 USD. images.edit 도 같은 값으로 본다(입력 이미지 토큰은 무시).
# 모델명이 표에 없으면 'gpt-image*' 는 gpt-image-1 요율로 추정한다.
_IMAGE_1 = {
    "low": {"1024x1024": 0.011, "1024x1536": 0.016, "1536x1024": 0.016},
    "medium": {"1024x1024": 0.042, "1024x1536": 0.063, "1536x1024": 0.063},
    "high": {"1024x1024": 0.167, "1024x1536": 0.25, "1536x1024": 0.25},
}
# 이미지 토큰 단가(100만 토큰당 USD, OpenAI 표준 처리 기준 — developers.openai.com/api/docs/pricing 2026-10).
# text_in: 프롬프트 텍스트 토큰, image_in: 참조 이미지(edit) 토큰, image_out: 생성 이미지 토큰. '*' 는 표에 없는 gpt-image 모델의 추정.
_IMAGE_TOKENS = {
    "gpt-image-2.5-flare": {"text_in": 5.00, "image_in": 8.00, "image_out": 30.00},
    "gpt-image-2.5-sunburst": {"text_in": 5.00, "image_in": 8.00, "image_out": 30.00},
    "gpt-image-1.5": {"text_in": 5.00, "image_in": 8.00, "image_out": 32.00},
    "gpt-image-1": {"text_in": 5.00, "image_in": 10.00, "image_out": 40.00},
    "*": {"text_in": 5.00, "image_in": 8.00, "image_out": 30.00},
}
DEFAULT = {
    "image": {"gpt-image-1": _IMAGE_1, "gpt-image-1.5": _IMAGE_1, "*": _IMAGE_1},
    "image_tokens": _IMAGE_TOKENS,
    # 채팅: 모델 → {in, out} = 100만 토큰당 USD. '*' 는 표에 없는 모델의 추정 요율(소형 모델 기준).
    "chat": {"gpt-4.1-mini": {"in": 0.40, "out": 1.60}, "gpt-4o-mini": {"in": 0.15, "out": 0.60},
             "gpt-4.1": {"in": 2.00, "out": 8.00},
             # Claude (Anthropic 1차 API 요금, 100만 토큰당 USD)
             "claude-opus-5-5": {"in": 4.00, "out": 20.00}, "claude-sonnet-5-5": {"in": 2.00, "out": 10.00},
             "claude-haiku-5-5": {"in": 0.10, "out": 0.50}, "claude-opus-5": {"in": 5.00, "out": 25.00},
             "*": {"in": 0.40, "out": 1.60}},
    # 무료 구간을 쓰는 공급자(장당 0)
    "free_providers": ["cloudflare"],
}


def table() -> dict:
    try:
        user = json.loads(PRICING_FILE.read_text())
    except (OSError, ValueError):
        return DEFAULT
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULT.items()}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def _lookup(section: dict, model: str) -> tuple[dict | None, bool]:
    """(요율, 추정 여부). 모델명이 표에 없으면 '*' 요율을 쓰고 추정으로 표시한다."""
    if model in section:
        return section[model], False
    return section.get("*"), True


def cost_of(row: dict) -> tuple[float | None, bool]:
    """호출 기록 1건의 USD 비용과 추정 여부. 요율을 알 수 없으면 (None, True). 실패한 호출은 0 으로 본다
    (요청 검증 단계 실패는 과금되지 않는다; 생성 중 실패는 드물어 무시한다)."""
    if not row.get("ok"):
        return 0.0, False
    t = table()
    if row.get("provider") in t.get("free_providers", []):
        return 0.0, False
    api, model = row.get("api") or "", row.get("model") or ""
    if api.startswith("images"):
        if row.get("tokens_out") is not None:      # 토큰이 기록된 호출: 실제 과금 방식(토큰×단가)으로 계산
            rates, est = _lookup(t.get("image_tokens") or {}, model)
            if rates:
                tin = float(row.get("tokens_in") or 0)
                text_in = row.get("tokens_text_in"); image_in = row.get("tokens_image_in")
                if text_in is None and image_in is None:   # 내역이 없으면 edit 은 참조 이미지, generate 는 텍스트로 본다
                    text_in, image_in = (0, tin) if api == "images.edit" else (tin, 0)
                usd = (float(text_in or 0) * rates["text_in"] + float(image_in or 0) * rates["image_in"]
                       + float(row["tokens_out"]) * rates["image_out"]) / 1_000_000
                return usd, est
        rates, est = _lookup(t["image"], model)
        if not rates:
            return None, True
        size = row.get("size") or "1024x1024"
        q = rates.get(row.get("quality") or "medium") or rates.get("medium") or {}
        price = q.get(size)
        if price is None:          # 표에 없는 크기: 정사각 요율로 추정
            price, est = q.get("1024x1024"), True
        return (float(price) if price is not None else None), est or price is None
    if api == "chat":
        rates, est = _lookup(t["chat"], model)
        if not rates:
            return None, True
        tin, tout = row.get("tokens_in"), row.get("tokens_out")
        if tin is None and tout is None:
            return None, True       # 토큰을 기록하지 않은 예전 호출
        return (float(tin or 0) * rates["in"] + float(tout or 0) * rates["out"]) / 1_000_000, est
    return None, True


def krw(usd: float | None) -> int | None:
    return None if usd is None else round(usd * USD_KRW)
