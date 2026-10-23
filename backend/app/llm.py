"""텍스트 모델(채팅) 호출. 카피·아이콘 기능명처럼 JSON 을 받는 호출을 한 곳에서 처리하고 공급자를 바꿀 수 있게 한다.

- CHAT_PROVIDER=anthropic (기본): Claude API. output_config.format(json_schema)으로 스키마에 맞는 JSON 을 보장받는다.
  모델은 CLAUDE_MODEL(기본 claude-sonnet-5-5), 생각 깊이는 CLAUDE_EFFORT(기본 low — 카피·기능명은 단순 생성이라 충분하다).
  안전 분류기 거절(stop_reason refusal)은 서버 측 fallback(fallbacks="default")으로 다른 모델이 이어 받는다.
- CHAT_PROVIDER=openai: 예전 경로(chat.completions + json_object). 모델은 CHAT_MODEL.
이미지 생성(gpt-image·FLUX)은 여기 해당 없음 — Claude 는 이미지를 만들지 않는다(imagegen.py 그대로).
"""
import json
import os

from . import calllog

CHAT_PROVIDER = os.getenv("CHAT_PROVIDER", "anthropic").strip().lower()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "low")          # low | medium | high | xhigh | max
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "16000"))
_anthropic_client = None


class ChatRefused(RuntimeError):
    """모델이 요청을 거절했다(안전 분류기). 사용자에게 그대로 보여 줄 수 있는 문구."""


# 종류별 기본 모델: 짧은 구조화 생성은 Haiku(Sonnet 과 같은 시험 통과율, 비용 약 1/8 — CHANGELOG 참조). judge 등 나머지는 CLAUDE_MODEL
KIND_DEFAULTS = {"copy": "claude-haiku-5-5", "copy_fit": "claude-haiku-5-5", "icon_names": "claude-haiku-5-5"}


def model_for(kind: str | None = None) -> str:
    """호출 종류(copy, copy_fit, icon_names, icon_redraw, judge …)별 Claude 모델. 환경변수 CLAUDE_MODEL_<종류 대문자>
    (예: CLAUDE_MODEL_COPY=claude-sonnet-5-5)가 있으면 그 값, 없으면 KIND_DEFAULTS, 그것도 없으면 CLAUDE_MODEL. 짧은 구조화 생성은 작은 모델로 비용을 줄이고
    이미지를 보는 채점은 큰 모델을 두는 식으로 종류마다 따로 고를 수 있다."""
    return (os.getenv(f"CLAUDE_MODEL_{kind.upper()}", "").strip() if kind else "") or KIND_DEFAULTS.get(kind or "") or CLAUDE_MODEL


def chat_model(kind: str | None = None) -> str:
    """기록·요금 계산용 현재 채팅 모델 이름. kind 를 주면 그 종류에 쓰는 모델."""
    if CHAT_PROVIDER == "anthropic":
        return model_for(kind)
    from .copywriter import CHAT_MODEL
    return CHAT_MODEL


def anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(timeout=120.0)      # ANTHROPIC_API_KEY 는 환경변수/.env 에서 읽는다
    return _anthropic_client


def _claude_json(system: str, user: str, schema: dict, images: list[bytes] | None = None,
                 model: str | None = None) -> tuple[dict, object]:
    """Claude 에 JSON 스키마 출력을 요청한다. 반환: (파싱한 dict, 응답 객체).
    images 를 주면(PNG 바이트) 사용자 메시지 앞에 이미지 블록으로 붙인다(분석 화면의 비전 채점)."""
    import anthropic
    import base64
    client = anthropic_client()
    content: str | list = user
    if images:
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": base64.b64encode(b).decode()}} for b in images]
        content.append({"type": "text", "text": user})
    kwargs = dict(
        model=model or CLAUDE_MODEL, max_tokens=CLAUDE_MAX_TOKENS, system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": CLAUDE_EFFORT, "format": {"type": "json_schema", "schema": schema}},
    )
    try:
        # 서버 측 fallback: 안전 분류기가 거절하면 같은 요청을 다른 모델이 이어 받는다 (Claude API)
        r = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except anthropic.BadRequestError as e:
        if "fallback" not in str(e).lower() and "beta" not in str(e).lower():
            raise
        r = client.messages.create(**kwargs)        # fallback 을 받지 않는 환경(프록시·구형)이면 일반 호출
    if getattr(r, "stop_reason", None) == "refusal":
        det = getattr(r, "stop_details", None)
        raise ChatRefused(f"모델이 요청을 거절했습니다 ({getattr(det, 'category', None) or '사유 미상'})")
    if getattr(r, "stop_reason", None) == "max_tokens":
        raise RuntimeError("응답이 길이 한도에서 잘렸습니다 (CLAUDE_MAX_TOKENS 를 올리세요)")
    text = next((b.text for b in r.content if getattr(b, "type", "") == "text"), "{}")
    return json.loads(text or "{}"), r


def _openai_json(system: str, user: str) -> tuple[dict, object]:
    from .copywriter import CHAT_MODEL, client
    r = client().chat.completions.create(
        model=CHAT_MODEL, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return json.loads(r.choices[0].message.content or "{}"), r


def chat_json(system: str, user: str, kind: str, schema: dict) -> dict:
    """시스템·사용자 프롬프트로 JSON 하나를 받는다. 호출 기록(calllog)에 공급자·모델·토큰·소요 시간과 보낸 프롬프트 전문을 남긴다.
    schema 는 JSON Schema(object). openai 경로에서는 스키마를 강제하지 못하고 프롬프트의 형식 지시에 의존한다."""
    provider = "anthropic" if CHAT_PROVIDER == "anthropic" else "openai"
    model = chat_model(kind)
    with calllog.timed("chat", kind, provider, model) as info:
        info["prompt_text"] = {"system": system, "user": user}
        data, r = _claude_json(system, user, schema, model=model) if provider == "anthropic" else _openai_json(system, user)
        _note_usage(info, r, provider, model)
    return data if isinstance(data, dict) else {}


def vision_json(system: str, user: str, images: list[bytes], kind: str, schema: dict) -> dict:
    """이미지(PNG 바이트)를 함께 보내 JSON 하나를 받는다. 공급자는 Claude 고정(CHAT_PROVIDER 와 무관 — 이미지 입력 경로는
    여기 하나뿐이다). 호출 기록에는 프롬프트 전문과 이미지 수만 남긴다(이미지 자체는 저장하지 않는다)."""
    model = model_for(kind)
    with calllog.timed("chat", kind, "anthropic", model, images=len(images)) as info:
        info["prompt_text"] = {"system": system, "user": user}
        data, r = _claude_json(system, user, schema, images=images, model=model)
        _note_usage(info, r, "anthropic", model)
    return data if isinstance(data, dict) else {}


def _note_usage(info: dict, r, provider: str, model: str | None = None) -> None:
    u = getattr(r, "usage", None)
    if u is None:
        return
    if provider == "anthropic":
        info["tokens_in"] = getattr(u, "input_tokens", None)
        info["tokens_out"] = getattr(u, "output_tokens", None)
        cr = getattr(u, "cache_read_input_tokens", None)
        if cr:
            info["tokens_cached"] = cr
        served = getattr(r, "model", None)
        if served and served != (model or CLAUDE_MODEL):
            info["served_by"] = served              # fallback 으로 다른 모델이 답한 경우
    else:
        info["tokens_in"] = getattr(u, "prompt_tokens", None)
        info["tokens_out"] = getattr(u, "completion_tokens", None)


# ---- 스키마 (카피·아이콘 응답 모양) ----
def _arr(item: dict) -> dict:
    return {"type": "array", "items": item}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required if required is not None else list(props),
            "additionalProperties": False}


STR = {"type": "string"}
ICON_3D = _obj({"name": STR, "concept": STR})
SVG_ATTRS = _obj({k: STR for k in ("d", "cx", "cy", "r", "rx", "ry", "x", "y", "width", "height", "x1", "y1", "x2", "y2", "points")},
                 required=[])
ICON_SVG = _obj({"name": STR, "concept": STR, "elements": _arr(_obj({"tag": STR, "attrs": SVG_ATTRS}))})
ICONS_3D_SCHEMA = _obj({"icons": _arr(ICON_3D)})
ICONS_SVG_SCHEMA = _obj({"icons": _arr(ICON_SVG)})


def copy_schema(asset_fields: list[str]) -> dict:
    """카피 응답: variants(제목·본문·CTA) + icons(기능명 후보) + assets(요구된 필드만, 각 문자열 배열)."""
    props = {"variants": _arr(_obj({"headline": STR, "body": STR, "cta": STR})), "icons": _arr(ICON_3D)}
    if asset_fields:
        props["assets"] = _obj({f: _arr(STR) for f in asset_fields})
    return _obj(props)
