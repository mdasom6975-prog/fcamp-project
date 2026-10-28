"""브랜드 삭제: 버전·캠페인·생성 결과·업로드 파일이 함께 지워지고, 다른 브랜드의 것은 남는다."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import storage
from app.db import Base
from app.main import app
from app.models import Asset, Brand, BrandVersion, Design, GeneratedImage
from app.routers_assets import get_db


@pytest.fixture
def Local(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        yield Local
    finally:
        app.dependency_overrides.clear()


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGBA", (200, 200), (255, 0, 0, 255)).save(b, "PNG")
    return b.getvalue()


def _brand(c, name):
    r = c.post("/api/assets", data={"kind": "logo"}, files={"file": ("l.png", _png(), "image/png")})
    aid = r.json()["id"]
    b = c.post("/api/brands", json={"name": name, "logo_asset_id": aid}).json()
    d = c.post("/api/designs", json={"brand_id": b["id"], "kind": "광고 이미지", "brief": {"product_name": "상품"}}).json()
    return b["id"], aid, d["id"]


def test_delete_brand_cascades_and_keeps_others(Local):
    c = TestClient(app)
    a_id, a_logo, a_design = _brand(c, "에이")
    b_id, b_logo, _ = _brand(c, "비")
    c.put(f"/api/brands/{a_id}", json={"name": "에이", "logo_asset_id": a_logo})      # 버전 2
    with Local() as s:
        key = "gen_test.png"
        storage.put(key, _png(), "image/png")
        s.add(GeneratedImage(design_id=a_design, preset_version_id=1, kind="background", file_path=key, width=1, height=1))
        logo_key = s.get(Asset, a_logo).file_path
        s.commit()

    r = c.delete(f"/api/brands/{a_id}")
    assert r.status_code == 200 and r.json()["designs"] == 1 and r.json()["versions"] == 2

    with Local() as s:
        assert s.get(Brand, a_id) is None and s.get(Asset, a_logo) is None
        assert not s.scalars(select(BrandVersion).where(BrandVersion.brand_id == a_id)).all()
        assert not s.scalars(select(Design).where(Design.brand_id == a_id)).all()
        assert not s.scalars(select(GeneratedImage)).all()
        assert s.get(Brand, b_id) and s.get(Asset, b_logo)
    assert not storage.exists("gen_test.png") and not storage.exists(logo_key)
    assert c.get(f"/api/brands/{b_id}").status_code == 200
    assert c.get(f"/api/brands/{a_id}").status_code == 404
    assert c.delete(f"/api/brands/{a_id}").status_code == 404
