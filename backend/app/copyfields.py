"""규격이 요구하는 문구 필드를 채운다 (6-7). 값의 출처를 함께 기록한다.

- 제목/본문/CTA: copywriter.limits_for 가 연결한 필드 (편집한 제목, 선택한 카피의 본문·CTA)
- '긴 광고 제목': 선택한 카피의 본문 (사용자 결정)
- '업체 이름', '프로필 이름': 브랜드명 (사용자 결정)
- '배너 카피': 제목 (px 길이 기준이라 측정하지 않고 사람이 확인)
"""
import re

from . import copywriter

_COUNT_RE = re.compile(r"\s*([^,，]+?)\s*(\d+)\s*[~\-–]\s*(\d+)\s*개")


def parse_asset_counts(value: str | None) -> list[dict]:
    """'광고 제목 3~15개, 긴 광고 제목 1~5개, 설명 2~5개' → [{field, min, max}]. 해석 못 하면 []."""
    out = []
    for m in _COUNT_RE.finditer(value or ""):
        out.append({"field": m.group(1).strip(), "min": int(m.group(2)), "max": int(m.group(3))})
    return out


def asset_spec(presets) -> list[dict]:
    """선택 지면 중 '문구 수량' 조건이 있는 지면들의 추가 문구 요구를 하나로 합친다(카피 생성 호출에 넣는다).
    필드별: 요청 개수 = 최소 + 2(여유분, 최대 이내), 글자 수 제한은 가장 엄격한 값·세는 방식. 반환 [{field, count, min, max, max_chars, method, placements}]."""
    merged: dict[str, dict] = {}
    for pv in presets:
        cond = next((c for c in (pv.conditions or []) if c.get("name") == "문구 수량" and c.get("value")), None)
        if not cond:
            continue
        lim = {t["field"]: t for t in (pv.text_limits or [])}
        for r in parse_asset_counts(cond["value"]):
            cur = merged.setdefault(r["field"], {"field": r["field"], "min": 0, "max": 10**9, "max_chars": None,
                                                  "method": "글자 수", "placements": []})
            cur["min"], cur["max"] = max(cur["min"], r["min"]), min(cur["max"], r["max"])
            t = lim.get(r["field"], {})
            if t.get("max_chars") is not None and (cur["max_chars"] is None or t["max_chars"] < cur["max_chars"]):
                cur["max_chars"], cur["method"] = t["max_chars"], t.get("count_method", "글자 수")
            cur["placements"].append(pv.placement.name)
    for cur in merged.values():
        cur["count"] = min(cur["min"] + 2, cur["max"])
    return list(merged.values())

EXTRA = {"긴 광고 제목": ("body", "선택한 카피의 본문"),
         "업체 이름": ("brand_name", "브랜드명"),
         "프로필 이름": ("brand_name", "브랜드명")}
BANNER = "배너 카피"
SLOT_LABEL = {"headline": ("제목", "편집한 제목"), "body": ("본문", "선택한 카피의 본문"), "cta": ("CTA", "선택한 카피의 CTA")}


def fields_for(text_limits: list[dict], texts: dict, brand_name: str) -> list[dict]:
    """[{field, value, source, min_chars, max_chars, min_px, level, required, count_method}]"""
    limits = copywriter.limits_for(text_limits or [])
    by_field = {t["field"]: t for t in (text_limits or [])}
    out, seen = [], set()
    def add(field, value, source):
        t = by_field.get(field, {})
        out.append({"field": field, "value": value or "", "source": source,
                    "min_chars": t.get("min_chars"), "max_chars": t.get("max_chars"), "min_px": t.get("min_px"),
                    "level": t.get("level"), "required": bool(t.get("required")),
                    "count_method": t.get("count_method", "글자 수")})
        seen.add(field)
    for slot in ("headline", "body", "cta"):
        lim = limits.get(slot)
        if lim:
            add(lim["field"], texts.get(slot), SLOT_LABEL[slot][1])
    for t in text_limits or []:
        f = t["field"]
        if f in seen:
            continue
        if f in EXTRA:
            src, label = EXTRA[f]
            add(f, texts.get("body") if src == "body" else brand_name, label)
        elif f == BANNER:
            add(f, texts.get("headline"), "편집한 제목")
    # 규격에 글자 수 제한이 없는 제목·본문·CTA 도 카피 파일에는 빠짐없이 넣는다. 특히 CTA 는 대부분의 매체가 규격 필드 없이
    # 플랫폼의 버튼 설정으로 받으므로, 사용자가 고른 문장이 어디에도 안 남지 않게 참고값으로 적는다(검수 대상 아님).
    covered = {slot for slot in ("headline", "body", "cta") if limits.get(slot)}
    for slot in ("headline", "body", "cta"):
        if slot in covered or not (texts.get(slot) or "").strip():
            continue
        label, source = SLOT_LABEL[slot]
        note = " (이 매체에는 CTA 문구 필드가 없음 — 플랫폼 버튼 설정에 쓰는 참고값)" if slot == "cta" else " (규격에 글자 수 제한 정보 없음)"
        out.append({"field": label, "value": texts[slot], "source": source + note,
                    "min_chars": None, "max_chars": None, "min_px": None, "level": None,
                    "required": False, "count_method": "글자 수"})
    return out
