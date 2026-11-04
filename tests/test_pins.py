"""핀 뱃지 세트 검사: 4×2 격자 1장(투명)에 핀 8개(로고 · 단어 · 이름 · 아이콘 세트 주제 5)를 그려 잘라 쓰고,
잘라낸 핀으로 4:5 시트를 배치한다. 외부 API 는 호출하지 않는다 (이미지 생성은 가짜 함수로 바꾼다)."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy.orm import sessionmaker

ICONS = ["보관함", "선물", "알림", "수집", "공개", "기대"]


def _grid(skip: tuple[int, ...] = ()) -> Image.Image:
    """1536×1024 투명 캔버스, 4×2 칸 가운데에 원 하나씩(skip 칸 번호는 비운다, 0부터)."""
    im = Image.new("RGBA", (1536, 1024), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    for k in range(8):
        if k in skip:
            continue
        cx, cy = 192 + (k % 4) * 384, 256 + (k // 4) * 512
        dr.ellipse((cx - 120, cy - 120, cx + 120, cy + 120), fill=(76, 105, 254, 255), outline=(201, 162, 39, 255), width=12)
    return im


def _one() -> Image.Image:
    im = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((300, 300, 700, 700), fill=(254, 150, 76, 255))
    return im


@pytest.fixture
def env(tmp_path, monkeypatch, pg_engine):
    from app import models, storage
    from app.db import Base
    from app.main import app
    from app.routers_assets import get_db

    engine = pg_engine
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
        db.add(d); db.flush()
        for i, n in enumerate(ICONS, 1):
            db.add(models.IconAsset(design_id=d.id, brand_version_id=bv.id, position=i, name=n, concept=f"{n} 오브젝트",
                                    svg="", png_path="x.png", width=256, height=256, style={"render": "3d"}))
        db.commit()
        did = d.id

    calls = []
    grids = {"next": _grid()}

    def fake_variant(prompt, w, h, reference, quality=None, model=None, product=None, kind=None, transparent=False):
        calls.append({"api": "edit", "prompt": prompt, "size": (w, h), "product": product, "kind": kind, "transparent": transparent})
        return grids["next"] if (w, h) == (1536, 1024) else _one()

    def fake_transparent(prompt, model=None, quality=None, kind="icon", size=None):
        calls.append({"api": "generate", "prompt": prompt, "size": size, "kind": kind})
        return grids["next"] if size == "1536x1024" else _one()

    from app import imagegen
    monkeypatch.setattr(imagegen, "generate_variant", fake_variant)
    monkeypatch.setattr(imagegen, "generate_transparent", fake_transparent)
    yield TestClient(app), did, calls, Local, grids
    app.dependency_overrides.clear()


def test_word_default_from_voice():
    from app import icons
    assert icons.pin_word_default({"description": "명확함 · 호기심 · 친근함"}) == "명확함"
    assert icons.pin_word_default({}) == ""


def test_grid_prompt_cells_in_order():
    from app import icons
    sv = icons.pin_style_vars([{"hex": "#4C69FE", "role": "주조색"}], {})
    cells = icons.pin_cells("쿠챠", "호기심", [(n, f"{n} 오브젝트") for n in ICONS[:5]], has_logo=True)
    p = icons.pin_grid_prompt("쿠챠", cells, sv, ["최저가"])
    assert "4칸 × 세로 2줄" in p and "투명" in p and "#4C69FE(주조색)" in p and "최저가" in p
    assert '1. 브랜드 로고 핀: 첨부한 이미지' in p and '2. 단어 핀' in p and '"호기심"' in p and '3. 이름 핀' in p
    assert '4. 주제 핀 "보관함": 소재는 보관함 오브젝트' in p and '8. 주제 핀 "공개"' in p
    mono = icons.pin_cells("쿠챠", "호기심", [("a", "")] * 5, has_logo=False)[0]
    assert "모노그램" in mono


def test_compose_sheet_is_4x5_and_skips_missing():
    from app import icons
    pieces, info = icons.split_blobs(_grid(), 4, 2, 8)
    assert info["failed"] == [] and all(p is not None for p in pieces)
    sheet = icons.compose_pin_sheet(pieces[:7] + [None], "#F6F7FA")
    assert sheet.size == (1024, 1280)
    assert sheet.getpixel((5, 5)) == (246, 247, 250), "배경은 브랜드 배경색"
    assert sheet.getpixel((512, round(0.42 * 1280))) != (246, 247, 250), "로고 칸(가운데)에 핀이 놓인다"


def test_create_splits_grid_and_lists(env):
    client, did, calls, _, _ = env
    j = client.get(f"/api/designs/{did}/pins").json()
    assert j["pins"] == [] and j["defaults"] == {"name": "쿠챠", "word": "명확함"}
    assert [t["name"] for t in j["topics"]] == ICONS and j["topic_source"] == "icons" and j["topic_count"] == 5

    r = client.post(f"/api/designs/{did}/pins", json={"word": "호기심", "topics": ICONS[1:6]})
    assert r.status_code == 200, r.text
    pin = r.json()
    assert (pin["width"], pin["height"]) == (1024, 1280)
    assert pin["meta"]["topics"] == ICONS[1:6] and pin["meta"]["missing"] == [] and pin["meta"]["retried"] == []
    assert [x["label"] for x in pin["pins"]] == ["로고", "호기심", "쿠챠"] + ICONS[1:6]
    assert all("path" not in x for x in pin["pins"]), "저장소 경로는 응답에 내보내지 않는다"
    assert len(calls) == 1
    c = calls[0]
    assert c["api"] == "edit" and c["transparent"] and c["kind"] == "pin" and c["size"] == (1536, 1024) and c["product"] is not None
    assert '주제 핀 "선물"' in c["prompt"] and '주제 핀 "보관함"' not in c["prompt"]

    assert Image.open(io.BytesIO(client.get(f"/api/pins/{pin['id']}/png").content)).size == (1024, 1280)
    assert Image.open(io.BytesIO(client.get(f"/api/pins/{pin['id']}/grid").content)).size == (1536, 1024)
    piece = Image.open(io.BytesIO(client.get(f"/api/pins/{pin['id']}/pieces/4").content))
    assert piece.mode == "RGBA" and piece.getpixel((0, 0))[3] == 0
    assert client.get(f"/api/pins/{pin['id']}/pieces/9").status_code == 404

    assert client.delete(f"/api/designs/{did}/pins/{pin['id']}").status_code == 200
    assert client.get(f"/api/designs/{did}/pins").json()["pins"] == []


def test_failed_cells_are_redrawn_one_by_one(env):
    """격자에서 못 잘라낸 칸(로고·주제 하나)만 1장씩 다시 그린다. 로고 칸은 로고를 첨부한다."""
    client, did, calls, _, grids = env
    grids["next"] = _grid(skip=(0, 5))
    pin = client.post(f"/api/designs/{did}/pins", json={}).json()
    assert pin["meta"]["retried"] == [1, 6] and pin["meta"]["missing"] == []
    assert pin["meta"]["topics"] == ICONS[:5], "주제를 안 고르면 아이콘 세트 앞에서부터"
    singles = calls[1:]
    assert len(singles) == 2
    logo_call = next(c for c in singles if "브랜드 로고 핀" in c["prompt"])
    assert logo_call["api"] == "edit" and logo_call["transparent"]
    assert next(c for c in singles if c is not logo_call)["api"] == "generate"


def test_too_many_failures_saves_partial_without_retry(env):
    client, did, calls, _, grids = env
    grids["next"] = _grid(skip=(1, 2, 3, 4))
    pin = client.post(f"/api/designs/{did}/pins", json={}).json()
    assert len(calls) == 1 and len(pin["meta"]["missing"]) == 4
    grids["next"] = Image.new("RGBA", (1536, 1024), (0, 0, 0, 0))
    assert client.post(f"/api/designs/{did}/pins", json={}).status_code == 502


def test_without_logo_uses_monogram(env):
    client, did, calls, Local, _ = env
    from app import models
    with Local() as db:
        bv = db.get(models.BrandVersion, db.get(models.Design, did).brand_version_id)
        bv.logo_asset_id = None
        db.commit()
    assert client.post(f"/api/designs/{did}/pins", json={}).status_code == 200
    assert calls[-1]["api"] == "generate" and calls[-1]["size"] == "1536x1024" and "모노그램" in calls[-1]["prompt"]


def test_rejects_bad_input(env):
    client, did, calls, _, _ = env
    assert client.post(f"/api/designs/{did}/pins", json={"word": "최저가"}).status_code == 400
    assert client.post(f"/api/designs/{did}/pins", json={"word": "가" * 13}).status_code == 400
    assert client.post(f"/api/designs/{did}/pins", json={"topics": ICONS[:4]}).status_code == 400
    assert client.post(f"/api/designs/{did}/pins", json={"topics": ICONS[:4] + ["없는 주제"]}).status_code == 400
    assert calls == []


def test_needs_five_topics(env):
    client, did, calls, Local, _ = env
    from sqlalchemy import delete
    from app import models
    with Local() as db:
        db.execute(delete(models.IconAsset)); db.commit()
    r = client.post(f"/api/designs/{did}/pins", json={})
    assert r.status_code == 400 and "아이콘 세트" in r.json()["detail"] and calls == []
