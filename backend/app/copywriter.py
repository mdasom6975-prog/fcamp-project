"""카피 생성 (6-2). 지면별 문구 제한을 생성 조건으로 주고, 생성 후 코드로 다시 검사한다."""
import os
import re

from dotenv import load_dotenv
from openai import OpenAI

from . import legal, prompts
from .db import BASE_DIR
from .textcount import METHODS, count_len

load_dotenv(BASE_DIR / ".env")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-5.4-mini")
VARIANTS = 3
# 글자 수 위반 시 자동 재생성: 기본 끔(COPY_AUTO_RETRY=1 이면 켬). 두 번째 호출도 위반이 남는 경우가 많아 호출만 늘렸다.
# 대신 프롬프트에 제한의 90% 이내로 쓰라는 여유분 지시를 두고, 위반이 남으면 사용자가 '카피 다시 생성'을 누른다.
MAX_ATTEMPTS = 2 if os.getenv("COPY_AUTO_RETRY", "0") == "1" else 1
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
    v += legal.violations(joined, brand.get("source_text") or "", brand.get("functional"))       # 법적·제도적 금지 표현(브랜드 규칙과 별개로 항상 검사)
    v += legal.functional_violations(joined, brand.get("functional"))    # 입력한 기능성 밖의 기능성 표현(입력한 캠페인만)
    return v


def _strip_title(text: str) -> str:
    """조각 앞의 '[문서 제목] ' 을 뗀다(프롬프트에서는 출처 표시를 따로 붙인다)."""
    return re.sub(r"^\[[^\[\]]*\]\s*", "", text)


def knowledge_vars(knowledge: dict | None, brief: dict) -> dict[str, str]:
    """카피 User 의 {{functional}} · {{law}} · {{guide}} 값. 문장은 prompts/copy/_functional*.md · _law.md · _guide.md 에 있고
    여기서는 줄 목록만 만든다. 해당 내용이 없으면 빈 문자열(지식이 없는 브랜드는 프롬프트가 그대로다)."""
    k = knowledge or {}
    fn = brief.get("functional")
    functional = ("" if fn is None else "\n" + (prompts.render("copy/_functional", functional=", ".join(fn)) if fn
                                                 else prompts.render("copy/_functional_none")))
    law = ""
    if k.get("law"):
        lines = "\n".join(f"- ({r['section']}) {_strip_title(r['text'])}" for r in k["law"])
        law = "\n" + prompts.render("copy/_law", industries=", ".join(k.get("industries") or []), items=lines)
    guide = ""
    if k.get("guide"):
        lines = "\n".join(f"- ({r['layer']} · {r['section']}) {_strip_title(r['text'])}" for r in k["guide"])
        guide = "\n" + prompts.render("copy/_guide", items=lines)
    return {"functional": functional, "law": law, "guide": guide}


def _shared_vars(brand_name: str, brand: dict, brief: dict, knowledge: dict | None) -> dict:
    """copy/user 와 copy/extras_user 가 함께 쓰는 변수."""
    voice = brand.get("voice", {})
    return dict(
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
        legal=legal.industry_block(legal.detect(" ".join(str(brief.get(k) or "") for k in ("product_name", "description", "key_message")))),
        **knowledge_vars(knowledge, brief),
    )


def _prompt(brand_name: str, brand: dict, brief: dict, placement: dict, limits: dict,
            knowledge: dict | None = None) -> tuple[str, str]:
    """knowledge: knowledge.for_copy 결과(규제 원문·브랜드 지식). 없으면 지금처럼 브랜드 규칙과 브리프만으로 만든다."""
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
    system = prompts.render("copy/system", variants=VARIANTS)
    user = prompts.render(
        "copy/user",
        **_shared_vars(brand_name, brand, brief, knowledge),
        platform=placement["platform"],
        placement=placement["name"],
        limit_headline=rule("headline", "headline(제목)"),
        limit_body=rule("body", "body(본문)"),
        limit_cta=rule("cta", "cta(행동 유도)"),
    )
    return system, user


def _assets_block(spec: list[dict]) -> str:
    """추가 문구 자산 요구 문단(PMax 등 '문구 수량' 조건, copyfields.asset_spec 결과). 요구가 없으면 빈 문자열."""
    if not spec:
        return ""
    how = lambda m: ("한글 등 전각 문자는 글자당 2자로 계산(한글 한 글자=2), 영문·숫자·공백은 1자" if m == "전각 2자 계산" else "공백 포함")
    rows = [f"- {a['field']}: 정확히 {a['count']}개, 각 {a['max_chars']}자 이하({how(a['method'])})" if a.get("max_chars")
            else f"- {a['field']}: 정확히 {a['count']}개" for a in spec]
    return ("\n[추가 문구 자산] 아래 지면은 문구를 여러 개 요구한다(" + ", ".join(sorted({p for a in spec for p in a["placements"]})) +
            "). JSON 의 \"assets\" 객체에 필드명을 키로, 문구 문자열 배열을 값으로 넣는다. 서로 다른 각도의 문장으로, 위 브랜드 규칙과 "
            "금지 표현을 똑같이 지키고 각 문구는 제한의 90% 이내로 쓴다.\n" + "\n".join(rows))


def extras_prompt(brand_name: str, brand: dict, brief: dict, spec: list[dict], variants: list[dict],
                  knowledge: dict | None = None, want_icons: bool = True) -> tuple[str, str]:
    """카피 뒤의 보조 호출(아이콘 후보·추가 문구 자산) 프롬프트. variants 는 이번 카피(어조 참고)."""
    icons_rule = prompts.render("copy/_extras_icons", icon_count=ICON_CANDIDATES) if want_icons else ""
    system = prompts.render("copy/extras_system", icons_rule=icons_rule)
    lines = "\n".join(f"- {v.get('headline', '')} / {v.get('body', '')}" for v in variants) or "- 없음"
    shared = _shared_vars(brand_name, brand, brief, knowledge)
    user = prompts.render("copy/extras_user", **shared, copy_lines=lines, assets=_assets_block(spec))
    return system, user


def extras(brand_name: str, brand: dict, brief: dict, spec: list[dict], variants: list[dict],
           knowledge: dict | None = None, want_icons: bool = True) -> tuple[list[dict], dict]:
    """아이콘 기능명 후보와 추가 문구 자산을 작은 모델로 따로 받는다. 반환: (아이콘 후보, 추가 문구 자산)."""
    from . import llm
    system, user = extras_prompt(brand_name, brand, brief, spec, variants, knowledge, want_icons)
    data = llm.chat_json(system, user, "copy_extras", llm.extras_schema([a["field"] for a in spec], want_icons))
    return parse_icons(data) if want_icons else [], parse_assets(data, spec) if spec else {}


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


def parse_assets(data: dict, spec: list[dict]) -> dict:
    """응답의 assets {필드: [문구]} 를 요구 필드만, 빈 값·중복 제거해 최대 개수까지 돌려준다."""
    out = {}
    raw = data.get("assets") or {}
    for a in spec:
        vals, seen = [], set()
        for t in (raw.get(a["field"]) or []) if isinstance(raw, dict) else []:
            t = str(t or "").strip()
            if t and t not in seen:
                seen.add(t); vals.append(t)
        out[a["field"]] = vals[:a["max"]]
    return out


def generate(brand_name: str, brand: dict, brief: dict, placement: dict, limits: dict,
             knowledge: dict | None = None) -> list[dict]:
    """카피 변형을 만든다. angle·evidence 는 어떤 각도로 어떤 사실을 썼는지의 기록(분석 화면)."""
    system, user = _prompt(brand_name, brand, brief, placement, limits, knowledge)
    from . import llm       # 공급자(Claude/OpenAI) 전환은 llm.py 가 맡는다. 순환 import 를 피해 함수 안에서 가져온다
    data = llm.chat_json(system, user, "copy", llm.copy_schema())
    out = []
    for item in (data.get("variants") or [])[:VARIANTS]:
        out.append({k: str(item.get(k, "")).strip() for k in ("headline", "body", "cta", "angle", "evidence")})
    return out


def generate_checked(brand_name, brand, brief, placement, limits, knowledge: dict | None = None) -> tuple[list[dict], int]:
    """생성 → 코드 검사. 반환: (변형, 시도 수). 기본은 1회 호출.
    COPY_AUTO_RETRY=1 이면 통과한 변형이 하나도 없을 때만 1회 더 생성해 위반이 적은 쪽을 채택한다(사용자는 변형 하나를
    고르므로 깨끗한 변형이 하나면 충분하다)."""
    best, best_bad, attempts = None, 10**9, 0
    for _ in range(MAX_ATTEMPTS):
        attempts += 1
        variants = generate(brand_name, brand, brief, placement, limits, knowledge=knowledge)
        for v in variants:
            v["violations"] = check(v, limits, brand)
        bad = sum(1 for v in variants if v["violations"])
        if variants and bad < best_bad:
            best, best_bad = variants, bad
        if best is not None and any(not v["violations"] for v in best):
            break
    return best or [], attempts
