"""tools/migrate_to_supabase.py: SQLite → 대상 DB 복사, 경로를 키로 바꾸기, 로그 파일 이관, 파일 업로드(모의 Storage)."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import db, models, storage

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def mig():
    spec = importlib.util.spec_from_file_location("migrate_to_supabase", ROOT / "tools" / "migrate_to_supabase.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_migration_copies_rows_normalizes_paths_and_uploads(mig, tmp_path, monkeypatch):
    # 원본: 옛 방식(절대 경로)으로 파일 경로를 가진 SQLite
    old_storage = tmp_path / "old" / "storage"
    (old_storage / "thumbs").mkdir(parents=True)
    (old_storage / "bg_1.png").write_bytes(b"PNG1")
    (old_storage / "thumbs" / "bg_1_360.jpg").write_bytes(b"T")
    src = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    db.Base.metadata.create_all(src)
    with Session(src) as s:
        s.add(models.Brand(id=7, name="브랜드"))
        s.add(models.Asset(id=3, brand_id=7, kind="logo", file_path=str(old_storage / "bg_1.png"), mime="image/png", bytes=4))
        s.commit()
    data = tmp_path / "old" / "data"
    (data / "prompt_log").mkdir(parents=True)
    (data / "prompt_log" / "p1.json").write_text(json.dumps({"prompt": "P"}), encoding="utf-8")
    (data / "api_calls.jsonl").write_text(json.dumps({"design_id": 5, "date": "2026-10-20", "api": "chat"}) + "\n{깨진줄\n")

    dst = create_engine(f"sqlite:///{tmp_path / 'new.db'}")
    db.Base.metadata.create_all(dst)
    bucket: dict[str, bytes] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path.startswith("/storage/v1/object/brand-studio/"):
            bucket[req.url.path.removeprefix("/storage/v1/object/brand-studio/")] = req.content
            return httpx.Response(200, json={})
        return httpx.Response(500)

    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    monkeypatch.setattr(storage, "_client", httpx.Client(transport=httpx.MockTransport(handler)))

    counts = mig.copy_tables(src, dst, dry=False)
    assert counts["brands"] == 1 and counts["assets"] == 1
    assert mig.copy_logs(dst, data, dry=False) == {"api_calls": 1, "prompt_logs": 1, "judgments": 0, "kv": 0}
    assert mig.upload_files(old_storage, dry=False) == 1          # 썸네일 캐시는 올리지 않는다
    assert list(bucket) == ["bg_1.png"]
    with Session(dst) as s:
        assert s.get(models.Asset, 3).file_path == "bg_1.png" and s.get(models.Brand, 7).name == "브랜드"
        assert s.scalars(select(models.PromptLog.text)).one() == {"prompt": "P"}
        assert s.scalars(select(models.ApiCall.design_id)).one() == 5

    with pytest.raises(SystemExit):        # 대상에 이미 행이 있으면 멈춘다
        mig.copy_tables(src, dst, dry=False)
