import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import pytest


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """테스트가 실제 backend/data 의 DB·backend/storage 의 파일, Supabase 를 건드리지 않게 임시 SQLite·임시 폴더로 돌린다.
    호출 기록·사용량·모델 기능 기록(calllog·state)은 db.SessionLocal 을 호출 시점에 읽으므로 여기서 바꿔 끼운다."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import db, imagegen, main, models  # noqa: F401  (models: 테이블 등록)
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(k, raising=False)
    eng = create_engine(f"sqlite:///{tmp_path / 'state.db'}", connect_args={"check_same_thread": False})
    db.Base.metadata.create_all(eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=eng, autoflush=False))
    monkeypatch.setattr(db, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(main, "_ready", True)       # 첫 요청 초기화(실제 DB 시드)를 건너뛴다
    monkeypatch.setattr(imagegen, "_caps_cache", None)
    # 아이콘 격자 렌더(기본 4x3)는 격자 테스트에서만 켠다. 나머지 테스트는 아이콘마다 1장 렌더를 가정한다
    from app import icons
    monkeypatch.setattr(icons, "GRID_CELLS", 0)
