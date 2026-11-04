import base64
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException   # StaticFiles 가 던지는 쪽(fastapi.HTTPException 은 이것의 하위 클래스)
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from . import models, state  # noqa: F401  (models: 테이블 등록)
from . import storage
from .db import BASE_DIR, STORAGE_DIR, Base, SessionLocal, engine
from .routers_analysis import router as analysis_router
from .routers_assets import router as assets_router
from .routers_brands import router as brands_router
from .routers_copy import router as copy_router
from .routers_edit import router as edit_router
from .routers_export import router as export_router
from .routers_icons import router as icons_router
from .routers_images import router as images_router
from .routers_designs import router as designs_router
from .routers_presets import router as presets_router
from .routers_spec_update import router as spec_update_router
from .routers_review import router as review_router
from .routers_evolution import router as evolution_router
from .routers_smallbiz import router as smallbiz_router
from .seed import deactivate_excluded, seed, sync_conditions


_init_lock = threading.Lock()
_ready = False


def _added_columns() -> None:
    """이미 있는 테이블에 새로 생긴 컬럼. create_all 은 기존 테이블에 컬럼을 추가하지 않으므로 없을 때만 추가한다.
    외래키 제약은 새로 만든 DB 에만 생기고(create_all), 여기서 추가한 컬럼은 정수 컬럼이다."""
    if "group_id" not in {c["name"] for c in inspect(engine).get_columns("brands")}:
        with engine.begin() as conn:      # 브랜드 그룹(아모레퍼시픽 → 설화수 등)
            conn.execute(text("ALTER TABLE brands ADD COLUMN group_id INTEGER"))
    insp = inspect(engine)
    if insp.has_table("copy_variants") and "long_body" not in {c["name"] for c in insp.get_columns("copy_variants")}:
        with engine.begin() as conn:      # 긴 지면의 게시 문구
            conn.execute(text("ALTER TABLE copy_variants ADD COLUMN long_body TEXT NOT NULL DEFAULT ''"))
    if insp.has_table("knowledge_docs") and "industry" not in {c["name"] for c in insp.get_columns("knowledge_docs")}:
        with engine.begin() as conn:      # 규제 문서의 업종(업종 감지 시 검색 없이 전부 넣는다)
            conn.execute(text("ALTER TABLE knowledge_docs ADD COLUMN industry VARCHAR(20)"))
    if insp.has_table("knowledge_docs") and "visual" not in {c["name"] for c in insp.get_columns("knowledge_docs")}:
        with engine.begin() as conn:      # 배경 이미지용 시각 소재·연출 후보
            conn.execute(text("ALTER TABLE knowledge_docs ADD COLUMN visual JSON"))
    if insp.has_table("knowledge_chunks") and "aliases" not in {c["name"] for c in insp.get_columns("knowledge_chunks")}:
        with engine.begin() as conn:      # 제품 별칭(브리프 상품명과 제품 자료를 정확히 잇는다)
            conn.execute(text("ALTER TABLE knowledge_chunks ADD COLUMN aliases JSON"))
    if insp.has_table("knowledge_chunks"):
        _vector_column(insp)


def _ensure_vector_extension() -> None:
    """pgvector 확장. Supabase 는 대시보드(Database → Extensions → vector)에서도 켤 수 있고, 여기서도 시도한다."""
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except Exception as e:      # 권한이 없는 환경: 안내만 남기고 create_all 이 실패하면 그 오류가 원인을 말해 준다
        log.warning("vector 확장을 만들지 못했습니다(%s). Supabase 대시보드 Database → Extensions 에서 vector 를 켜세요", e)


def _vector_column(insp) -> None:
    """knowledge_chunks.vec (pgvector): 없으면 만들고 JSON embedding 에서 채운 뒤 HNSW 인덱스를 만든다(2단계 전환, 1회)."""
    from .models import EMBED_DIMS
    cols = {c["name"] for c in insp.get_columns("knowledge_chunks")}
    with engine.begin() as conn:
        if "vec" not in cols:
            conn.execute(text(f"ALTER TABLE knowledge_chunks ADD COLUMN vec vector({EMBED_DIMS})"))
        # 옛 배포가 JSON 만 쓰고 남긴 조각: vec 가 비었고 차원이 맞으면 채운다
        conn.execute(text(f"UPDATE knowledge_chunks SET vec = embedding::text::vector "
                          f"WHERE vec IS NULL AND json_array_length(embedding::json) = {EMBED_DIMS}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_vec ON knowledge_chunks "
                          "USING hnsw (vec vector_cosine_ops) WITH (m = 16, ef_construction = 64)"))


def _check_storage() -> None:
    """파일 원본은 Supabase Storage 에만 둔다. 설정이 없으면 로컬 폴더에만 써서 운영과 어긋나므로, 명시적으로 허용(ALLOW_LOCAL_STORAGE=1,
    테스트·오프라인 점검용)하지 않으면 시작하지 않는다."""
    if not storage.remote() and os.getenv("ALLOW_LOCAL_STORAGE", "").strip() != "1":
        raise RuntimeError("SUPABASE_URL · SUPABASE_SERVICE_KEY 가 없습니다. backend/.env 에 dev 프로젝트의 값을 넣으세요(.env.example). "
                           "로컬 폴더에만 저장하며 띄우려면 ALLOW_LOCAL_STORAGE=1.")


INIT_KEY = "schema_init"      # KV 키: 초기화를 마친 배포(VERCEL_GIT_COMMIT_SHA)를 적어 둔다
LOCK_ID = 7351001
LOCK_WAIT_SEC = 90
log = logging.getLogger("app.init")


def _initialized_for_deploy() -> bool:
    """이 배포에서 스키마·시드를 이미 마쳤는가(쿼리 1번). Vercel 이 아니면(배포 ID 없음) 항상 False 라 매번 확인한다.
    리전이 멀면 쿼리마다 왕복이 길어, 인스턴스가 켜질 때마다 테이블 확인·시드를 하면 제한 시간을 넘긴다."""
    deploy = os.getenv("VERCEL_GIT_COMMIT_SHA", "")
    if not deploy:
        return False
    try:
        return (state.get(INIT_KEY) or {}).get("deploy") == deploy
    except SQLAlchemyError:      # kv 테이블이 아직 없는 첫 배포
        return False


def _acquire_lock(conn) -> bool:
    """다른 인스턴스가 초기화 중이면 끝날 때까지 기다린다. 기다리는 동안 그쪽이 끝냈으면(표식이 생기면) False.
    블로킹 락 대신 try-lock 을 돌려, DB 의 statement timeout 에 걸리지 않게 한다. 락은 트랜잭션이 끝나면 풀려
    트랜잭션 모드 풀러에서도 안전하다."""
    deadline = time.monotonic() + LOCK_WAIT_SEC
    while not conn.execute(text("SELECT pg_try_advisory_xact_lock(:i)"), {"i": LOCK_ID}).scalar():
        if time.monotonic() > deadline:
            raise TimeoutError("다른 인스턴스의 DB 초기화가 끝나지 않습니다")
        time.sleep(1)
        if _initialized_for_deploy():
            return False
    return True


def init_app() -> None:
    """스키마 생성 + 규격 시드. 프로세스(함수 인스턴스)당 한 번, Vercel 에서는 배포당 한 번만 한다.
    Vercel 은 ASGI lifespan 을 보장하지 않으므로 미들웨어가 첫 요청에서도 부른다. 인스턴스 여러 개가 동시에 처음 뜰 때
    시드가 겹치지 않게 트랜잭션 advisory lock 을 잡는다(로컬도 같은 Postgres 라 같은 경로를 탄다)."""
    global _ready
    if _ready:
        return
    with _init_lock:
        if _ready:
            return
        if _initialized_for_deploy():
            _ready = True
            return
        _check_storage()
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        with engine.connect() as lock_conn:
            try:
                if _acquire_lock(lock_conn):
                    if not _initialized_for_deploy():    # 락을 기다리는 사이 다른 인스턴스가 끝냈을 수 있다
                        _ensure_vector_extension()
                        Base.metadata.create_all(engine)
                        _added_columns()
                        with SessionLocal() as db:
                            seed(db)
                            sync_conditions(db)      # 시드의 조건·상태가 바뀌면 기존 지면에도 반영
                            deactivate_excluded(db)
                        if os.getenv("VERCEL_GIT_COMMIT_SHA"):
                            state.put(INIT_KEY, {"deploy": os.environ["VERCEL_GIT_COMMIT_SHA"]})
            finally:
                lock_conn.commit()
        _ready = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_app()
    except Exception:    # 초기화 실패가 인스턴스 전체를 죽이면 원인이 로그에만 남고 모든 요청이 500 이 된다. 다음 요청이 다시 시도한다
        log.exception("DB 초기화 실패 (첫 요청에서 다시 시도합니다)")
    yield


# APP_PUBLIC=1 (외부 공개 시연): API 문서(/docs, /redoc, /openapi.json)를 끈다
_PUBLIC = os.getenv("APP_PUBLIC", "").strip() == "1"
app = FastAPI(title="브랜드 마케팅 콘텐츠 제작 플랫폼", lifespan=lifespan,
              docs_url=None if _PUBLIC else "/docs", redoc_url=None if _PUBLIC else "/redoc",
              openapi_url=None if _PUBLIC else "/openapi.json")


class BasicAuthMiddleware:
    """APP_PASSWORD 가 비어 있지 않으면 모든 요청에 HTTP Basic 인증(APP_USER, 기본 demo)을 요구한다.
    /api/health 는 예외. 값은 요청마다 환경변수에서 읽는다(테스트에서 재로드 없이 바꿀 수 있게)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/api/health":
            return await self.app(scope, receive, send)
        password = os.getenv("APP_PASSWORD", "")
        if not password:
            return await self.app(scope, receive, send)
        user = os.getenv("APP_USER", "demo")
        header = next((v for k, v in scope["headers"] if k == b"authorization"), b"")
        if not self._ok(header, user, password):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"www-authenticate", b'Basic realm="brand-studio"'), (b"content-type", b"text/plain; charset=utf-8")]})
            await send({"type": "http.response.body", "body": "로그인이 필요합니다".encode()})
            return
        return await self.app(scope, receive, send)

    @staticmethod
    def _ok(header: bytes, user: str, password: str) -> bool:
        scheme, _, token = header.partition(b" ")
        if scheme.lower() != b"basic":
            return False
        try:
            given_user, _, given_pw = base64.b64decode(token.strip(), validate=True).decode().partition(":")
        except Exception:
            return False
        # 둘 다 비교해 어느 쪽이 틀렸는지 응답 시간으로 드러나지 않게 한다
        return secrets.compare_digest(given_user.encode(), user.encode()) & secrets.compare_digest(given_pw.encode(), password.encode())




class InitMiddleware:
    """첫 요청에서 init_app() 을 한 번 실행한다(lifespan 이 돌지 않는 서버리스 환경용). 인증 뒤에 둬서 미인증 요청은 DB 를 건드리지 않는다."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not _ready:
            try:
                await run_in_threadpool(init_app)
            except Exception:
                log.exception("DB 초기화 실패")      # 요청은 그대로 보내, 라우터가 DB 오류를 응답으로 돌려주게 한다
        return await self.app(scope, receive, send)


app.add_middleware(InitMiddleware)
app.add_middleware(BasicAuthMiddleware)       # 나중에 추가한 쪽이 바깥이므로 인증 → 초기화 → 라우터 순서가 된다
app.include_router(assets_router)
app.include_router(evolution_router)
app.include_router(brands_router)
app.include_router(copy_router)
app.include_router(images_router)
app.include_router(icons_router)
app.include_router(edit_router)
app.include_router(review_router)
app.include_router(export_router)
app.include_router(designs_router)
app.include_router(analysis_router)
app.include_router(spec_update_router)  # /presets/{id} 보다 먼저 등록해야 update-status 가 가려지지 않는다
app.include_router(presets_router)
app.include_router(smallbiz_router)


@app.get("/api/health")
def health():
    """서버·DB·시드 상태 확인용."""
    with SessionLocal() as db:
        n = db.scalar(select(func.count()).select_from(models.PresetVersion))
    return {"status": "ok", "presets": n}


class _SpaFiles(StaticFiles):
    """빌드된 프론트(frontend/dist)를 루트에서 서빙한다. 없는 경로는 index.html 로(단일 페이지 앱),
    /api/... 와 API 문서 경로는 그대로 404 (API 오타가 HTML 200 으로 보이지 않게, APP_PUBLIC 로 끈 문서가 되살아나지 않게)."""

    NO_FALLBACK = ("/api/", "/docs", "/redoc", "/openapi.json")

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404 or scope["path"].startswith(self.NO_FALLBACK):
                raise
            return await super().get_response("index.html", scope)


DIST_DIR = BASE_DIR.parent / "frontend" / "dist"
if (DIST_DIR / "index.html").exists():          # 라우터 등록 뒤에 mount 해야 /api 가 가려지지 않는다
    app.mount("/", _SpaFiles(directory=DIST_DIR, html=True), name="frontend")
