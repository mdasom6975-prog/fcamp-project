import base64
import os
import secrets
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException   # StaticFiles 가 던지는 쪽(fastapi.HTTPException 은 이것의 하위 클래스)
from sqlalchemy import func, inspect, select, text

from . import models  # noqa: F401  (테이블 등록)
from .db import BASE_DIR, DB_PATH, STORAGE_DIR, Base, SessionLocal, engine
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
from .seed import deactivate_excluded, seed, sync_conditions


_init_lock = threading.Lock()
_ready = False


def _legacy_sqlite_columns() -> None:
    """create_all 은 기존 테이블에 컬럼을 추가하지 않으므로, 없으면 직접 추가한다 (SQLite 로컬 DB 의 기존 데이터 보존).
    Postgres 는 새로 만든 스키마라 필요 없다."""
    if "description" not in {c["name"] for c in inspect(engine).get_columns("brands")}:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE brands ADD COLUMN description VARCHAR(300) NOT NULL DEFAULT ''"))
    bv_cols = {c["name"] for c in inspect(engine).get_columns("brand_versions")}
    if "logo_square_asset_id" not in bv_cols:
        with engine.begin() as conn:      # 정사각 로고 자산(PMax 등 1:1 로고 조건)
            conn.execute(text("ALTER TABLE brand_versions ADD COLUMN logo_square_asset_id INTEGER"))
    if "logo_position" not in bv_cols:
        with engine.begin() as conn:      # 합성 로고 자리(브랜드 고정)
            conn.execute(text("ALTER TABLE brand_versions ADD COLUMN logo_position VARCHAR(10)"))
    icon_cols = {c["name"] for c in inspect(engine).get_columns("icon_assets")}
    for col, ddl in (("prompt", "TEXT"), ("source_path", "VARCHAR(500)")):   # 3D 아이콘 전환으로 추가된 컬럼
        if col not in icon_cols:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE icon_assets ADD COLUMN {col} {ddl}"))


def init_app() -> None:
    """스키마 생성 + 규격 시드. 프로세스(함수 인스턴스)당 한 번만 한다.
    Vercel 은 ASGI lifespan 을 보장하지 않으므로 미들웨어가 첫 요청에서도 부른다. 인스턴스 여러 개가 동시에 처음 뜰 때
    시드가 겹치지 않게 Postgres 에서는 트랜잭션 advisory lock 을 잡는다."""
    global _ready
    if _ready:
        return
    with _init_lock:
        if _ready:
            return
        if engine.dialect.name == "sqlite":
            DB_PATH.parent.mkdir(exist_ok=True)
        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        with engine.connect() as lock_conn:
            if engine.dialect.name == "postgresql":
                lock_conn.execute(text("SELECT pg_advisory_xact_lock(7351001)"))   # 트랜잭션이 끝나면 풀려, 트랜잭션 모드 풀러에서도 안전
            try:
                Base.metadata.create_all(engine)
                if engine.dialect.name == "sqlite":
                    _legacy_sqlite_columns()
                with SessionLocal() as db:
                    seed(db)
                    sync_conditions(db)      # 시드의 조건·상태가 바뀌면 기존 지면에도 반영
                    deactivate_excluded(db)
            finally:
                lock_conn.commit()
        _ready = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_app()
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
            await run_in_threadpool(init_app)
        return await self.app(scope, receive, send)


app.add_middleware(InitMiddleware)
app.add_middleware(BasicAuthMiddleware)       # 나중에 추가한 쪽이 바깥이므로 인증 → 초기화 → 라우터 순서가 된다
app.include_router(assets_router)
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
