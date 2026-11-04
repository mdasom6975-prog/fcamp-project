"""DB 연결. 로컬 개발과 운영(Vercel)이 같은 Supabase Postgres 를 쓴다 — 프로젝트만 dev / prod 로 나눈다.

- DATABASE_URL(backend/.env 또는 환경변수)이 없으면 바로 실패한다. 예전의 SQLite 폴백은 없앴다(로컬·운영의 DB 가 달라
  SQLite 에서만 통과하는 코드가 생겼고, pgvector 처럼 Postgres 에만 있는 기능을 쓸 수 없었다).
- 테스트는 conftest 가 임시 Postgres(conda-forge postgresql + pgvector)를 띄워 SessionLocal 을 바꿔 끼운다.
- 1회용 이전 도구(tools/migrate_to_supabase.py)만 sqlite:/// URL 로 make_engine 을 부른다.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")          # DATABASE_URL 을 엔진을 만들기 전에 읽어야 한다(copywriter 의 load_dotenv 보다 먼저 import 된다)

# 파일 저장소의 로컬 캐시. 원본은 Supabase Storage(storage.py). Vercel 함수는 요청이 끝나면 사라지는 /tmp 만 쓸 수 있다.
STORAGE_DIR = Path(os.getenv("STORAGE_DIR") or ("/tmp/storage" if os.getenv("VERCEL") else BASE_DIR / "storage"))

MISSING_URL = ("DATABASE_URL 이 없습니다. backend/.env 에 Supabase dev 프로젝트의 Transaction pooler 주소(포트 6543)를 넣으세요. "
               "항목 이름과 예시는 .env.example, 절차는 docs/11_Vercel_Supabase_배포.md 2절.")


def database_url() -> str:
    """DATABASE_URL(Supabase Postgres 연결 문자열). postgres:// · postgresql:// 는 psycopg 드라이버 표기로 바꾼다."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(MISSING_URL)
    for old in ("postgres://", "postgresql://"):
        if url.startswith(old):
            return "postgresql+psycopg://" + url[len(old):]
    return url


def make_engine(url: str | None = None):
    url = url or database_url()
    if url.startswith("sqlite"):        # 이전 도구의 원본(옛 로컬 DB)만 해당
        return create_engine(url, connect_args={"check_same_thread": False})
    # 서버리스는 함수 인스턴스마다 커넥션을 쥐고 있으면 Supabase 연결 한도를 금방 쓴다. 풀링은 Supabase 풀러(6543)에 맡기고
    # 여기서는 요청마다 열고 닫는다. 트랜잭션 모드 풀러는 prepared statement 를 지원하지 않으므로 끈다. 로컬도 같은 설정으로 돈다.
    return create_engine(url, poolclass=NullPool, connect_args={"prepare_threshold": None})


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass
