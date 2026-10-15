"""Day 2 검증: specs/presets.json 이 시드와 같고, '확인됨' 값마다 출처·확인일이 있으며, 미확인 값이 '확인됨'으로 표시되지 않는다.

REQUIRE_VERIFIED_SPECS=1 이면 RFP 최소 요건(3개 이상 매체 · 6개 이상 프리셋이 '확인됨')까지 검사한다.
실행: backend/.venv/bin/python -m pytest tests -q
"""
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPECS = json.loads((ROOT / "specs" / "presets.json").read_text(encoding="utf-8"))
PRESETS = SPECS["presets"]
LEVELS = set(SPECS["level_values"])
STATUSES = set(SPECS["status_values"])
TEXT_METHODS = {"글자 수", "px 길이", "전각 2자 계산"}


def test_presets_json_matches_seed():
    from app.seed import PRESETS as SEED
    assert [(p["platform"], p["name"]) for p in SEED] == [(p["platform"], p["placement"]) for p in PRESETS], \
        "specs/presets.json 이 시드와 다릅니다. tools/export_presets.py 를 다시 실행하세요"


@pytest.mark.parametrize("p", PRESETS, ids=lambda p: f"{p['platform']}·{p['placement']}")
def test_status_and_sources(p):
    assert p["status"] in STATUSES
    if p["status"] != "확인 필요":
        assert p["sources"], "확인된 규격에는 출처 URL 이 있어야 합니다"
        for s in p["sources"]:
            assert s["url"].startswith("http") and s["checked_at"], "출처마다 URL 과 확인일"
        pd_ = p["production_default"]
        assert pd_["width"] and pd_["height"], "확인된 규격에는 제작 기본값(크기)이 있어야 합니다"
    else:
        assert not p["conditions"] and p["production_default"]["width"] is None, \
            "'확인 필요' 규격에 수치가 들어 있으면 추측값일 수 있습니다"


@pytest.mark.parametrize("p", PRESETS, ids=lambda p: f"{p['platform']}·{p['placement']}")
def test_conditions_have_level_and_status(p):
    for c in p["conditions"]:
        assert c["level"] in LEVELS, f"{c['name']}: 구분값 {c['level']}"
        assert c["status"] in STATUSES, f"{c['name']}: 상태 {c['status']}"
        if c["status"] in ("충돌", "확인 필요"):
            assert c.get("note"), f"{c['name']}: 충돌·확인 필요에는 이유(note)를 남긴다"
    for t in p["text_limits"]:
        assert t["count_method"] in TEXT_METHODS
        assert t["level"] in LEVELS
        if t["count_method"] == "px 길이":
            assert t["min_px"] or t["max_px"]
        else:
            assert t["max_chars"] or t["min_chars"]


def test_platform_has_cta_handling():
    """매체가 CTA 를 별도로 받는지는 content_kind(분리 자산형)로 구분한다."""
    kinds = {p["content_kind"] for p in PRESETS}
    assert kinds <= {"완성형", "분리 자산형"}
    separate = [p for p in PRESETS if p["content_kind"] == "분리 자산형"]
    assert separate, "CTA·문구를 별도 필드로 받는 지면(분리 자산형)이 하나 이상 있어야 한다"


@pytest.mark.skipif(os.getenv("REQUIRE_VERIFIED_SPECS") != "1", reason="REQUIRE_VERIFIED_SPECS=1 일 때만")
def test_rfp_minimum_verified():
    verified = [p for p in PRESETS if p["status"] == "확인됨"]
    platforms = {p["platform"] for p in verified}
    assert len(platforms) >= 3, f"확인된 매체 {len(platforms)}개 (3개 이상 필요)"
    assert len(verified) >= 6, f"확인된 프리셋 {len(verified)}개 (6개 이상 필요)"
