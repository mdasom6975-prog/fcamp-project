"""필수·금지 표현의 적용 규칙 (10/19).

브랜드 규칙(BrandVersion.required_phrases / forbidden_phrases)은 그대로 두고, 캠페인의 상품·메시지 입력(Design.brief)에도
같은 두 항목을 둔다. **캠페인 입력이 우선**이고, 캠페인 쪽이 비어 있으면 브랜드 규칙 값을 쓴다. 필수와 금지는 각각 따로 판단한다.
카피 생성·검수·편집 경고·아이콘 기능명 검사가 모두 이 함수를 거친다.
"""

CAMPAIGN, BRAND = "캠페인", "브랜드"


def clean(items: list[str] | None) -> list[str]:
    """공백 제거·빈 값 제외·중복 제거 (routers_brands._clean 과 같은 규칙)."""
    seen: set[str] = set()
    out: list[str] = []
    for s in items or []:
        s = (s or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def effective(bv, brief: dict | None) -> dict:
    """{required_phrases, forbidden_phrases, required_source, forbidden_source}. source 는 '캠페인' | '브랜드'."""
    brief = brief or {}
    out: dict = {}
    for key in ("required_phrases", "forbidden_phrases"):
        mine = clean(brief.get(key))
        src = CAMPAIGN if mine else BRAND
        out[key] = mine if mine else clean(getattr(bv, key, None) if bv is not None else None)
        out[key.replace("_phrases", "_source")] = src
    return out


def rules(bv, brief: dict | None) -> dict:
    """카피 생성·검사용 브랜드 규칙 dict (copywriter.check / _prompt 가 읽는 형태)."""
    eff = effective(bv, brief)
    from . import legal
    return {"voice": bv.voice, "required_phrases": eff["required_phrases"],
            "forbidden_phrases": eff["forbidden_phrases"], "levels": bv.levels, "source_text": legal.source_text(brief),
            "functional": (brief or {}).get("functional")}
