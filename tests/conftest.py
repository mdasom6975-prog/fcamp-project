import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import pytest


@pytest.fixture(autouse=True)
def _calllog_tmp(tmp_path, monkeypatch):
    """테스트가 실제 backend/data/api_calls.jsonl 에 호출 기록을 남기지 않게 임시 파일로 돌린다."""
    from app import calllog
    monkeypatch.setattr(calllog, "LOG_FILE", tmp_path / "api_calls.jsonl")
