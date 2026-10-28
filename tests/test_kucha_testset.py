"""쿠챠 테스트 세트 70건(tools/kucha_testset.py, 가이드 8쪽): 구성·예상 결과 일치·교육 정보 분리·앱 변환을 검사한다.
외부 API 는 호출하지 않는다 (임시 DB·임시 저장 폴더)."""
import copy
import importlib.util
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("kucha_testset", ROOT / "tools" / "kucha_testset.py")
ks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ks)

RECORDS = ks.build_records()
BY_ID = {r["record_id"]: r for r in RECORDS}
ACCEPTED = [r for r in RECORDS if r["test_group"] != "reject"]
REJECTED = [r for r in RECORDS if r["test_group"] == "reject"]


def test_set_has_the_documented_composition():
    assert len(RECORDS) == 70 and len(BY_ID) == 70, "레코드 ID 는 겹치지 않아야 한다"
    assert Counter(r["test_group"] for r in RECORDS) == {"visual": 48, "ux": 12, "reject": 10}
    visual = [r for r in RECORDS if r["test_group"] == "visual"]
    assert Counter(r["asset_type"] for r in visual) == {t: 12 for t in
                                                        ("ad_image", "background", "generic_3d", "marketing_icon")}
    assert len({(r["asset_type"], r["campaign_objective"], r["visual_keywords"][0], r["variant_no"])
                for r in visual}) == 48, "자산 유형 × 목표 × 분위기 × 시안 조합이 모두 달라야 한다"
    ux = [r for r in RECORDS if r["test_group"] == "ux"]
    assert len({(r["copy_intent"], r["copy"]["tone"], r["variant_no"]) for r in ux}) == 12
    assert {r["evaluation"]["error_code"] for r in REJECTED} == {
        "missing_required", "invalid_enum", "training_context_leak", "unapproved_ip", "minor_targeting",
        "unsupported_claim", "expired_live_claim", "kuji_prelaunch", "broken_text", "over_budget"}


@pytest.mark.parametrize("r", RECORDS, ids=lambda r: r["record_id"])
def test_validator_matches_expected_result(r):
    """가이드의 '예상 결과 일치': 예상 상태·오류 코드가 검증기 판정과 정확히 같다. 반려는 위반 하나만 걸린다."""
    ev = r["evaluation"]
    assert ks.verdict(r) == (ev["expected_status"], ev["error_code"])
    assert len(ks.validate(r)) == (1 if r["test_group"] == "reject" else 0)


def test_each_violation_is_caught_only_by_its_own_mutation():
    """반려 레코드는 바탕 정상 레코드에서 위반 하나만 넣은 것이어야 한다 (바탕은 통과)."""
    for r in REJECTED:
        base = BY_ID[r["base_record_id"]]
        assert ks.validate(base) == []
        assert r["asset_type"] == base["asset_type"]


def test_service_flow_never_reaches_prompts_or_briefs():
    """교육용 서비스 흐름은 저장만 하고 AI 전달문·앱 브리프에는 없어야 한다."""
    for r in ACCEPTED:
        assert r["instruction_context"]["compile_to_prompt"] is False
        assert r["excluded_context_keys"] == ["service_flow"]
        text = r["compiled_positive_prompt"] + str(ks.to_campaign(r)["brief"])
        assert not any(t in text for t in ks.LEAK_TERMS), r["record_id"]
        assert ks.SERVICE_FLOW not in text


def test_positive_prompt_has_no_forbidden_claim_words():
    for r in ACCEPTED:
        assert not any(t in r["compiled_positive_prompt"] for t in ks.CLAIM_TERMS), r["record_id"]
        assert "글자를 생성하지 않" in r["compiled_positive_prompt"] or r["asset_type"] == "ux_copy"


def test_records_file_is_current():
    """demo/kucha_testset/records.jsonl 이 생성기 결과와 같다 (코드만 고치고 build 를 잊지 않도록)."""
    assert ks.load_records() == RECORDS, "python tools/kucha_testset.py build 를 다시 실행하세요"


def test_campaigns_fit_the_app_schema():
    from app.routers_designs import BriefIn
    names = set()
    presets = ROOT / "specs" / "presets.json"
    import json
    placements = {p["placement"] for p in json.loads(presets.read_text(encoding="utf-8"))["presets"]}
    assert len(ACCEPTED) == 60
    for r in ACCEPTED:
        c = ks.to_campaign(r)
        BriefIn.model_validate(c["brief"])
        assert c["placements"] and set(c["placements"]) <= placements
        assert c["brief"]["required_phrases"] == [] and c["brief"]["forbidden_phrases"] == [], \
            "캠페인 값이 있으면 브랜드의 필수·금지 표현을 덮어쓰므로 비워 둔다"
        names.add(c["brief"]["product_name"])
    assert len(names) == 60, "캠페인 상품명은 레코드마다 달라야 중복 방지에 걸리지 않는다"


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import routers_assets, seed
    from app.db import Base
    from app.main import app

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)
    with Local() as db:
        seed.seed(db)

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[routers_assets.get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_seed_creates_campaigns_once(client):
    logs: list[str] = []
    assert ks.seed(client, limit=None, log=logs.append) == 60
    assert not [m for m in logs if "실패" in m], logs
    designs = client.get("/api/designs").json()
    assert len(designs) == 60 and {d["brand"]["name"] for d in designs} == {"쿠챠"}
    assert ks.seed(client, log=lambda m: None) == 0, "다시 실행해도 새로 만들지 않는다"
    assert len(client.get("/api/designs").json()) == 60


def test_seed_group_filter(client):
    assert ks.seed(client, group="ux", log=lambda m: None) == 12
    assert len(client.get("/api/designs").json()) == 12


def test_validator_is_not_trivially_true():
    """검증기가 정상 레코드를 가리지 않고 위반만 잡는지: 정상 레코드를 망가뜨리면 해당 코드가 나온다."""
    r = copy.deepcopy(BY_ID["KCH-IMG-001"])
    r["output_contract"]["ratio"] = "7:3"
    assert ks.validate(r) == ["invalid_enum"], "지원하지 않는 비율은 반려"
    r = copy.deepcopy(BY_ID["KCH-IMG-001"])
    r["live_claims"] = [{"key": "k", "value": 1, "unit": "원", "checked_at": "2026-10-01", "expires_at": "2026-12-31",
                         "source_url": None}]
    assert ks.validate(r) == ["expired_live_claim"], "출처 누락도 실시간 정보 위반"
