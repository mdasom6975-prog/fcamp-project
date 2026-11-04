"""소상공인 광고 이미지: 프리셋·추천·숨은 브랜드·사진 품질 안내·캠페인 생성 흐름."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app import smallbiz


def test_presets_are_valid_and_forbidden_words_are_required_level():
    assert len(smallbiz.PRESETS) >= 6
    for p in smallbiz.PRESETS:
        b = smallbiz.brand_in(p)
        assert b.name.startswith(smallbiz.BRAND_PREFIX)
        assert set(smallbiz.COMMON_FORBIDDEN) <= set(b.forbidden_phrases)
        assert b.levels["forbidden_phrases"] == "필수"


def test_recommend_returns_known_ids_without_duplicates():
    ids = smallbiz.recommend("동네 수제 딸기 케이크")
    assert ids and len(ids) == len(set(ids)) and all(smallbiz.get(i) for i in ids)
    assert smallbiz.recommend("") 


@pytest.fixture
def env(tmp_path, monkeypatch, pg_engine):
    from app import copywriter, models, routers_copy
    from app.db import Base
    from app.main import app
    from app.routers_assets import get_db

    engine = pg_engine
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)

    def fake_generate_checked(brand_name, brand, brief, placement, limits, *a, **k):
        vs = [{"headline": f"제목{i}", "body": f"본문{i}", "cta": "보기", "violations": []} for i in (1, 2, 3)]
        return vs, 1
    monkeypatch.setattr(copywriter, "generate_checked", fake_generate_checked)
    monkeypatch.setattr(copywriter, "extras", lambda *a, **k: ([], {}))
    monkeypatch.setattr("app.routers_smallbiz.llm.chat_json", lambda *a, **k: {"message": "오늘의 케이크"})

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[get_db] = override
    with Local() as db:
        plat = models.Platform(name="테스트")
        db.add(plat); db.flush()
        for name, w, h in [("Instagram 피드", 1440, 1800), ("Instagram 스토리", 1080, 1920),
                           ("채널 메시지 와이드이미지형 800×600", 800, 600)]:
            pl = models.Placement(platform_id=plat.id, name=name, ad_type="테스트", content_kind="완성형")
            db.add(pl); db.flush()
            db.add(models.PresetVersion(placement_id=pl.id, production_width=w, production_height=h, status="확인됨",
                                        text_limits=[{"field": "headline", "max": 30}, {"field": "body", "max": 60}]))
        db.commit()
    try:
        yield TestClient(app), Local
    finally:
        app.dependency_overrides.clear()


def test_hidden_brand_is_created_once_and_not_listed(env):
    client, Local = env
    for _ in range(2):
        r = client.post("/api/smallbiz/campaigns", json={"what": "수제 딸기 케이크", "preset_id": "cafe_mood"})
        assert r.status_code == 200, r.text
    assert client.get("/api/brands").json() == []
    from app.models import Brand, Design
    with Local() as db:
        assert db.query(Brand).count() == 1
        d = db.get(Design, r.json()["design_id"])
        assert d.brief["key_message"] == "오늘의 케이크" and d.brief["smallbiz"]["preset_id"] == "cafe_mood"
        assert len(d.preset_version_ids) == 3


def test_style_change_switches_brand_keeps_input(env):
    client, Local = env
    did = client.post("/api/smallbiz/campaigns", json={"what": "치킨", "message": "바삭!"}).json()["design_id"]
    assert client.put(f"/api/smallbiz/campaigns/{did}/style", json={"preset_id": "sale_event"}).status_code == 200
    from app.models import Design
    with Local() as db:
        d = db.get(Design, did)
        assert d.brief["key_message"] == "바삭!" and d.brief["smallbiz"]["preset_id"] == "sale_event"
    assert client.put(f"/api/smallbiz/campaigns/{did}/style", json={"preset_id": "nope"}).status_code == 400


def test_unknown_preset_and_missing_photo_are_rejected(env):
    client, _ = env
    assert client.post("/api/smallbiz/campaigns", json={"what": "빵", "preset_id": "nope"}).status_code == 400
    assert client.post("/api/smallbiz/campaigns", json={"what": "빵", "photo_asset_id": 999}).status_code == 400


def _png(size, color):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


from app import cutout as _cutout_mod
_REAL_CUT = _cutout_mod.cut        # autouse 가짜로 바뀌기 전의 실제 함수


@pytest.fixture(autouse=True)
def _no_real_cutout(monkeypatch):
    """업로드 테스트가 실제 rembg 모델을 돌리지 않게 한다(누끼 처리는 아래 전용 테스트에서 가짜로 확인)."""
    from app import cutout
    monkeypatch.setattr(cutout, "cut", lambda im: None)


def test_photo_upload_saves_cutout_png_when_background_removed(env, monkeypatch):
    from app import cutout, storage
    client, _ = env
    def fake_cut(im):
        out = Image.new("RGBA", im.size, (0, 0, 0, 0))
        out.paste((200, 30, 30, 255), (im.width // 4, im.height // 4, im.width * 3 // 4, im.height * 3 // 4))
        return out
    monkeypatch.setattr(cutout, "cut", fake_cut)
    r = client.post("/api/smallbiz/photo", files={"file": ("a.jpg", _png((400, 300), (120, 120, 120)), "image/png")})
    assert r.status_code == 200 and r.json()["cutout"] is True
    assert client.get(f"/api/assets/{r.json()['id']}/file").headers["content-type"] == "image/png"


def test_photo_upload_keeps_original_when_cutout_unavailable(env):
    client, _ = env
    r = client.post("/api/smallbiz/photo", files={"file": ("a.png", _png((400, 300), (120, 120, 120)), "image/png")})
    assert r.json()["cutout"] is False
    assert client.get(f"/api/assets/{r.json()['id']}/file").headers["content-type"] == "image/jpeg"


def test_cutout_rejects_implausible_masks(monkeypatch):
    import sys, types
    from app import cutout
    masks = {}
    fake = types.ModuleType("rembg")
    fake.new_session = lambda name: object()
    fake.remove = lambda im, session=None: masks["m"]
    monkeypatch.setitem(sys.modules, "rembg", fake)
    monkeypatch.setattr(cutout, "_session", None)
    src = Image.new("RGB", (200, 200), "white")
    masks["m"] = Image.new("RGBA", (200, 200), (1, 2, 3, 0))                         # 아무것도 못 찾음
    assert _REAL_CUT(src) is None
    masks["m"] = Image.new("RGBA", (200, 200), (1, 2, 3, 255))                       # 전부 제품(배경을 못 지움)
    assert _REAL_CUT(src) is None
    m = Image.new("RGBA", (200, 200), (1, 2, 3, 0)); m.paste((9, 9, 9, 255), (50, 50, 150, 150))
    masks["m"] = m
    assert _REAL_CUT(src) is not None
    monkeypatch.setattr(cutout, "available", lambda: False)
    assert _REAL_CUT(src) is None


def test_photo_upload_resizes_and_warns(env):
    client, _ = env
    r = client.post("/api/smallbiz/photo", files={"file": ("a.png", _png((200, 200), (10, 10, 10)), "image/png")})
    assert r.status_code == 200
    w = " ".join(r.json()["warnings"])
    assert "해상도" in w and "어두" in w and "흐리" in w
    r = client.post("/api/smallbiz/photo", files={"file": ("a.png", _png((3000, 2000), (120, 120, 120)), "image/png")})
    assert max(r.json()["width"], r.json()["height"]) == 1280
    assert client.post("/api/smallbiz/photo", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 400


def test_pricing_info_lists_plan_and_prices(env):
    client, _ = env
    r = client.get("/api/smallbiz/pricing")
    assert r.status_code == 200
    d = r.json()
    groups = {p["group"] for p in d["image_prices"]}
    assert groups == {"세로형", "가로형"}        # 피드·스토리는 세로형 한 장을 공유, 채널 와이드는 가로형
    assert [p["calls"] for p in d["plan"]][2] == "2"
    assert d["image_total_usd"] == pytest.approx(sum(p["usd"] for p in d["image_prices"]), abs=1e-4)
    assert d["restyle"]["calls"] == 2
