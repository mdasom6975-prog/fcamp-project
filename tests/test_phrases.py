"""필수·금지 표현의 우선순위: 캠페인 입력(brief)이 우선, 비어 있으면 브랜드 규칙. 카피 생성·검수가 이 규칙을 따르는지 검사.
외부 API 는 호출하지 않는다 (generate_checked 를 가짜로 바꾼다)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker


class _BV:
    voice, levels, colors, fonts, icon_style, logo_asset_id = {}, {}, [], [], {}, None
    required_phrases = ["로켓배송", "쿠팡"]
    forbidden_phrases = ["최저가"]


def test_effective_prefers_campaign_and_falls_back_per_list():
    from app import phrases
    e = phrases.effective(_BV(), {})
    assert e["required_phrases"] == ["로켓배송", "쿠팡"] and e["required_source"] == "브랜드"
    assert e["forbidden_phrases"] == ["최저가"] and e["forbidden_source"] == "브랜드"
    e = phrases.effective(_BV(), {"required_phrases": [" 프레시 ", "", "프레시"], "forbidden_phrases": []})
    assert e["required_phrases"] == ["프레시"] and e["required_source"] == "캠페인"
    assert e["forbidden_phrases"] == ["최저가"] and e["forbidden_source"] == "브랜드", "금지는 비어 있으니 브랜드 값"
    assert phrases.effective(None, {"forbidden_phrases": ["x"]})["required_phrases"] == []
    assert phrases.rules(_BV(), {"forbidden_phrases": ["공짜"]})["forbidden_phrases"] == ["공짜"]


@pytest.fixture
def env(tmp_path, monkeypatch, pg_engine):
    from app import copywriter, models
    from app.db import Base
    from app.main import app
    from app.routers_assets import get_db

    engine = pg_engine
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)
    seen: list[dict] = []

    def fake_generate_checked(brand_name, brand, brief, placement, limits, knowledge=None):
        seen.append(brand)
        v = {"headline": "프레시한 리빙", "body": "본문", "cta": "보기"}
        v["violations"] = copywriter.check(v, limits, brand)
        return [v], 1
    monkeypatch.setattr(copywriter, "generate_checked", fake_generate_checked)
    monkeypatch.setattr(copywriter, "extras", lambda *a, **k: ([], {}))

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[get_db] = override

    with Local() as db:
        brand = models.Brand(name="쿠팡")
        db.add(brand); db.flush()
        bv = models.BrandVersion(brand_id=brand.id, version_no=1, required_phrases=["로켓배송"], forbidden_phrases=["최저가"],
                                 levels={"required_phrases": "필수", "forbidden_phrases": "필수"})
        db.add(bv); db.flush()
        brand.current_version_id = bv.id
        plat = models.Platform(name="테스트")
        db.add(plat); db.flush()
        pl = models.Placement(platform_id=plat.id, name="피드", ad_type="테스트", content_kind="완성형")
        db.add(pl); db.flush()
        pv = models.PresetVersion(placement_id=pl.id, production_width=1440, production_height=1800, status="확인됨",
                                  text_limits=[])
        db.add(pv); db.flush()
        d = models.Design(brand_id=brand.id, brand_version_id=bv.id, kind="광고 이미지", preset_version_ids=[pv.id],
                          brief={"product_name": "리빙"})
        db.add(d); db.commit()
        ids = {"design": d.id, "brand": brand.id}

    client = TestClient(app)
    try:
        yield {"client": client, "ids": ids, "seen": seen}
    finally:
        app.dependency_overrides.clear()


def test_copy_uses_brand_phrases_when_campaign_empty(env):
    c, did, seen = env["client"], env["ids"]["design"], env["seen"]
    out = c.post(f"/api/designs/{did}/copy").json()
    assert seen[-1]["required_phrases"] == ["로켓배송"] and seen[-1]["forbidden_phrases"] == ["최저가"]
    v = out["groups"][0]["variants"][0]
    assert any(x["type"] == "required_missing" and "로켓배송" in x["detail"] for x in v["violations"])


def test_copy_uses_campaign_phrases_when_given(env):
    c, did, seen = env["client"], env["ids"]["design"], env["seen"]
    r = c.patch(f"/api/designs/{did}", json={"brief": {"product_name": "리빙", "required_phrases": ["프레시"],
                                                       "forbidden_phrases": []}})
    assert r.status_code == 200
    j = r.json()
    assert j["brief"]["required_phrases"] == ["프레시"]
    assert j["effective_phrases"] == {"required_phrases": ["프레시"], "required_source": "캠페인",
                                      "forbidden_phrases": ["최저가"], "forbidden_source": "브랜드"}
    out = c.post(f"/api/designs/{did}/copy").json()
    assert seen[-1]["required_phrases"] == ["프레시"] and seen[-1]["forbidden_phrases"] == ["최저가"]
    assert out["groups"][0]["variants"][0]["violations"] == []


def test_brief_rejects_overlap_and_cleans(env):
    c, did = env["client"], env["ids"]["design"]
    r = c.patch(f"/api/designs/{did}", json={"brief": {"product_name": "리빙", "required_phrases": ["A"], "forbidden_phrases": ["A"]}})
    assert r.status_code == 422
    r = c.patch(f"/api/designs/{did}", json={"brief": {"product_name": "리빙", "required_phrases": [" A ", "A", ""]}})
    assert r.status_code == 200 and r.json()["brief"]["required_phrases"] == ["A"]


def test_review_uses_campaign_phrases(env):
    """검수의 브랜드 규칙 dict 도 캠페인 입력을 우선한다."""
    from app import models, routers_review
    c, did = env["client"], env["ids"]["design"]
    c.patch(f"/api/designs/{did}", json={"brief": {"product_name": "리빙", "forbidden_phrases": ["공짜"]}})
    bv = _BV()
    bd = routers_review._brand_dict(bv, "쿠팡", {"forbidden_phrases": ["공짜"]})
    assert bd["forbidden_phrases"] == ["공짜"] and bd["required_phrases"] == ["로켓배송", "쿠팡"]
    assert routers_review._brand_dict(bv, "쿠팡")["forbidden_phrases"] == ["최저가"]
    assert models  # (임포트 확인)
