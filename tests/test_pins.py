"""핀 뱃지 세트(4:5 한 장에 에나멜 핀 8개) 검사. 외부 API 는 호출하지 않는다 (이미지 생성은 가짜 함수로 바꾼다)."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app import models, storage
    from app.db import Base
    from app.main import app
    from app.routers_assets import get_db

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[get_db] = override

    with Local() as db:
        brand = models.Brand(name="쿠챠", description="랜덤 굿즈 커머스")
        db.add(brand); db.flush()
        b = io.BytesIO(); Image.new("RGBA", (200, 100), (76, 105, 254, 255)).save(b, "PNG")
        logo = models.Asset(brand_id=brand.id, kind="logo", file_path=storage.put("logo.png", b.getvalue()),
                            mime="image/png", bytes=len(b.getvalue()))
        db.add(logo); db.flush()
        bv = models.BrandVersion(brand_id=brand.id, version_no=1, logo_asset_id=logo.id,
                                 colors=[{"hex": "#4C69FE", "role": "주조색"}, {"hex": "#F6F7FA", "role": "배경색"}],
                                 voice={"description": "명확함 · 호기심 · 친근함"}, forbidden_phrases=["최저가"])
        db.add(bv); db.flush()
        brand.current_version_id = bv.id
        d = models.Design(brand_id=brand.id, brand_version_id=bv.id, kind="광고 이미지", preset_version_ids=[],
                          brief={"product_name": "랜덤 캡슐"})
        db.add(d); db.commit()
        did = d.id

    calls = []

    def fake_variant(prompt, w, h, reference, model=None, product=None, kind=None, **_):
        calls.append({"api": "edit", "prompt": prompt, "size": (w, h), "reference": reference, "product": product, "kind": kind})
        return Image.new("RGB", (w, h), (255, 200, 0))

    def fake_bg(prompt, w, h, model=None, kind="background", **_):
        calls.append({"api": "generate", "prompt": prompt, "size": (w, h), "kind": kind})
        return Image.new("RGB", (w, h), (255, 200, 0))

    from app import imagegen
    monkeypatch.setattr(imagegen, "generate_variant", fake_variant)
    monkeypatch.setattr(imagegen, "generate_background", fake_bg)
    yield TestClient(app), did, calls, Local
    app.dependency_overrides.clear()


def test_word_default_from_voice():
    from app import icons
    assert icons.pin_word_default({"description": "명확함 · 호기심 · 친근함"}) == "명확함"
    assert icons.pin_word_default({}) == ""


def test_prompt_has_logo_word_name_and_attach_numbers():
    from app import icons
    p = icons.pin_prompt("쿠챠", "호기심", "굿즈 커머스", {"product_name": "캡슐"}, {}, {}, [{"hex": "#4C69FE", "role": "주조색"}],
                         ["최저가"], logo_n=2, photo_n=1)
    assert "에나멜 핀 8개" in p and "4:5" in p
    assert '"호기심"' in p and '"쿠챠"' in p
    assert "첨부 2번 이미지의 브랜드 로고" in p and "첨부 1번 이미지는 이 브랜드의 상품 사진" in p
    assert "#4C69FE(주조색)" in p and "최저가" in p
    none = icons.pin_prompt("쿠챠", "호기심", "", {}, {}, {}, [], [], None, None)
    assert "모노그램" in none and "상품 사진" not in none


def test_crop_is_4x5():
    from app import icons
    assert icons.crop_pin_sheet(Image.new("RGB", (1024, 1536))).size == (1024, 1280)
    assert icons.crop_pin_sheet(Image.new("RGB", (1024, 1024))).size == (1024, 1280)


def test_create_list_png_delete(env):
    client, did, calls, _ = env
    j = client.get(f"/api/designs/{did}/pins").json()
    assert j["pins"] == [] and j["defaults"] == {"name": "쿠챠", "word": "명확함"} and j["has_logo"] is True

    r = client.post(f"/api/designs/{did}/pins", json={"word": "호기심"})
    assert r.status_code == 200, r.text
    pin = r.json()
    assert (pin["width"], pin["height"]) == (1024, 1280)
    assert pin["meta"]["name"] == "쿠챠" and pin["meta"]["word"] == "호기심" and pin["meta"]["logo"] is True
    c = calls[-1]
    assert c["api"] == "edit" and c["kind"] == "pin" and c["size"] == (1024, 1536)
    assert c["reference"] is None and c["product"] is not None, "상품 사진이 없으면 로고만 첨부"
    assert "첨부 1번 이미지의 브랜드 로고" in c["prompt"]

    png = client.get(f"/api/pins/{pin['id']}/png")
    assert png.status_code == 200 and Image.open(io.BytesIO(png.content)).size == (1024, 1280)
    assert len(client.get(f"/api/designs/{did}/pins").json()["pins"]) == 1

    assert client.delete(f"/api/designs/{did}/pins/{pin['id']}").status_code == 200
    assert client.get(f"/api/designs/{did}/pins").json()["pins"] == []


def test_without_logo_uses_plain_generation(env):
    client, did, calls, Local = env
    from app import models
    with Local() as db:
        bv = db.get(models.BrandVersion, db.get(models.Design, did).brand_version_id)
        bv.logo_asset_id = None
        db.commit()
    assert client.post(f"/api/designs/{did}/pins", json={}).status_code == 200
    assert calls[-1]["api"] == "generate" and calls[-1]["kind"] == "pin" and "모노그램" in calls[-1]["prompt"]


def test_rejects_forbidden_and_long_text(env):
    client, did, calls, _ = env
    assert client.post(f"/api/designs/{did}/pins", json={"word": "최저가"}).status_code == 400
    assert client.post(f"/api/designs/{did}/pins", json={"word": "가" * 13}).status_code == 400
    assert calls == []
