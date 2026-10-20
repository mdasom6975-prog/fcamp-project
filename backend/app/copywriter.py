"""카피 생성 (6-2). 지면별 문구 제한을 생성 조건으로 주고, 생성 후 코드로 다시 검사한다."""
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from . import calllog, prompts
from .db import BASE_DIR
from .textcount import METHODS, count_len

load_dotenv(BASE_DIR / ".env")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-5.4-mini")
VARIANTS = 3
MAX_ATTEMPTS = 2  # 최초 1회 + 위반 시 자동 재생성 1회
ICON_COUNT = 10            # 아이콘 세트 크기
ICON_CANDIDATES = 15       # 카피 호출에서 함께 받는 기능명 후보 수 (여유분 5개 → 중복·금지 표현 제외 뒤에도 추가 요청이 거의 없다)

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


def _as_chars(l: dict, key: str) -> int | None:
    """제한값을 '글자 수' 기준으로 환산. 전각 2자 계산은 한글 위주 문장을 가정해 절반(보수적)."""
    v = l.get(key)
    if v is None:
        return None
    return v // 2 if l.get("method") == "전각 2자 계산" else v


def merge_limits(per_placement: list[tuple[str, dict]]) -> dict:
    """여러 지면의 제한을 '모든 지면에 같은 문장'을 쓰기 위한 가장 엄격한 제한 하나로 합친다.
    슬롯별 max 는 가장 작은 값, min 은 가장 큰 값. 제한이 있는 지면이 하나도 없는 슬롯은 None.
    field 에는 어느 지면의 제한이 기준이 됐는지 남긴다 (프롬프트의 '매체 필드명' 자리에 들어간다)."""
    out: dict = {"headline": None, "body": None, "cta": None, "skipped": []}
    seen: set[tuple[str, str]] = set()
    for name, limits in per_placement:
        for s in limits.get("skipped", []):
            if (name, s["field"]) not in seen:
                seen.add((name, s["field"]))
                out["skipped"].append({"field": f"{name} {s['field']}", "reason": s["reason"]})
        for slot in ("headline", "body", "cta"):
            l = limits.get(slot)
            if not l:
                continue
            mx, mn = _as_chars(l, "max"), _as_chars(l, "min")
            cur = out[slot]
            if cur is None:
                out[slot] = {"field": f"모든 지면 공통 · 가장 짧은 제한 {name} '{l['field']}'", "min": mn, "max": mx,
                             "level": l.get("level"), "method": "글자 수"}
                continue
            if mx is not None and (cur["max"] is None or mx < cur["max"]):
                cur.update({"max": mx, "field": f"모든 지면 공통 · 가장 짧은 제한 {name} '{l['field']}'",
                            "level": l.get("level")})
            if mn is not None and (cur["min"] is None or mn > cur["min"]):
                cur["min"] = mn
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

    # 문장은 prompts/copy/system.md · user.md 에 있다. 여기서는 분기(없음/지정 없음)와 제한 줄만 계산해 넘긴다.
    voice = brand.get("voice", {})
    system = prompts.render("copy/system", variants=VARIANTS, icon_count=ICON_CANDIDATES)
    user = prompts.render(
        "copy/user",
        brand_name=brand_name,
        voice=voice.get("description") or "지정 없음",
        headline_len=str(voice["headline_max_chars"]) + "자 이내" if voice.get("headline_max_chars") else "지정 없음",
        polite="존댓말로 작성" if voice.get("polite") else "지정 없음",
        required=", ".join(brand.get("required_phrases", [])) or "없음",
        forbidden=", ".join(brand.get("forbidden_phrases", [])) or "없음",
        product_name=brief.get("product_name"),
        description=brief.get("description") or "없음",
        key_message=brief.get("key_message") or "없음",
        target=brief.get("target") or "없음",
        platform=placement["platform"],
        placement=placement["name"],
        limit_headline=rule("headline", "headline(제목)"),
        limit_body=rule("body", "body(본문)"),
        limit_cta=rule("cta", "cta(행동 유도)"),
    )
    return system, user


def parse_icons(data: dict) -> list[dict]:
    """카피 응답에 같이 온 아이콘 기능명 후보 [{name, concept}]. 이름이 비었거나 겹치면 뺀다."""
    out, seen = [], set()
    for it in data.get("icons") or []:
        name = str((it or {}).get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "concept": str(it.get("concept") or "").strip()})
    return out[:ICON_CANDIDATES]


def generate(brand_name: str, brand: dict, brief: dict, placement: dict, limits: dict,
             icons_out: list | None = None) -> list[dict]:
    """카피 변형을 만든다. icons_out 리스트를 주면 같은 응답의 아이콘 기능명 후보를 거기에 채운다(추가 호출 없음)."""
    system, user = _prompt(brand_name, brand, brief, placement, limits)
    with calllog.timed("chat", "copy", "openai", CHAT_MODEL) as info:
        r = client().chat.completions.create(
            model=CHAT_MODEL,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        calllog.note_usage(info, r)
    data = json.loads(r.choices[0].message.content or "{}")
    if icons_out is not None:
        icons_out[:] = parse_icons(data)
    out = []
    for item in (data.get("variants") or [])[:VARIANTS]:
        out.append({k: str(item.get(k, "")).strip() for k in ("headline", "body", "cta")})
    return out


def generate_checked(brand_name, brand, brief, placement, limits) -> tuple[list[dict], int, list[dict]]:
    """생성 → 코드 검사 → 위반이 있으면 1회 재생성 → 위반이 적은 쪽을 채택. 반환: (변형, 시도 수, 아이콘 기능명 후보).
    아이콘 후보는 마지막 응답의 것을 쓴다(아이콘 세트 생성 때 기능명 제안 호출을 대신한다)."""
    best, best_bad, attempts, icons = None, 10**9, 0, []
    for _ in range(MAX_ATTEMPTS):
        attempts += 1
        got: list = []
        variants = generate(brand_name, brand, brief, placement, limits, icons_out=got)
        icons = got or icons
        for v in variants:
            v["violations"] = check(v, limits, brand)
        bad = sum(1 for v in variants if v["violations"])
        if variants and bad < best_bad:
            best, best_bad = variants, bad
        if best is not None and best_bad == 0:
            break
    return best or [], attempts, icons
