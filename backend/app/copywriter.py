"""카피 생성 (6-2). 지면별 문구 제한을 생성 조건으로 주고, 생성 후 코드로 다시 검사한다."""
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from .db import BASE_DIR
from .textcount import METHODS, count_len

load_dotenv(BASE_DIR / ".env")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-5.4-mini")
VARIANTS = 3
MAX_ATTEMPTS = 2  # 최초 1회 + 위반 시 자동 재생성 1회

# 시드된 문구 제한 필드명을 제목/본문/CTA 로 연결한다. 목록에 없는 필드는 이번 단계에서 생성하지 않는다.
HEADLINE_FIELDS = {"제목", "광고 제목", "타이틀"}
BODY_FIELDS = {"기본 문구", "설명", "홍보문구", "내용"}
CTA_FIELDS = {"버튼명"}

_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(timeout=60.0)
    return _client


def limits_for(text_limits: list[dict]) -> dict:
    """프리셋 문구 제한 → {headline, body, cta: {field,min,max,level}|None, skipped: [...]}"""
    out: dict = {"headline": None, "body": None, "cta": None, "skipped": []}
    for t in text_limits:
        f = t["field"]
        slot = "headline" if f in HEADLINE_FIELDS else "body" if f in BODY_FIELDS else "cta" if f in CTA_FIELDS else None
        if slot is None or t.get("count_method") not in METHODS or out[slot] is not None:
            reason = "px 길이 기준(검수 단계에서 측정)" if t.get("count_method") == "px 길이" else "이번 단계에서 생성하지 않는 항목"
            out["skipped"].append({"field": f, "reason": reason})
            continue
        out[slot] = {"field": f, "min": t.get("min_chars"), "max": t.get("max_chars"), "level": t.get("level"),
                     "method": t.get("count_method", "글자 수")}
    return out


def check(copy: dict, limits: dict, brand: dict) -> list[dict]:
    """생성된 문구를 코드로 검사한다. (글자 수는 공백 포함 문자 수)"""
    v: list[dict] = []
    for slot, label in (("headline", "제목"), ("body", "본문"), ("cta", "CTA")):
        lim, text = limits.get(slot), copy.get(slot, "")
        if not text.strip():
            v.append({"type": "empty", "field": label, "detail": "내용이 비어 있습니다", "severity": "수정 필요"})
            continue
        n = count_len(text, lim.get("method")) if lim else len(text)
        unit = "자(전각 2자 계산)" if lim and lim.get("method") == "전각 2자 계산" else "자"
        if lim and lim.get("max") is not None and n > lim["max"]:
            v.append({"type": "length", "field": f"{label}({lim['field']})",
                      "detail": f"{n}{unit} / 최대 {lim['max']}자", "severity": lim.get("level") or "미표기"})
        if lim and lim.get("min") is not None and n < lim["min"]:
            v.append({"type": "min_length", "field": f"{label}({lim['field']})",
                      "detail": f"{n}{unit} / 최소 {lim['min']}자", "severity": lim.get("level") or "미표기"})
    joined = " ".join(copy.get(k, "") for k in ("headline", "body", "cta"))
    lv = brand.get("levels", {})
    for ph in brand.get("forbidden_phrases", []):
        if ph and ph in joined:
            v.append({"type": "forbidden", "field": "금지 표현", "detail": f"'{ph}' 포함",
                      "severity": lv.get("forbidden_phrases", "필수")})
    for ph in brand.get("required_phrases", []):
        if ph and ph not in joined:
            v.append({"type": "required_missing", "field": "필수 표현", "detail": f"'{ph}' 누락",
                      "severity": lv.get("required_phrases", "필수")})
    return v


def _prompt(brand_name: str, brand: dict, brief: dict, placement: dict, limits: dict) -> tuple[str, str]:
    def rule(slot, label):
        l = limits.get(slot)
        if not l:
            return f"- {label}: 글자 수 제한 정보 없음 (짧고 간결하게)"
        rng = []
        if l.get("min") is not None:
            rng.append(f"{l['min']}자 이상")
        if l.get("max") is not None:
            rng.append(f"{l['max']}자 이하")
        how = ("한글 등 전각 문자는 글자당 2자로 계산(한글 한 글자=2), 영문·숫자·공백은 1자"
               if l.get("method") == "전각 2자 계산" else "공백 포함")
        return f"- {label}: {', '.join(rng) or '제한 없음'} ({how}, 매체 필드명 '{l['field']}')"

    voice = brand.get("voice", {})
    system = (
        "너는 한국어 광고 카피라이터다. 브랜드 규칙과 매체 문구 제한을 지켜 광고 카피를 쓴다. "
        "반드시 JSON 객체만 출력한다: {\"variants\":[{\"headline\":\"\",\"body\":\"\",\"cta\":\"\"}]}. "
        f"variants 는 정확히 {VARIANTS}개이며 서로 접근 방식이 달라야 한다. "
        "과장·결과 보장·근거 없는 최상급·전후 비교 표현은 쓰지 않는다. 제공되지 않은 사실(가격, 수치, 효과)은 만들지 않는다."
    )
    user = f"""[브랜드] {brand_name}
- 어조: {voice.get('description') or '지정 없음'}
- 헤드라인 권장 길이: {str(voice['headline_max_chars']) + '자 이내' if voice.get('headline_max_chars') else '지정 없음'}
- 존댓말: {'존댓말로 작성' if voice.get('polite') else '지정 없음'}
- 반드시 포함할 표현(headline, body, cta 중 어디든): {', '.join(brand.get('required_phrases', [])) or '없음'}
- 절대 쓰지 말 표현: {', '.join(brand.get('forbidden_phrases', [])) or '없음'}

[상품·메시지]
- 상품/서비스명: {brief.get('product_name')}
- 설명: {brief.get('description') or '없음'}
- 핵심 메시지: {brief.get('key_message') or '없음'}
- 타깃 고객: {brief.get('target') or '없음'}

[지면] {placement['platform']} · {placement['name']}
글자 수 제한:
{rule('headline', 'headline(제목)')}
{rule('body', 'body(본문)')}
{rule('cta', 'cta(행동 유도)')}
제한을 넘지 않도록 글자 수를 직접 세어 확인한 뒤 출력한다."""
    return system, user


def generate(brand_name: str, brand: dict, brief: dict, placement: dict, limits: dict) -> list[dict]:
    system, user = _prompt(brand_name, brand, brief, placement, limits)
    r = client().chat.completions.create(
        model=CHAT_MODEL,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    data = json.loads(r.choices[0].message.content or "{}")
    out = []
    for item in (data.get("variants") or [])[:VARIANTS]:
        out.append({k: str(item.get(k, "")).strip() for k in ("headline", "body", "cta")})
    return out


def generate_checked(brand_name, brand, brief, placement, limits) -> tuple[list[dict], int]:
    """생성 → 코드 검사 → 위반이 있으면 1회 재생성 → 위반이 적은 쪽을 채택."""
    best, best_bad, attempts = None, 10**9, 0
    for _ in range(MAX_ATTEMPTS):
        attempts += 1
        variants = generate(brand_name, brand, brief, placement, limits)
        for v in variants:
            v["violations"] = check(v, limits, brand)
        bad = sum(1 for v in variants if v["violations"])
        if variants and bad < best_bad:
            best, best_bad = variants, bad
        if best is not None and best_bad == 0:
            break
    return best or [], attempts
