"""피드 세트 생성기 P1: 브랜드와 가상 모델(후보 생성·선택·안전 필터 재시도). 이미지 생성은 가짜."""
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def env(pg_engine, monkeypatch):
    from app import imagegen
    from app.db import Base
    from app.main import app
    from app.routers_assets import get_db
    Base.metadata.create_all(pg_engine)
    Local = sessionmaker(bind=pg_engine, autoflush=False)
    calls = []

    def fake_gen(prompt, w, h, *a, **k):
        calls.append(prompt)
        return Image.new("RGB", (w, h), (200, 190, 180))
    monkeypatch.setattr(imagegen, "generate_background", fake_gen)

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app), Local, calls
    finally:
        app.dependency_overrides.clear()


def _brand(c):
    r = c.post("/api/feed/brands", json={"name": "달빛공방", "tone": "cosmetic_natural"})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_brand_uses_tone_rules_and_stays_out_of_old_brand_list(env):
    c, Local, _ = env
    b = _brand(c)
    assert b["name"] == "달빛공방" and b["models"] == {"여": None, "남": None}
    assert c.get("/api/brands").json() == [], "이전 버전 브랜드 목록에는 섞이지 않는다"
    from app.models import BrandVersion
    with Local() as db:
        v = db.query(BrandVersion).one()
        assert "최고" in v.forbidden_phrases and v.voice["description"]
    assert c.post("/api/feed/brands", json={"name": "x", "tone": "cafe_mood"}).status_code == 400


def test_candidates_have_different_looks_and_clothing_and_select_replaces(env):
    c, _, calls = env
    b = _brand(c)
    r = c.post(f"/api/feed/brands/{b['id']}/models", json={"gender": "여"}).json()
    assert len(r["candidates"]) == 4 and r["failed"] == 0
    assert len(set(calls)) == 4 and all("여성" in p and "입고 있다" in p for p in calls), "후보마다 외모가 다르고 옷차림은 항상 명시"
    assert len({next(w for w in ("니트", "터틀넥", "셔츠", "재킷") if w in p) for p in calls}) == 4, "후보마다 옷도 다르다"
    first = r["candidates"][0]
    assert c.get(first["url"]).status_code == 200
    out = c.post(f"/api/feed/models/{first['id']}/select").json()
    assert out["models"]["여"]["id"] == first["id"] and out["candidates"] == []
    # 다시 후보를 만들고 다른 얼굴을 고르면 이전 선택은 사라진다(브랜드에 여자 모델은 하나)
    r2 = c.post(f"/api/feed/brands/{b['id']}/models", json={"gender": "여"}).json()
    out2 = c.post(f"/api/feed/models/{r2['candidates'][1]['id']}/select").json()
    assert out2["models"]["여"]["id"] == r2["candidates"][1]["id"] and out2["models"]["남"] is None
    assert c.post(f"/api/feed/brands/{b['id']}/models", json={"gender": "x"}).status_code == 400


def test_moderation_block_retries_once_with_safer_clothing(env, monkeypatch):
    from app import imagegen
    c, _, _ = env
    b = _brand(c)
    seen = []

    def gen(prompt, w, h, *a, **k):
        seen.append(prompt)
        if "가디건" not in prompt:
            raise RuntimeError("Error code: 400 - moderation_blocked")
        return Image.new("RGB", (w, h))
    monkeypatch.setattr(imagegen, "generate_background", gen)
    r = c.post(f"/api/feed/brands/{b['id']}/models", json={"gender": "남"}).json()
    assert len(r["candidates"]) == 4 and sum("가디건" in p for p in seen) == 4


def test_all_candidates_failing_returns_502(env, monkeypatch):
    from app import imagegen
    c, _, _ = env
    b = _brand(c)
    monkeypatch.setattr(imagegen, "generate_background", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert c.post(f"/api/feed/brands/{b['id']}/models", json={"gender": "여"}).status_code == 502
