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


def test_cosmetic_presets_are_listed_only_for_the_industry_and_not_recommended():
    cos = [p["id"] for p in smallbiz.for_industry("화장품")]
    assert len(cos) == 3 and not set(cos) & set(smallbiz.recommend("크림 세럼 화장품", limit=20))
    assert all(p.get("industry") is None for p in smallbiz.for_industry(None))


def test_list_presets_filters_by_industry(env):
    client, _ = env
    assert len(client.get("/api/smallbiz/presets", params={"industry": "화장품"}).json()["presets"]) == 3
    assert not any(p["industry"] for p in client.get("/api/smallbiz/presets").json()["presets"])


def test_read_photo_returns_fields_and_survives_vision_failure(env, monkeypatch):
    client, _ = env
    pid = client.post("/api/smallbiz/photo", files={"file": ("a.png", _png((400, 300), (120, 120, 120)), "image/png")}).json()["id"]
    monkeypatch.setattr("app.routers_smallbiz.llm.vision_json", lambda *a, **k: {
        "product_name": "수분 크림", "category": "크림", "is_cosmetic": True, "label_claims": ["주름개선", " ", "수분 보습"], "suggested_message": "촉촉한 하루"})
    r = client.post("/api/smallbiz/read-photo", json={"photo_asset_id": pid}).json()
    assert r["ok"] and r["is_cosmetic"] and r["label_claims"] == ["주름개선", "수분 보습"] and r["product_name"] == "수분 크림"
    def boom(*a, **k):
        raise RuntimeError("vision down")
    monkeypatch.setattr("app.routers_smallbiz.llm.vision_json", boom)
    r2 = client.post("/api/smallbiz/read-photo", json={"photo_asset_id": pid}).json()
    assert r2["ok"] is False and r2["product_name"] == "" and r2["label_claims"] == []
    assert client.post("/api/smallbiz/read-photo", json={"photo_asset_id": 999}).status_code == 400


def test_cosmetic_campaign_uses_feed_label_claims_and_chosen_angles(env):
    client, Local = env
    from app.models import Design
    r = client.post("/api/smallbiz/campaigns", json={"what": "수분 크림", "preset_id": "cosmetic_natural",
                                                      "label_claims": ["주름개선"], "trend_angles": ["연말"]})
    assert r.status_code == 200, r.text
    with Local() as db:
        d = db.get(Design, r.json()["design_id"])
        assert len(d.preset_version_ids) == 1, "화장품 프리셋의 기본 지면은 Instagram 피드 하나"
        assert "라벨 문구: 주름개선" in d.brief["description"] and d.brief["use_trend"] is True and d.brief["trend_angles"] == ["연말"]
    plain = client.post("/api/smallbiz/campaigns", json={"what": "수분 크림", "preset_id": "cosmetic_natural"}).json()["design_id"]
    with Local() as db:
        assert db.get(Design, plain).brief["use_trend"] is False, "각도를 고르지 않으면 트렌드를 쓰지 않는다"


def test_trend_angles_is_empty_without_trend_document(env):
    client, _ = env
    assert client.get("/api/smallbiz/trend-angles").json() == {"angles": []}


def test_campaign_auto_runs_fix_once_when_copy_has_blocking_phrase(env, monkeypatch):
    from app import copywriter, copyfix
    client, _ = env
    monkeypatch.setattr(copywriter, "generate_checked", lambda *a, **k: ([{"headline": "최고의 수분 크림", "body": "본문", "cta": "보기", "violations": []}] * 3, 1))
    monkeypatch.setattr(copyfix.llm, "chat_json", lambda *a, **k: {"headline": "촉촉한 수분 크림", "body": "본문", "cta": "보기"})
    r = client.post("/api/smallbiz/campaigns", json={"what": "수분 크림", "preset_id": "cosmetic_natural"})
    assert r.status_code == 200, r.text
    fixes = r.json()["auto_fix"]
    assert len(fixes) == 1 and fixes[0]["adopted"] is True, "금지어 '최고'가 든 제목을 자동으로 한 번 고친다"


def test_campaign_without_blocking_phrase_makes_no_auto_fix(env):
    client, _ = env
    r = client.post("/api/smallbiz/campaigns", json={"what": "수분 크림", "preset_id": "cosmetic_natural"})
    assert r.status_code == 200 and r.json()["auto_fix"] == []


# ---- 3.5 카피 다안 + 심사 ----
def _cands(n):
    return [{"headline": f"제목{i}", "body": f"본문{i}", "cta": "보기", "angle": "", "evidence": "", "long_body": ""} for i in range(1, n + 1)]


def test_judging_only_for_smallbiz_and_switch(monkeypatch):
    from app import copywriter
    assert copywriter.judging({"smallbiz": True}) and not copywriter.judging({})
    monkeypatch.setenv("COPY_JUDGE", "0")
    assert not copywriter.judging({"smallbiz": True})


def test_pick_best_returns_top3_by_score_and_keeps_blocked_out(monkeypatch):
    from app import copywriter
    vs = _cands(6)
    vs[0]["headline"] = "최고의 수분 크림"      # 법적 차단 표현 → 심사에서 제외
    monkeypatch.setattr("app.llm.chat_json", lambda *a, **k: {"scores": [
        {"no": i, "brand": s, "fact": s, "concrete": s, "trend": s, "feed": s, "reason": f"r{i}"} for i, s in
        zip(range(1, 6), (5, 1, 4, 2, 3))]})
    out = copywriter.pick_best("b", {}, {"product_name": "p"}, vs)
    assert len(out) == 3
    assert "최고의 수분 크림" not in [v["headline"] for v in out]
    assert out[0]["score"] >= out[1]["score"] >= out[2]["score"] and out[0]["judge"]


def test_pick_best_falls_back_to_order_when_judge_fails(monkeypatch):
    from app import copywriter
    def boom(*a, **k): raise RuntimeError("x")
    monkeypatch.setattr("app.llm.chat_json", boom)
    out = copywriter.pick_best("b", {}, {"product_name": "p"}, _cands(6))
    assert [v["headline"] for v in out] == ["제목1", "제목2", "제목3"]


def test_generate_checked_asks_six_for_smallbiz_and_three_otherwise(monkeypatch):
    from app import copywriter
    asked = []
    def fake(*a, n=3, **k):
        asked.append(n); return _cands(n)
    monkeypatch.setattr(copywriter, "generate", fake)
    monkeypatch.setattr(copywriter, "pick_best", lambda b, br, bf, vs, keep=3: vs[:keep])
    lim = {"headline": None, "body": None, "cta": None}
    assert len(copywriter.generate_checked("b", {}, {"smallbiz": True}, {}, lim)[0]) == 3
    assert len(copywriter.generate_checked("b", {}, {}, {}, lim)[0]) == 3
    assert asked == [6, 3]


def test_image_prompt_gets_trend_mood_only_when_angles_chosen():
    from app import imagegen
    tone = {"description": "맑은 자연광"}
    base = {"product_name": "수분 크림", "description": "보습", "key_message": "겨울 보습"}
    plain = imagegen.build_prompt("b", tone, base, "정사각", "실사 제품·공간")
    withs = imagegen.build_prompt("b", tone, {**base, "trend_angles": ["속광", "홈케어"]}, "정사각", "실사 제품·공간")
    assert "[이번 주 분위기]" not in plain and "속광, 홈케어" in withs and "[이번 주 분위기]" in withs
    assert imagegen.build_prompt("b", tone, {**base, "trend_angles": []}, "정사각", "실사 제품·공간") == plain


# ---- 3.7 배경 비전 심사 + 재생성 ----
from tests.test_image_fit import env as img_env  # noqa: E402,F401  (픽스처: 임시 Postgres·가짜 이미지 생성)


def _judged(overall, text=5):
    items = ["톤 일치", "내용 일치", "표현 방식 일치", "금지 요소 없음", "글자·로고 없음"]
    return {"scores": [{"item": i, "score": text if i == "글자·로고 없음" else 4, "reason": ""} for i in items],
            "overall": overall, "summary": f"o{overall}"}


def _smallbiz_image(img_env, monkeypatch, answers):
    from app import analysis
    c, ids, M = img_env["client"], img_env["ids"], img_env["models"]
    with img_env["Local"]() as db:
        d = db.get(M.Design, ids["design"]); d.brief = {**d.brief, "smallbiz": {"preset_id": "x"}}
        db.add(M.CopyVariant(design_id=d.id, preset_version_id=ids["presets"]["피드 4:5"], variant_no=1, headline="제목",
                             body="본문", cta="보기", selected=True, batch_no=1))
        db.commit()
    seen = []
    def fake(im, vars):
        seen.append(1)
        a = answers[len(seen) - 1]
        if isinstance(a, Exception): raise a
        return a
    monkeypatch.setattr(analysis, "judge_image", fake)
    pid = ids["presets"]["피드 4:5"]
    assert c.post(f"/api/designs/{ids['design']}/presets/{pid}/image", json={"regenerate_background": True}).status_code == 200
    with img_env["Local"]() as db:
        g = db.query(M.GeneratedImage).filter_by(kind="background", preset_version_id=pid).order_by(M.GeneratedImage.id.desc()).first()
        js = [j.result for j in db.query(M.Judgment).filter_by(design_id=ids["design"]).all()]
    return seen, g.layout, js


def test_image_judge_passes_without_retry(img_env, monkeypatch):
    seen, lay, js = _smallbiz_image(img_env, monkeypatch, [_judged(4)])
    assert len(seen) == 1 and len(img_env["calls"]) == 1
    assert lay["judge"]["overall"] == 4 and lay["judge"]["tries"] == 1
    assert len(js) == 1 and js[0]["auto"] and js[0]["background_id"] is None and js[0]["overall"] == 4


def test_image_judge_retries_once_when_text_drawn_and_keeps_better(img_env, monkeypatch):
    seen, lay, js = _smallbiz_image(img_env, monkeypatch, [_judged(4, text=1), _judged(3)])
    assert len(seen) == 2 and len(img_env["calls"]) == 2, "글자가 그려진 첫 장은 기준 미달 → 1회 재생성"
    assert lay["judge"] == {"overall": 3, "summary": "o3", "tries": 2, "first_overall": 4}


def test_image_judge_failure_keeps_image(img_env, monkeypatch):
    seen, lay, js = _smallbiz_image(img_env, monkeypatch, [RuntimeError("x")])
    assert len(img_env["calls"]) == 1 and "judge" not in lay and not js


def test_image_judge_off_by_switch(img_env, monkeypatch):
    monkeypatch.setenv("IMAGE_JUDGE", "0")
    seen, lay, js = _smallbiz_image(img_env, monkeypatch, [_judged(1)])
    assert not seen and len(img_env["calls"]) == 1


def test_image_judge_looks_at_background_before_product_paste(monkeypatch):
    """합성본(제품 라벨 글자 포함)이 아니라 제품을 붙이기 전 빈 배경을 채점한다(시안 70 실측: 라벨 글자로 매번 재생성)."""
    from PIL import Image
    from app import analysis, routers_images
    seen = []
    monkeypatch.setattr(analysis, "judge_image", lambda im, vars: seen.append(im) or _judged(4))
    monkeypatch.setattr(analysis, "judge_vars", lambda *a: {})

    class DB:
        def get(self, *a): return None
    final, plain = Image.new("RGB", (8, 8), "red"), Image.new("RGB", (8, 8), "white")
    bg, extra, j = routers_images._judge_and_retry(DB(), type("D", (), {"brand_id": 1, "brief": {}})(), None, "실사 제품·공간",
                                                   final, {}, plain, lambda: (_ for _ in ()).throw(AssertionError("재생성 없음")))
    assert seen == [plain] and bg is final and extra["judge"]["overall"] == 4


def test_pick_best_puts_low_fact_after_higher_total(monkeypatch):
    """사실 근거 2점 이하(지어낸 사연)는 합계가 높아도 뒤로 간다."""
    from app import copywriter
    monkeypatch.setattr("app.llm.chat_json", lambda *a, **k: {"scores": [
        {"no": 1, "brand": 5, "fact": 2, "concrete": 5, "trend": 5, "feed": 5, "reason": "입력에 없음"},
        {"no": 2, "brand": 4, "fact": 4, "concrete": 3, "trend": 3, "feed": 3, "reason": "ok"},
        {"no": 3, "brand": 3, "fact": 4, "concrete": 3, "trend": 3, "feed": 3, "reason": "ok"}]})
    out = copywriter.pick_best("b", {}, {"product_name": "p"}, _cands(3))
    assert [v["headline"] for v in out] == ["제목2", "제목3", "제목1"]


def test_smallbiz_image_direction_follows_tone_not_common_options():
    from app import imagegen
    b = {"product_name": "수분 장벽 크림 50ml", "key_message": "겨울 건조함에 맞서는 보습"}
    assert imagegen.pick_direction("실사 제품·공간", b) != imagegen.SMALLBIZ_DIRECTION
    assert imagegen.pick_direction("실사 제품·공간", {**b, "smallbiz": {"preset_id": "x"}}) == imagegen.SMALLBIZ_DIRECTION


def test_image_judge_skipped_when_model_redraws_product(img_env, monkeypatch):
    """누끼 없이 제품을 참조 이미지로 다시 그리는 방식은 라벨 글자 때문에 채점이 늘 미달이라 건너뛴다."""
    from app import routers_images
    real = routers_images._bg_prompt
    def ref_mode(*a, **k):
        bp = real(*a, **k)
        from PIL import Image
        return {**bp, "product_img": Image.new("RGBA", (10, 10)), "composite": False}
    monkeypatch.setattr(routers_images, "_bg_prompt", ref_mode)
    seen, lay, js = _smallbiz_image(img_env, monkeypatch, [_judged(1, text=1)])
    assert not seen and "judge" not in lay
