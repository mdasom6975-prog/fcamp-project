"""위반 테스트셋 — eval/briefs.csv 의 경계 사례를 카피 코드 검사(copywriter.check)에 돌려 예상 판정과 맞는지 본다.
브랜드 규칙은 brands/kucha/brand.json 을 그대로 쓴다. 외부 API 없음.
쿠챠 가이드 승인 기준의 '예상 결과 일치'와 워크북 Day 5(위반 테스트셋)·Day 7(briefs.csv 경계 사례)에 대응한다."""
import csv
import json
from pathlib import Path

import pytest

from app import copywriter

ROOT = Path(__file__).resolve().parent.parent
BRAND = json.loads((ROOT / "brands" / "kucha" / "brand.json").read_text(encoding="utf-8"))["brand"]
ROWS = list(csv.DictReader((ROOT / "eval" / "briefs.csv").open(encoding="utf-8")))


def _verdict(violations: list[dict]) -> str:
    return "수정 필요" if any(v["severity"] in ("필수", "강제", "수정 필요") for v in violations) else "통과"


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_case_matches_expected(row):
    limits = {"headline": {"field": "headline", "min": None, "max": int(row["headline_max"]), "level": "강제", "method": "글자 수"},
              "body": None, "cta": None}
    copy = {"headline": row["headline"], "body": row["body"], "cta": row["cta"]}
    got = copywriter.check(copy, limits, BRAND)
    assert _verdict(got) == row["expected"], (row["case"], got)
    expected_types = sorted(t for t in row["expected_types"].split(",") if t)
    assert sorted(v["type"] for v in got) == expected_types, (row["case"], got)


def test_every_forbidden_phrase_is_caught():
    """금지 표현 21개 각각이 제목에 들어가면 하나도 빠짐없이 '수정 필요'가 된다."""
    for ph in BRAND["forbidden_phrases"]:
        got = copywriter.check({"headline": f"쿠챠 {ph}", "body": "본문", "cta": "보기"}, {}, BRAND)
        assert [v["type"] for v in got] == ["forbidden"] and _verdict(got) == "수정 필요", ph


def test_set_covers_pass_and_fail():
    assert {r["expected"] for r in ROWS} == {"통과", "수정 필요"}
    assert len(ROWS) >= 16
