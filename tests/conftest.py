import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import pytest


@pytest.fixture(autouse=True)
def _calllog_tmp(tmp_path, monkeypatch):
    """테스트가 실제 backend/data 의 호출 기록·모델 기능 기록을 건드리지 않게 임시 파일로 돌린다."""
    from app import calllog, imagegen
    monkeypatch.setattr(calllog, "LOG_FILE", tmp_path / "api_calls.jsonl")
    monkeypatch.setattr(imagegen, "CAPS_FILE", tmp_path / "model_caps.json")
    monkeypatch.setattr(imagegen, "_caps_cache", None)
