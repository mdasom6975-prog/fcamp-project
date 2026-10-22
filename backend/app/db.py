import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "app.db"   # DATABASE_URL 이 없을 때(로컬 개발)만 쓰는 SQLite 파일

# Vercel 함수의 파일 시스템은 요청이 끝나면 사라지는 /tmp 만 쓸 수 있다. 여기는 Supabase Storage 의 로컬 캐시이고,
# 파일의 원본은 storage.py 가 Supabase 에 올린다. 로컬 개발에서는 backend/storage 가 그대로 저장소다.
STORAGE_DIR = Path(os.getenv("STORAGE_DIR") or ("/tmp/storage" if os.getenv("VERCEL") else BASE_DIR / "storage"))


def database_url() -> str:
    """DATABASE_URL(Supabase Postgres 연결 문자열)이 있으면 그것을, 없으면 로컬 SQLite 를 쓴다."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{DB_PATH}"
    for old in ("postgres://", "postgresql://"):
        if url.startswith(old):
            return "postgresql+psycopg://" + url[len(old):]
    return url


def make_engine(url: str | None = None):
    url = url or database_url()
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    # 서버리스는 함수 인스턴스마다 커넥션을 쥐고 있으면 Supabase 연결 한도를 금방 쓴다. 풀링은 Supabase 풀러(6543)에 맡기고
    # 여기서는 요청마다 열고 닫는다. 트랜잭션 모드 풀러는 prepared statement 를 지원하지 않으므로 끈다.
    return create_engine(url, poolclass=NullPool, connect_args={"prepare_threshold": None})


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass
