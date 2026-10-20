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
    # 아이콘 격자 렌더(기본 4x3)는 격자 테스트에서만 켠다. 나머지 테스트는 아이콘마다 1장 렌더를 가정한다
    from app import icons
    monkeypatch.setattr(icons, "GRID_CELLS", 0)
