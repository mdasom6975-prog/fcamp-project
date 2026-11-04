"""tools/sync_env.py: 환경 사이 복제 — 표 복사(id 유지·시퀀스 보정·--replace), 버킷 재귀 목록·복사(모의 Storage)."""
import importlib.util
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sync():
    spec = importlib.util.spec_from_file_location("sync_env", ROOT / "tools" / "sync_env.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_copy_tables_keeps_ids_and_fixes_sequences(sync, pg_engine, pg_server):
    from app import db
    dst_name = pg_server.create()
    dst = db.make_engine(pg_server.url(dst_name))
    try:
        with Session(pg_engine) as s:
            s.add(models.Brand(id=7, name="브랜드")); s.commit()
        counts = sync.copy_tables(pg_engine, dst, dry=False, replace=False)
        assert counts["brands"] == 1
        with Session(dst) as s:
            assert s.get(models.Brand, 7).name == "브랜드"
            s.add(models.Brand(name="다음")); s.commit()        # 시퀀스가 7 뒤로 맞춰져 있어야 충돌이 없다
            assert s.scalar(select(models.Brand.id).where(models.Brand.name == "다음")) == 8
        with pytest.raises(SystemExit):                          # 대상에 행이 있으면 멈춘다
            sync.copy_tables(pg_engine, dst, dry=False, replace=False)
        assert sync.copy_tables(pg_engine, dst, dry=False, replace=True)["brands"] == 1
        with Session(dst) as s:
            assert [b.name for b in s.scalars(select(models.Brand))] == ["브랜드"], "--replace 는 대상을 비우고 다시 채운다"
    finally:
        dst.dispose()
        pg_server.drop(dst_name)


def test_bucket_lists_recursively_and_copies(sync, monkeypatch):
    src_files = {"bg_1.png": b"A", "exports/exp_1.zip": b"Z", "thumbs/bg_1_360.jpg": b"T"}
    dst_files: dict[str, bytes] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/storage/v1/object/list/src":
            prefix = req.read() and __import__("json").loads(req.read())["prefix"]
            seen, out = set(), []
            for k in src_files:
                if prefix and not k.startswith(prefix + "/"):
                    continue
                rest = k[len(prefix) + 1:] if prefix else k
                head, _, tail = rest.partition("/")
                if head in seen:
                    continue
                seen.add(head)
                out.append({"name": head, "id": None if tail else "x"})
            return httpx.Response(200, json=out)
        if p.startswith("/storage/v1/object/authenticated/src/"):
            return httpx.Response(200, content=src_files[p.split("/src/", 1)[1]])
        if p == "/storage/v1/bucket/dst":
            return httpx.Response(200, json={})
        if req.method == "POST" and p.startswith("/storage/v1/object/dst/"):
            dst_files[p.split("/dst/", 1)[1]] = req.read()
            return httpx.Response(200, json={})
        return httpx.Response(500, text=p)

    src = sync.Bucket("https://a.supabase.co", "k", "src")
    dst = sync.Bucket("https://b.supabase.co", "k", "dst")
    for b in (src, dst):
        b.http = httpx.Client(transport=httpx.MockTransport(handler), headers=b.http.headers)
    assert sorted(src.keys()) == ["bg_1.png", "exports/exp_1.zip", "thumbs/bg_1_360.jpg"]
    assert sync.copy_files(src, dst, dry=True, replace=False) == 2          # 썸네일 캐시는 세지 않는다
    assert sync.copy_files(src, dst, dry=False, replace=False) == 2
    assert dst_files == {"bg_1.png": b"A", "exports/exp_1.zip": b"Z"}


def test_copy_tables_tolerates_older_source_schema(sync, pg_engine, pg_server):
    """운영이 옛 배포면 나중에 추가된 컬럼(knowledge_docs.visual)·테이블이 원본에 없다. 있는 컬럼만 읽고 빈 자리는 NULL 로 둔다."""
    from sqlalchemy import text
    from app import db
    dst_name = pg_server.create()
    dst = db.make_engine(pg_server.url(dst_name))
    try:
        with Session(pg_engine) as s:
            s.add(models.KnowledgeDoc(id=3, layer="brand", source_id="s1", title="문서")); s.commit()
        with pg_engine.begin() as c:
            c.execute(text("ALTER TABLE knowledge_docs DROP COLUMN visual"))
            c.execute(text("DROP TABLE IF EXISTS knowledge_chunks"))
        counts = sync.copy_tables(pg_engine, dst, dry=False, replace=False)
        assert counts["knowledge_docs"] == 1 and counts["knowledge_chunks"] == 0
        with Session(dst) as s:
            d = s.get(models.KnowledgeDoc, 3)
            assert d.title == "문서" and d.visual is None
    finally:
        dst.dispose()
        pg_server.drop(dst_name)
