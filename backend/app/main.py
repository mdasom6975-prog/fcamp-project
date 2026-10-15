from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import func, select

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
from .routers_review import router as review_router
from .seed import deactivate_excluded, seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    DB_PATH.parent.mkdir(exist_ok=True)
    STORAGE_DIR.mkdir(exist_ok=True)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
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
app.include_router(presets_router)


@app.get("/api/health")
def health():
    """서버·DB·시드 상태 확인용."""
    with SessionLocal() as db:
        n = db.scalar(select(func.count()).select_from(models.PresetVersion))
    return {"status": "ok", "presets": n}
