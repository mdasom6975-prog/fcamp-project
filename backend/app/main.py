from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import func, inspect, select, text

from . import models  # noqa: F401  (테이블 등록)
from .db import DB_PATH, STORAGE_DIR, Base, SessionLocal, engine
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    DB_PATH.parent.mkdir(exist_ok=True)
    STORAGE_DIR.mkdir(exist_ok=True)
    Base.metadata.create_all(engine)
    # create_all 은 기존 테이블에 컬럼을 추가하지 않으므로, 없으면 직접 추가한다 (기존 데이터 보존)
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
    with SessionLocal() as db:
        seed(db)
        sync_conditions(db)      # 시드의 조건·상태가 바뀌면 기존 지면에도 반영
        deactivate_excluded(db)
    yield


app = FastAPI(title="브랜드 마케팅 콘텐츠 제작 플랫폼", lifespan=lifespan)
app.include_router(assets_router)
app.include_router(brands_router)
app.include_router(copy_router)
app.include_router(images_router)
app.include_router(icons_router)
app.include_router(edit_router)
app.include_router(review_router)
app.include_router(export_router)
app.include_router(designs_router)
app.include_router(spec_update_router)  # /presets/{id} 보다 먼저 등록해야 update-status 가 가려지지 않는다
app.include_router(presets_router)


@app.get("/api/health")
def health():
    """서버·DB·시드 상태 확인용."""
    with SessionLocal() as db:
        n = db.scalar(select(func.count()).select_from(models.PresetVersion))
    return {"status": "ok", "presets": n}
