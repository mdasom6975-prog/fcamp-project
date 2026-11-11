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


# ---- P2: 장면 3장 + 심사 ----
def _ready(c, Local):
    """브랜드 + 고른 여자 모델 + 누끼 제품 사진 자산. 반환 (feed_brand_id, photo_asset_id)."""
    import io
    from app import storage
    from app.models import Asset
    b = _brand(c)
    r = c.post(f"/api/feed/brands/{b['id']}/models", json={"gender": "여"}).json()
    c.post(f"/api/feed/models/{r['candidates'][0]['id']}/select")
    im = Image.new("RGBA", (300, 400), (0, 0, 0, 0))
    im.paste((40, 180, 170, 255), (100, 80, 200, 360))
    key = storage.put_image("product_t.png", im)
    with Local() as db:
        a = Asset(kind="product", file_path=key, mime="image/png", bytes=1, width=300, height=400)
        db.add(a); db.commit()
        return b["id"], a.id


def _judge_seq(monkeypatch, answers):
    """심사(llm.vision_json)를 호출 순서대로 answers 로 답하게 한다. 보낸 이미지 수를 기록한다."""
    from app import llm
    seen = []
    def fake(system, user, images, kind, schema):
        seen.append({"n": len(images), "user": user})
        a = answers[min(len(seen), len(answers)) - 1]
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(llm, "vision_json", fake)
    return seen


def _ok(items=("제품 일치", "작은 글자", "얼굴 일치", "손·신체", "새 글자 없음", "장면"), low=None):
    return {"scores": [{"item": i, "score": 2 if i == low else 5, "reason": ""} for i in items], "overall": 4, "summary": "s"}


def test_post_makes_three_shots_feed_size_and_judges_against_originals(env, monkeypatch):
    from app import imagegen
    c, Local, calls = env
    fbid, pid = _ready(c, Local)
    variants = []
    monkeypatch.setattr(imagegen, "generate_variant", lambda prompt, w, h, ref, **k: variants.append((prompt, k.get("product") is not None)) or Image.new("RGB", (w, h), (90, 90, 90)))
    seen = _judge_seq(monkeypatch, [_ok()])
    r = c.post("/api/feed/posts", json={"feed_brand_id": fbid, "photo_asset_id": pid, "gender": "여", "product_name": "수분 세럼", "category": "세럼"})
    assert r.status_code == 200, r.text
    shots = r.json()["shots"]
    assert set(shots) == {"hold", "use", "detail"} and all(s["tries"] == 1 and s["url"] for s in shots.values())
    assert c.get(shots["detail"]["url"]).status_code == 200
    from app.models import Asset
    with Local() as db:
        assert all((db.get(Asset, s["asset_id"]).width, db.get(Asset, s["asset_id"]).height) == (1080, 1350) for s in shots.values())
    assert len(variants) == 2 and all(has_prod for _, has_prod in variants), "든·사용 장면은 얼굴+제품 참조"
    assert any("스포이트" in p for p, _ in variants), "세럼은 스포이트 동작"
    assert sorted(x["n"] for x in seen) == [2, 3, 3], "상세는 결과+제품, 든·사용은 결과+제품+얼굴로 심사"


def test_low_key_score_regenerates_that_shot_once(env, monkeypatch):
    from app import imagegen
    c, Local, _ = env
    fbid, pid = _ready(c, Local)
    n = {"v": 0}
    def var(prompt, w, h, ref, **k):
        n["v"] += 1
        return Image.new("RGB", (w, h))
    monkeypatch.setattr(imagegen, "generate_variant", var)
    # 모든 심사가 '제품 일치 2점' → 든·사용 장면이 각각 1회씩 다시 만들어진다(상세는 해당 항목이 없어 그대로)
    _judge_seq(monkeypatch, [_ok(low="제품 일치")])
    shots = c.post("/api/feed/posts", json={"feed_brand_id": fbid, "photo_asset_id": pid, "gender": "여", "product_name": "p"}).json()["shots"]
    assert n["v"] == 4 and shots["hold"]["tries"] == 2 and shots["use"]["tries"] == 2 and shots["detail"]["tries"] == 1


def test_misspelled_label_text_also_triggers_one_retry(env, monkeypatch):
    from app import imagegen
    c, Local, _ = env
    fbid, pid = _ready(c, Local)
    n = {"v": 0}
    monkeypatch.setattr(imagegen, "generate_variant", lambda prompt, w, h, ref, **k: n.__setitem__("v", n["v"] + 1) or Image.new("RGB", (w, h)))
    _judge_seq(monkeypatch, [_ok(low="작은 글자")])     # PDRN→PORN 같은 라벨 오기
    shots = c.post("/api/feed/posts", json={"feed_brand_id": fbid, "photo_asset_id": pid, "gender": "여", "product_name": "p"}).json()["shots"]
    assert n["v"] == 4 and shots["hold"]["tries"] == 2 and shots["use"]["tries"] == 2 and shots["detail"]["tries"] == 1


def test_judge_failure_keeps_image_and_regenerate_one_shot(env, monkeypatch):
    from app import imagegen
    c, Local, _ = env
    fbid, pid = _ready(c, Local)
    monkeypatch.setattr(imagegen, "generate_variant", lambda prompt, w, h, ref, **k: Image.new("RGB", (w, h)))
    _judge_seq(monkeypatch, [RuntimeError("x")])
    post = c.post("/api/feed/posts", json={"feed_brand_id": fbid, "photo_asset_id": pid, "gender": "여", "product_name": "p"}).json()
    assert all(s["judge"] is None and s["tries"] == 1 and s["asset_id"] for s in post["shots"].values())
    old = post["shots"]["use"]["asset_id"]
    new = c.post(f"/api/feed/posts/{post['id']}/shots/use/regenerate").json()["shots"]
    assert new["use"]["asset_id"] != old and new["hold"]["asset_id"] == post["shots"]["hold"]["asset_id"]
    assert c.post(f"/api/feed/posts/{post['id']}/shots/x/regenerate").status_code == 400


def test_post_requires_chosen_model(env):
    c, Local, _ = env
    fbid, pid = _ready(c, Local)
    r = c.post("/api/feed/posts", json={"feed_brand_id": fbid, "photo_asset_id": pid, "gender": "남", "product_name": "p"})
    assert r.status_code == 400 and "남성" in r.json()["detail"]


def test_main_body_drops_detached_sticker_but_keeps_product():
    from app.routers_feed import main_body
    im = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    im.paste((40, 180, 170, 255), (100, 80, 200, 290))     # 본체
    im.paste((240, 120, 60, 255), (230, 10, 280, 60))      # 떨어진 스티커
    out = main_body(im)
    assert out.getpixel((150, 200))[3] == 255 and out.getpixel((255, 35))[3] == 0


def test_seat_on_surface_moves_product_bottom_to_counter_edge():
    from app.routers_feed import seat_on_surface
    bg = Image.new("RGB", (1080, 1350), (230, 230, 230))
    bg.paste((120, 110, 100), (0, 945, 1080, 1350))      # 받침 모서리: 70% 높이
    box = seat_on_surface(bg, [400, 300, 680, 850])
    assert 945 < box[3] < 945 + 120 and box[2] - box[0] == 280 and box[3] - box[1] == 550
    flat = Image.new("RGB", (1080, 1350), (230, 230, 230))
    assert seat_on_surface(flat, [400, 300, 680, 850]) == [400, 300, 680, 850], "모서리가 없으면 그대로"


def test_contact_shadow_darkens_just_below_product_bottom():
    from app.routers_feed import contact_shadow
    bg = Image.new("RGB", (1080, 1350), (235, 235, 235))
    out = contact_shadow(bg, [400, 300, 680, 900])
    assert sum(out.getpixel((540, 900))) < sum(bg.getpixel((540, 900))) - 150, "바닥 바로 아래가 진해진다"
    assert out.getpixel((540, 200)) == bg.getpixel((540, 200)), "제품 위쪽은 그대로"
