"""테스트 공통: 임시 Postgres 한 대를 세션 동안 띄우고, 테스트마다 템플릿에서 복제한 빈 DB 를 준다.

앱·운영이 Supabase Postgres 를 쓰므로 테스트도 같은 엔진(+ pgvector)으로 돈다. SQLite 로는 Postgres 의 길이 제한·JSON·확장을
검증할 수 없어 운영에서만 나는 오류가 있었다. 바이너리는 Docker 없이 conda-forge 로 받는다:
    conda create -n pgtest -c conda-forge postgresql pgvector
찾는 순서: PG_BIN 환경변수(bin 폴더) → /opt/anaconda3/envs/pgtest/bin → PATH 의 pg_ctl.
- 세션 시작: initdb → 빈 포트로 서버 시작(fsync 끔) → 템플릿 DB(vector 확장 + 전체 스키마) 생성
- 테스트마다: CREATE DATABASE … TEMPLATE 로 복제(수십 ms) → 끝나면 삭제. 네트워크·실제 데이터를 건드리지 않는다
- 세션 끝: 서버 정지, 데이터 폴더 삭제(tmp_path_factory)
"""
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("DATABASE_URL", "postgresql://unused@127.0.0.1:1/unused")   # app.db import 가 실패하지 않게. 실제 연결은 아래 픽스처가 바꿔 끼운다
os.environ.setdefault("ALLOW_LOCAL_STORAGE", "1")                                  # 테스트는 임시 폴더 저장소


def _pg_bin() -> Path:
    for cand in (os.getenv("PG_BIN"), "/opt/anaconda3/envs/pgtest/bin"):
        if cand and (Path(cand) / "pg_ctl").exists():
            return Path(cand)
    found = shutil.which("pg_ctl")
    if found:
        return Path(found).parent
    pytest.exit("테스트용 Postgres 바이너리를 찾지 못했습니다. conda create -n pgtest -c conda-forge postgresql pgvector 로 받거나 "
                "PG_BIN=<bin 폴더> 를 지정하세요.", returncode=3)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PgServer:
    """임시 Postgres 서버. url(dbname) 으로 접속 문자열을, create(empty) 로 새 DB 를 만든다."""

    def __init__(self, bin_dir: Path, root: Path):
        self.bin, self.data, self.port = bin_dir, root / "pgdata", _free_port()
        self._n = 0

    def _run(self, *args: str) -> None:
        r = subprocess.run([str(self.bin / args[0]), *args[1:]], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"{args[0]} 실패:\n{r.stdout}\n{r.stderr}")

    def start(self) -> None:
        self._run("initdb", "-D", str(self.data), "-U", "postgres", "-A", "trust", "-E", "UTF8", "--no-locale", "--no-sync")
        # 유닉스 소켓은 끈다(TCP 만): macOS 는 소켓 경로가 103바이트를 넘으면 못 만들고 pytest 임시 폴더는 그보다 길다
        opts = (f"-p {self.port} -c listen_addresses=127.0.0.1 -c unix_socket_directories='' "
                "-c fsync=off -c synchronous_commit=off -c full_page_writes=off -c log_min_messages=warning")
        self._run("pg_ctl", "-D", str(self.data), "-o", opts, "-l", str(self.data.parent / "pg.log"), "-w", "start")

    def stop(self) -> None:
        subprocess.run([str(self.bin / "pg_ctl"), "-D", str(self.data), "-m", "immediate", "-w", "stop"], capture_output=True)

    def url(self, dbname: str) -> str:
        return f"postgresql+psycopg://postgres@127.0.0.1:{self.port}/{dbname}"

    def admin(self, sql: str) -> None:
        from sqlalchemy import create_engine, text
        eng = create_engine(self.url("postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool)
        with eng.connect() as c:
            c.execute(text(sql))
        eng.dispose()

    def create(self, empty: bool = False) -> str:
        """새 DB 이름. 기본은 스키마가 든 템플릿 복제, empty=True 는 vector 확장만 있는 빈 DB(컬럼 보정 테스트용)."""
        self._n += 1
        name = f"t{self._n}"
        self.admin(f"CREATE DATABASE {name} TEMPLATE {'tpl_empty' if empty else 'tpl'}")
        return name

    def drop(self, name: str) -> None:
        self.admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture(scope="session")
def pg_server(tmp_path_factory):
    from sqlalchemy import text
    from app import db, models  # noqa: F401  (models: 테이블 등록)
    srv = PgServer(_pg_bin(), tmp_path_factory.mktemp("pg"))
    srv.start()
    try:
        srv.admin("CREATE DATABASE tpl_empty")
        with db.make_engine(srv.url("tpl_empty")).connect() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            c.commit()
        srv.admin("CREATE DATABASE tpl TEMPLATE tpl_empty")
        eng = db.make_engine(srv.url("tpl"))
        db.Base.metadata.create_all(eng)
        eng.dispose()
        yield srv
    finally:
        srv.stop()


@pytest.fixture
def pg_engine(pg_server):
    """이 테스트만의 빈 DB(전체 스키마 포함)에 붙은 엔진."""
    from app import db
    name = pg_server.create()
    eng = db.make_engine(pg_server.url(name))
    yield eng
    eng.dispose()
    pg_server.drop(name)


@pytest.fixture
def pg_empty_engine(pg_server):
    """테이블이 하나도 없는 DB 의 엔진(기존 DB 에 컬럼을 보정하는 테스트용)."""
    from app import db
    name = pg_server.create(empty=True)
    eng = db.make_engine(pg_server.url(name))
    yield eng
    eng.dispose()
    pg_server.drop(name)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch, pg_engine):
    """테스트가 실제 DB(Supabase)·저장소 파일을 건드리지 않게 임시 Postgres·임시 폴더로 돌린다.
    호출 기록·사용량·모델 기능 기록(calllog·state)은 db.SessionLocal 을 호출 시점에 읽으므로 여기서 바꿔 끼운다."""
    from sqlalchemy.orm import sessionmaker
    from app import db, imagegen, main
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(db, "engine", pg_engine)
    monkeypatch.setattr(main, "engine", pg_engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=pg_engine, autoflush=False))
    monkeypatch.setattr(main, "SessionLocal", db.SessionLocal)
    monkeypatch.setattr(db, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(main, "_ready", True)       # 첫 요청 초기화(실제 DB 시드)를 건너뛴다
    monkeypatch.setattr(imagegen, "_caps_cache", None)
    from app import modellog
    monkeypatch.setattr(modellog, "LOG_DIR", tmp_path / "logs")    # 모델 입출력 로그를 backend/logs 에 쓰지 않게
    # 아이콘 격자 렌더(기본 4x3)는 격자 테스트에서만 켠다. 나머지 테스트는 아이콘마다 1장 렌더를 가정한다
    from app import icons
    monkeypatch.setattr(icons, "GRID_CELLS", 0)
