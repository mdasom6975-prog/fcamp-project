"""Day 3 검증: brands/*/brand.json 이 앱의 브랜드 입력 스키마(BrandIn)를 통과하고, 규칙이 검사 가능한 형태인지 확인한다."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BRAND_FILES = sorted((ROOT / "brands").glob("*/brand.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", BRAND_FILES, ids=lambda p: p.parent.name)
def test_brand_schema(path):
    from app.schemas import ITEMS, LEVELS, BrandIn
    doc = load(path)
    assert doc["schema_version"] and doc["brand_id"] == path.parent.name
    b = BrandIn.model_validate(doc["brand"])       # 앱이 거절하면 여기서 실패
    assert set(b.levels) == set(ITEMS), "모든 항목에 필수/권장/수정가능 등급이 있어야 한다"
    assert all(v in LEVELS for v in b.levels.values())


@pytest.mark.parametrize("path", BRAND_FILES, ids=lambda p: p.parent.name)
def test_rules_are_checkable(path):
    """'브랜드 색을 쓴다' 같은 서술이 아니라 코드가 검사할 수 있는 값인지."""
    b = load(path)["brand"]
    assert b["colors"], "허용 색상 목록(HEX)이 있어야 색 검사가 가능하다"
    roles = [c["role"] for c in b["colors"]]
    assert len(roles) == len(set(roles)), "색상 역할 이름이 중복된다"
    assert "텍스트색" in roles, "문구 색 검사에 쓰는 텍스트색이 필요하다"
    assert b["required_phrases"] and b["forbidden_phrases"], "필수·금지 표현은 비어 있으면 검수 항목이 생기지 않는다"
    assert not set(b["required_phrases"]) & set(b["forbidden_phrases"]), "필수와 금지에 같은 문구"
    assert b["levels"]["forbidden_phrases"] == "필수", "금지 표현 위반은 검수 완료를 차단해야 한다 (RFP)"
    assert b["levels"]["logo"] == "필수"


@pytest.mark.parametrize("path", BRAND_FILES, ids=lambda p: p.parent.name)
def test_meta_records_source_and_decisions(path):
    meta = load(path)["meta"]
    assert meta["source"] and meta["checked_at"], "가이드 출처와 확인일"
    assert "logo" in meta, "로고 해시·비율 자리(meta.logo)가 있어야 한다 (없으면 null)"


def test_kucha_excludes_service_flow_from_generation():
    """가이드: 서비스 이용 과정(service_flow)은 생성 프롬프트에 넣지 않는다."""
    doc = load(ROOT / "brands" / "kucha" / "brand.json")
    b = doc["brand"]
    text = " ".join([b["description"], b["image_tone"]["description"], b["voice"]["description"]])
    for banned in ("라인업 보기", "무작위 뽑기", "보관함", "합배송", "결과 확인"):
        assert banned not in text, f"생성 프롬프트에 들어가는 필드에 교육용 서비스 흐름 '{banned}' 이 있다"
    assert "쿠지" in b["forbidden_phrases"], "쿠지는 준비 중이라 광고 제작 금지"


def test_two_brands_do_not_share_colors():
    """교차 테스트: 기준 브랜드(쿠챠)와 두 번째 브랜드(그린테이블)의 허용 색이 겹치지 않아야 색 혼용을 검수로 잡을 수 있다.
    (brands/ 에 더미 브랜드가 늘어도 정렬 순서에 따라 비교 대상이 바뀌지 않도록 두 브랜드를 이름으로 지정한다.
    더미 브랜드 전체의 주조색 중복은 test_demo_data.py 가 본다. 말끔 폴더를 지워 그린테이블과 비교한다)"""
    sets = [{c["hex"].upper() for c in load(ROOT / "brands" / b / "brand.json")["brand"]["colors"]} for b in ("kucha", "greentable")]
    assert not (sets[0] & sets[1]), f"겹치는 색: {sets[0] & sets[1]}"
