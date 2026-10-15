"""규격이 요구하는 문구 필드를 채운다 (6-7). 값의 출처를 함께 기록한다.

- 제목/본문/CTA: copywriter.limits_for 가 연결한 필드 (편집한 제목, 선택한 카피의 본문·CTA)
- '긴 광고 제목': 선택한 카피의 본문 (사용자 결정)
- '업체 이름', '프로필 이름': 브랜드명 (사용자 결정)
- '배너 카피': 제목 (px 길이 기준이라 측정하지 않고 사람이 확인)
"""
from . import copywriter

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
    # 규격에 글자 수 제한이 없는 제목·본문·CTA 도 카피 파일에는 빠짐없이 넣는다
    covered = {slot for slot in ("headline", "body", "cta") if limits.get(slot)}
    for slot in ("headline", "body", "cta"):
        if slot in covered or not (texts.get(slot) or "").strip():
            continue
        label, source = SLOT_LABEL[slot]
        out.append({"field": label, "value": texts[slot], "source": source + " (규격에 글자 수 제한 정보 없음)",
                    "min_chars": None, "max_chars": None, "min_px": None, "level": None,
                    "required": False, "count_method": "글자 수"})
    return out
