"""법령 변경 점검 스크립트(tools/check_legal_sources.py): 변경·기준 없음·조회 실패 판정과 --update 동작. 네트워크는 쓰지 않는다."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_legal_sources", ROOT / "tools" / "check_legal_sources.py")
cls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cls)

SNAP = {"법령ID": "002011", "법령명한글": "표시ㆍ광고의 공정화에 관한 법률", "법령일련번호": "1", "공포일자": "20250101",
        "시행일자": "20250101", "제개정구분명": "일부개정"}


@pytest.fixture
def sources(tmp_path, monkeypatch):
    p = tmp_path / "sources.json"
    p.write_text(json.dumps({"checked_at": None, "sources": [
        {"name": "표시·광고의 공정화에 관한 법률", "cite": "표시·광고의 공정화에 관한 법률", "snapshot": None, "history": []}]}, ensure_ascii=False))
    monkeypatch.setattr(cls, "SOURCES", p)
    return p


def test_norm_treats_middle_dot_variants_as_same():
    assert cls.norm("표시ㆍ광고의 공정화에 관한 법률") == cls.norm("표시·광고의 공정화에 관한 법률")


def test_compare_statuses():
    assert cls.compare(None, SNAP) == "기준 없음"
    assert cls.compare(SNAP, dict(SNAP)) == "변경 없음"
    assert cls.compare(SNAP, {**SNAP, "공포일자": "20260101", "법령일련번호": "2"}) == "변경됨"


def test_check_without_update_never_writes(sources):
    before = sources.read_text()
    r = cls.run("test", False, fetch=lambda n, oc: SNAP)
    assert r[0]["status"] == "기준 없음" and sources.read_text() == before


def test_update_records_snapshot_then_detects_change_and_keeps_history(sources):
    cls.run("test", True, fetch=lambda n, oc: SNAP)
    saved = json.loads(sources.read_text())
    assert saved["sources"][0]["snapshot"]["법령일련번호"] == "1" and saved["checked_at"]
    assert cls.run("test", False, fetch=lambda n, oc: SNAP)[0]["status"] == "변경 없음"
    changed = {**SNAP, "법령일련번호": "2", "공포일자": "20260301"}
    assert cls.run("test", False, fetch=lambda n, oc: changed)[0]["status"] == "변경됨"
    cls.run("test", True, fetch=lambda n, oc: changed)
    saved = json.loads(sources.read_text())
    assert saved["sources"][0]["snapshot"]["법령일련번호"] == "2" and saved["sources"][0]["history"][0]["법령일련번호"] == "1"


def test_fetch_failure_is_reported_not_treated_as_unchanged(sources):
    def boom(n, oc):
        raise OSError("network down")
    r = cls.run("test", True, fetch=boom)
    assert r[0]["status"] == "조회 실패" and "network down" in r[0]["error"]
    assert json.loads(sources.read_text())["sources"][0]["snapshot"] is None      # 실패해도 기준을 만들지 않는다


def test_affected_rules_counts_phrases_and_files():
    a = cls.affected("의료법")
    assert a["phrases"] > 0 and "legal/industry/의료" in a["files"]
    assert cls.affected("존재하지 않는 법")["phrases"] == 0


def test_every_cited_law_in_rules_is_a_tracked_source():
    """규칙 파일·표현 사전이 근거로 적은 법령이 점검 대상 목록에 빠져 있지 않은지."""
    tracked = [cls.norm(s["cite"]) for s in json.loads(cls.SOURCES.read_text(encoding="utf-8"))["sources"]]
    laws = [e["law"] for e in json.loads((cls.LEGAL / "phrases.json").read_text(encoding="utf-8"))["entries"]]
    for law in laws:
        if law.startswith("광고 윤리 기준"):      # 자율 규제 기준(법령이 아님)이라 법령 API 점검 대상이 아니다
            continue
        assert any(t in cls.norm(law) for t in tracked), f"점검 대상에 없는 근거: {law}"


def test_main_exit_codes(sources, monkeypatch, capsys):
    monkeypatch.setattr(cls, "fetch_law", lambda n, oc: SNAP)
    monkeypatch.setattr(cls, "run", lambda oc, update, fetch=None: [{"status": "변경 없음", "name": "x", "affected": {"phrases": 0, "files": []}, "new": SNAP}])
    assert cls.main([]) == 0
    monkeypatch.setattr(cls, "run", lambda oc, update, fetch=None: [{"status": "조회 실패", "name": "x", "affected": {"phrases": 0, "files": []}, "error": "e"}])
    assert cls.main([]) == 2
