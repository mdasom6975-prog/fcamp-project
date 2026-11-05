"""6단계 "고쳐 보기": 검수가 차단한 법적·기능성·브랜드 금지 표현을 모델에 고치게 하고, 코드로 다시 검사해 줄었을 때만 받아들인다.
한 지면의 문구(검수가 보는 것과 같은 제목·본문·CTA)가 단위. 최대 MAX_ROUNDS 회, 회차 기록은 캠페인 brief["fix_rounds"][preset_id] 에 남는다.
COPY_AGENT=0 이면 끈다. 모델 호출은 llm.chat_json(kind="copy_fix") 1회, 재검사는 모델 없이 legal·review 의 코드 검사만 쓴다."""
import os
import re

from . import legal, llm, prompts, review

MAX_ROUNDS = 3
ENABLED = os.getenv("COPY_AGENT", "1").strip() != "0"
SLOTS = ("headline", "body", "cta")
_REFUSAL = ("죄송", "할 수 없", "수 없습니다", "sorry", "cannot", "i can't")
_SPACE = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _SPACE.sub("", s or "").lower()


def blocking_violations(texts: dict, brand: dict) -> list[dict]:
    """검수(review_preset)와 같은 기준으로 차단되는 표현만 모은다: 브랜드 금지 표현(구분값이 차단일 때)·법적 block(입력에 없던 것)·입력 밖 기능성.
    반환: [{phrase, kind, reason, law, suggest}]"""
    joined = " ".join(texts.get(k) or "" for k in SLOTS)
    out: list[dict] = []
    lv = brand.get("levels") or {}
    for ph in brand.get("forbidden_phrases") or []:
        if ph and ph in joined and review._brand_fail(lv.get("forbidden_phrases"))[1]:
            out.append({"phrase": ph, "kind": "금지 표현", "reason": "브랜드 규칙의 금지 표현", "law": "브랜드 규칙", "suggest": "다른 표현으로 바꾸기"})
    for h in legal.scan(joined, brand.get("source_text") or "", brand.get("functional")):
        if h["severity"] == "block":
            out.append({"phrase": h["phrase"], "kind": "법적 표현", "reason": h["reason"], "law": h["law"], "suggest": h.get("suggest", "")})
    for v in legal.functional_violations(joined, brand.get("functional")):
        out.append({"phrase": v["phrase"], "kind": "기능성 표현", "reason": v["detail"], "law": v["law"], "suggest": "입력한 기능성 범위의 표현으로 바꾸기"})
    return out


def fields_with(texts: dict, violations: list[dict]) -> list[str]:
    """위반 표현이 든 필드. 못 찾으면(띄어쓰기 차이 등) 세 필드 모두."""
    hit = [s for s in SLOTS if any(_norm(v["phrase"]) in _norm(texts.get(s) or "") for v in violations)]
    return hit or list(SLOTS)


def build_prompt(brand_name: str, brand: dict, platform: str, placement: str, texts: dict,
                 violations: list[dict], fields: list[str], round_no: int) -> tuple[str, str]:
    lines = [f"- '{v['phrase']}' ({v['kind']}): {v['reason']} — 근거: {v['law']}" + (f" · 제안: {v['suggest']}" if v.get("suggest") else "")
             for v in violations]
    system = prompts.render("copy/fix_system")
    user = prompts.render("copy/fix_user", brand_name=brand_name,
                          required=", ".join(brand.get("required_phrases") or []) or "없음",
                          forbidden=", ".join(brand.get("forbidden_phrases") or []) or "없음",
                          platform=platform, placement=placement,
                          headline=texts.get("headline") or "", body=texts.get("body") or "", cta=texts.get("cta") or "",
                          round=str(round_no), violations="\n".join(lines), fields=", ".join(fields))
    return system, user


def merge_answer(texts: dict, data: dict, fields: list[str]) -> dict:
    """모델 답에서 고치라고 한 필드만 받아들인다. 비었거나 문자열이 아니거나 거절 문장이면 그 필드는 원문 유지."""
    new = dict(texts)
    if not isinstance(data, dict):
        return new
    for slot in fields:
        t = data.get(slot)
        if not isinstance(t, str) or not t.strip():
            continue
        if any(r in t.lower() for r in _REFUSAL):
            continue
        new[slot] = t.strip()
    return new


def attempt(brand_name: str, brand: dict, platform: str, placement: str, texts: dict, round_no: int) -> dict:
    """한 회차: 차단 위반을 모아 모델에 고치게 하고 코드로 재검사한다. 차단 수가 줄었을 때만 adopted.
    반환: {adopted, fields, before, after, texts, blocking_before, blocking_after, model, error}"""
    before = blocking_violations(texts, brand)
    fields = fields_with(texts, before)
    system, user = build_prompt(brand_name, brand, platform, placement, texts, before, fields, round_no)
    model = llm.chat_model("copy_fix")
    error = None
    try:
        data = llm.chat_json(system, user, "copy_fix", llm._obj({"headline": llm.STR, "body": llm.STR, "cta": llm.STR}))
    except Exception as e:   # 모델 실패는 회차로 기록하고 원문 유지
        data, error = {}, f"{type(e).__name__}"
    new = merge_answer(texts, data, fields)
    after = blocking_violations(new, brand)
    adopted = new != texts and len(after) < len(before)
    return {"adopted": adopted, "fields": fields, "before": before, "after": after if adopted else before,
            "texts": new if adopted else texts, "candidate": new, "blocking_before": len(before),
            "blocking_after": len(after) if adopted else len(before), "model": model, "error": error}
