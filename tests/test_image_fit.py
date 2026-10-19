"""광고 이미지 규격 맞춤(비율군별 배경 생성 + 내용 상자 크롭 + 로고 자리)과 배경 형태 선택 검사.
외부 API 는 호출하지 않는다 (generate_background 를 가짜로 바꾼다). 임시 sqlite DB 와 임시 storage 를 쓴다."""
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

FONT = "backend/fonts/Pretendard-Bold.otf"


def _scene(w: int = 1024, h: int = 1024) -> Image.Image:
    """위쪽 45% 에 복잡한 피사체(얼굴색 원 + 줄무늬), 아래는 빈 흰 면 — 실제 실패 사례(bg_a775b800d7)와 같은 구도."""
    im = Image.new("RGB", (w, h), (246, 244, 240))
    d = ImageDraw.Draw(im)
    for x in range(int(w * 0.25), int(w * 0.75), 12):
        d.rectangle((x, int(h * 0.12), x + 6, int(h * 0.42)), fill=(40, 60, 120))
    d.ellipse((int(w * 0.42), int(h * 0.04), int(w * 0.58), int(h * 0.2)), fill=(230, 180, 150))   # 얼굴
    return im


def _logo() -> Image.Image:
    im = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, 399, 99), radius=20, fill=(200, 30, 30, 255))
    return im


# ---- compose: 내용 상자 크롭 ----
def test_content_box_finds_subject_and_ignores_empty_copy_space():
    from app import compose
    box = compose.content_box(_scene())
    assert box is not None
    x0, y0, x1, y1 = box
    assert y0 <= 1024 * 0.06 and 1024 * 0.4 <= y1 <= 1024 * 0.5, box   # 위 45% 안
    assert x0 <= 1024 * 0.3 and x1 >= 1024 * 0.7, box
    assert compose.content_box(Image.new("RGB", (300, 300), (250, 250, 250))) is None   # 단색이면 없음


def test_cover_crop_keeps_heads_for_landscape_from_square():
    """정사각 → 2:1 은 세로 절반만 남는다. 중앙 크롭은 얼굴(위 4~20%)을 잘라 냈고, 내용 상자 크롭은 윗변에 맞춘다."""
    from app import compose
    src = _scene()
    nw, nh, left, top, info = compose.crop_window(src, 1200, 600, focus=True, tighten=False)
    assert top == 0 and info["mode"] == "내용 상자" and not info["cut"]["top"] and info["zoom"] == 1.0
    _, _, _, top_center, info_c = compose.crop_window(src, 1200, 600, focus=False)
    assert top_center == 300 and info_c["mode"] == "중앙"
    out = compose.cover_crop(src, 1200, 600)
    assert out.size == (1200, 600)
    # 얼굴색이 결과 위쪽에 남아 있다
    assert any(abs(r - 230) < 20 and abs(g - 180) < 20 for r, g, b in out.crop((500, 0, 700, 120)).get_flattened_data())


def test_crop_window_reports_focus_for_editor():
    """편집 화면 초기 초점(zoom, cx, cy)은 editing.render 의 공식과 같은 뜻이어야 한다 — 같은 값으로 렌더하면 같은 창."""
    from PIL import ImageChops
    from app import compose, editing
    src = _scene()
    nw, nh, left, top, info = compose.crop_window(src, 1200, 600)
    assert info["cx"] == pytest.approx((left + 600) / nw, abs=1e-3) and info["cy"] == pytest.approx((top + 300) / nh, abs=1e-3)
    els = editing.default_elements({"crop": info, "elements": []}, {}, None)
    assert els[0]["props"] == {"zoom": info["zoom"], "cx": info["cx"], "cy": info["cy"]}
    a = compose.cover_crop(src, 1200, 600)
    b, _ = editing.render(src, 1200, 600, [{"type": "image", "props": els[0]["props"], "z_index": 0}], lambda r: None, lambda a: None)
    assert ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None or \
        max(ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getextrema()[0]) <= 8
    assert editing.default_elements({"elements": []}, {}, None)[0]["props"] == {"zoom": 1.0, "cx": 0.5, "cy": 0.5}


# ---- compose: 로고 자리 ----
def test_logo_moves_to_empty_bottom_corner_and_text_stacks_above():
    from app import compose
    img, rep = compose.compose(_scene(), 1440, 1800, "직구처럼 넓게, 리빙은 쿠팡에서", FONT, _logo(),
                               None, 10, "#000000", "#FFFFFF")
    assert rep["logo_placement"]["corner"] in ("좌하단", "우하단"), rep["logo_placement"]
    logo = next(e for e in rep["elements"] if e["type"] == "logo")
    text = next(e for e in rep["elements"] if e["type"] == "text")
    assert text["box"][3] <= logo["box"][1], "문구는 로고 위에 쌓인다"
    assert "반투명 판" not in " ".join(rep["warnings"])
    assert rep["crop"]["mode"].startswith("내용 상자")


def test_logo_stays_top_left_when_top_is_empty():
    from app import compose
    flat = Image.new("RGB", (1024, 1536), (240, 240, 240))
    _, rep = compose.compose(flat, 1440, 1800, "제목", FONT, _logo(), None, 10, "#000000", "#FFFFFF")
    assert rep["logo_placement"]["corner"] == "좌상단"
    logo = next(e for e in rep["elements"] if e["type"] == "logo")
    text = next(e for e in rep["elements"] if e["type"] == "text")
    assert text["box"][1] >= logo["box"][3]


def test_horizontal_keeps_three_candidates():
    from app import compose
    _, rep = compose.compose(_scene(), 1200, 600, "제목", FONT, _logo(), None, 10, "#000000", "#FFFFFF")
    assert rep["logo_placement"]["corner"] in ("좌상단", "우상단", "우하단")


# ---- imagegen: 크기·형태 ----
def test_size_for_template(monkeypatch):
    from app import imagegen
    monkeypatch.setattr(imagegen, "PER_RATIO", True)
    monkeypatch.setattr(imagegen, "PROVIDER", "openai")
    assert imagegen.size_for("세로형") == (1024, 1536)
    assert imagegen.size_for("가로형") == (1536, 1024)
    assert imagegen.size_for("정사각") == (1024, 1024)
    monkeypatch.setattr(imagegen, "PER_RATIO", False)
    assert imagegen.size_for("세로형") == (1024, 1024)
    monkeypatch.setattr(imagegen, "PER_RATIO", True)
    monkeypatch.setattr(imagegen, "PROVIDER", "cloudflare")
    assert imagegen.size_for("가로형") == (1024, 1024)


@pytest.mark.parametrize("tone, brief, expected", [
    ({}, {}, "실사 제품·공간"),
    ({"description": "경쾌하고 밝은 이미지"}, {}, "실사 제품·공간"),
    ({"description": "무광 레진 재질, 스튜디오 확산광"}, {}, "3D 렌더"),
    ({"description": "손그림 느낌의 일러스트"}, {}, "플랫 일러스트"),
    ({}, {"description": "기하 도형 패턴으로 경쾌하게"}, "그래픽 패턴"),
    ({"description": "가족이 함께 웃는 장면"}, {}, "실사 인물"),
    ({"description": "일러스트"}, {"image_style": "실사 인물"}, "실사 인물"),   # 브리프 지정이 우선
    ({"description": "일러스트"}, {"image_style": "자동"}, "플랫 일러스트"),
])
def test_pick_style(tone, brief, expected):
    from app import imagegen
    style, reason = imagegen.pick_style(tone, brief)
    assert style == expected
    assert reason.startswith("브리프" if brief.get("image_style") in imagegen.STYLES else "자동")


def test_build_prompt_rejects_unknown_style():
    from app import imagegen
    with pytest.raises(ValueError):
        imagegen.build_prompt("쿠챠", {}, {"product_name": "x"}, "정사각", "수채화")


def test_style_prompt_files_do_not_mention_people_except_person_style():
    from app import imagegen, prompts
    for name, file in imagegen.STYLES.items():
        text = prompts.render(f"image/style/{file}", target="20대")
        if name == "실사 인물":
            assert "인물" in text
        else:
            assert "등장 인물은 타깃" not in text


# ---- routers_images: 비율군별 생성 ----
@pytest.fixture
def env(tmp_path, monkeypatch):
    from app import imagegen, models, routers_images
    from app.db import Base
    from app.main import app
    from app.routers_assets import get_db

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(routers_images, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(imagegen, "PER_RATIO", True)
    monkeypatch.setattr(imagegen, "PROVIDER", "openai")
    monkeypatch.setattr(imagegen, "VARIANT", True)
    calls: list[tuple[int, int]] = []
    variants: list[dict] = []     # 참조 이미지로 만든 변형 호출 기록

    def fake_generate(prompt, w, h):
        calls.append((w, h))
        return _scene(w, h)

    def fake_variant(prompt, w, h, reference):
        calls.append((w, h))
        variants.append({"size": (w, h), "ref_size": reference.size, "prompt": prompt})
        return _scene(w, h)
    monkeypatch.setattr(imagegen, "generate_background", fake_generate)
    monkeypatch.setattr(imagegen, "generate_variant", fake_variant)

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[get_db] = override

    with Local() as db:
        brand = models.Brand(name="쿠팡")
        db.add(brand); db.flush()
        bv = models.BrandVersion(brand_id=brand.id, version_no=1, image_tone={"description": "경쾌하고 밝은 이미지"})
        db.add(bv); db.flush()
        brand.current_version_id = bv.id
        plat = models.Platform(name="테스트")
        db.add(plat); db.flush()
        ids = {"brand": brand.id, "bv": bv.id, "presets": {}}
        for name, w, h in [("피드 4:5", 1440, 1800), ("스토리 9:16", 1440, 2560), ("네이티브 2:1", 1200, 600),
                           ("정사각 1:1", 500, 500)]:
            pl = models.Placement(platform_id=plat.id, name=name, ad_type="테스트",
                                  content_kind="완성형")
            db.add(pl); db.flush()
            pv = models.PresetVersion(placement_id=pl.id, production_width=w, production_height=h, status="확인됨")
            db.add(pv); db.flush()
            ids["presets"][name] = pv.id
        d = models.Design(brand_id=brand.id, brand_version_id=bv.id, kind="광고 이미지",
                          preset_version_ids=list(ids["presets"].values()),
                          brief={"product_name": "리빙 창고 대방출", "key_message": "특가", "target": "성인"})
        db.add(d); db.commit()
        ids["design"] = d.id

    client = TestClient(app)
    try:
        yield {"client": client, "ids": ids, "Local": Local, "calls": calls, "variants": variants, "models": models}
    finally:
        app.dependency_overrides.clear()


def test_regenerate_makes_one_background_per_ratio_group(env):
    c, ids, calls, variants = env["client"], env["ids"], env["calls"], env["variants"]
    out = c.post(f"/api/designs/{ids['design']}/background/regenerate", json={}).json()
    assert out["generated"] == 3 and sorted(calls) == [(1024, 1024), (1024, 1536), (1536, 1024)]
    # 첫 비율군(첫 규격 = 세로형)만 프롬프트 생성, 나머지 두 비율군은 그 이미지를 참조한 '같은 장면' 변형
    assert calls[0] == (1024, 1536) and len(variants) == 2
    assert all(v["ref_size"] == (1024, 1536) for v in variants)
    assert all(v["prompt"].startswith("[참조 이미지]") and "[구도]" in v["prompt"] for v in variants)
    assert out["style"] == "실사 제품·공간" and out["style_reason"].startswith("자동")
    rows = c.get(f"/api/designs/{ids['design']}/images").json()
    by = {r["preset_id"]: r["background"] for r in rows}
    p = ids["presets"]
    assert (by[p["피드 4:5"]]["width"], by[p["피드 4:5"]]["height"]) == (1024, 1536)
    assert by[p["피드 4:5"]]["id"] != by[p["스토리 9:16"]]["id"]
    assert (by[p["네이티브 2:1"]]["width"], by[p["네이티브 2:1"]]["height"]) == (1536, 1024)
    assert (by[p["정사각 1:1"]]["width"], by[p["정사각 1:1"]]["height"]) == (1024, 1024)
    with env["Local"]() as db:
        bgs = db.query(env["models"].GeneratedImage).filter_by(kind="background").all()
        assert len({g.file_path for g in bgs}) == 3 and len(bgs) == 4
        feed = next(g for g in bgs if g.preset_version_id == p["피드 4:5"])
        story = next(g for g in bgs if g.preset_version_id == p["스토리 9:16"])
        assert feed.file_path == story.file_path, "같은 비율군은 파일을 공유한다"
        assert feed.layout["template"] == "세로형" and feed.layout["style"] == "실사 제품·공간"
        assert any(r.startswith("image/style/실사제품@") for r in feed.layout["prompts"])
        assert "reference" not in feed.layout
        land = next(g for g in bgs if g.preset_version_id == p["네이티브 2:1"])
        assert land.layout["reference"]["background_id"] == feed.id and land.layout["reference"]["template"] == "세로형"
        assert land.layout["prompts"][0].startswith("image/same_scene@")


def test_variant_disabled_generates_independently(env, monkeypatch):
    from app import imagegen
    monkeypatch.setattr(imagegen, "VARIANT", False)
    c, ids, calls, variants = env["client"], env["ids"], env["calls"], env["variants"]
    c.post(f"/api/designs/{ids['design']}/background/regenerate", json={})
    assert len(calls) == 3 and variants == []


def test_single_preset_regenerate_references_existing_background(env):
    """규격 하나만 다시 만들 때도 시안에 배경이 있으면 그 장면을 참조한다."""
    c, ids, calls, variants = env["client"], env["ids"], env["calls"], env["variants"]
    p = ids["presets"]
    c.post(f"/api/designs/{ids['design']}/background/regenerate", json={})
    with env["Local"]() as db:
        for pid in p.values():
            db.add(env["models"].CopyVariant(design_id=ids["design"], preset_version_id=pid, variant_no=1,
                                             headline="제목", selected=True, batch_no=1))
        db.commit()
    r = c.post(f"/api/designs/{ids['design']}/presets/{p['정사각 1:1']}/image", json={"regenerate_background": True})
    assert r.status_code == 200 and len(calls) == 4 and len(variants) == 3 and variants[-1]["size"] == (1024, 1024)


def test_regenerate_with_style_saves_to_brief(env):
    c, ids = env["client"], env["ids"]
    out = c.post(f"/api/designs/{ids['design']}/background/regenerate", json={"style": "플랫 일러스트"}).json()
    assert out["style"] == "플랫 일러스트" and out["style_reason"] == "브리프에서 지정"
    assert c.get(f"/api/designs/{ids['design']}").json()["brief"]["image_style"] == "플랫 일러스트"
    r = c.post(f"/api/designs/{ids['design']}/background/regenerate", json={"style": "수채화"})
    assert r.status_code == 400


def test_legacy_mode_makes_single_square(env, monkeypatch):
    from app import imagegen
    monkeypatch.setattr(imagegen, "PER_RATIO", False)
    c, ids, calls = env["client"], env["ids"], env["calls"]
    out = c.post(f"/api/designs/{ids['design']}/background/regenerate", json={}).json()
    assert out["generated"] == 1 and calls == [(1024, 1024)]


def test_make_image_reuses_same_ratio_background_without_new_call(env):
    """합성은 같은 비율군 배경을 재사용하고, 없으면 시안의 다른 배경을 자른다(추가 생성 없음)."""
    c, ids, calls = env["client"], env["ids"], env["calls"]
    p = ids["presets"]
    c.post(f"/api/designs/{ids['design']}/background/regenerate", json={})
    assert len(calls) == 3
    with env["Local"]() as db:
        for pid in p.values():
            db.add(env["models"].CopyVariant(design_id=ids["design"], preset_version_id=pid, variant_no=1,
                                             headline="제목", body="", cta="", violations=[], attempts=1,
                                             selected=True, batch_no=1))
        db.commit()
    r = c.post(f"/api/designs/{ids['design']}/presets/{p['네이티브 2:1']}/image", json={"regenerate_background": False})
    assert r.status_code == 200, r.text
    assert len(calls) == 3
    j = r.json()
    assert (j["background"]["width"], j["background"]["height"]) == (1536, 1024)
    assert j["composite"]["layout"]["crop"]["mode"].startswith("내용 상자")
    # 다시 만들기: 이 규격의 비율군 크기로 1장만
    r = c.post(f"/api/designs/{ids['design']}/presets/{p['피드 4:5']}/image", json={"regenerate_background": True})
    assert r.status_code == 200 and calls[-1] == (1024, 1536) and len(calls) == 4


def test_brief_rejects_unknown_style(env):
    c, ids = env["client"], env["ids"]
    r = c.patch(f"/api/designs/{ids['design']}", json={"brief": {"product_name": "x", "image_style": "수채화"}})
    assert r.status_code == 422
    r = c.patch(f"/api/designs/{ids['design']}", json={"brief": {"product_name": "x", "image_style": "3D 렌더"}})
    assert r.status_code == 200 and r.json()["brief"]["image_style"] == "3D 렌더"


def test_thumbnail_endpoint_caches_small_jpeg(env):
    """썸네일 그리드용 축소본: 긴 변 360px JPEG, id 기준 파일 캐시."""
    import io
    from app import routers_images
    c, ids = env["client"], env["ids"]
    p = ids["presets"]
    c.post(f"/api/designs/{ids['design']}/background/regenerate", json={})
    with env["Local"]() as db:
        db.add(env["models"].CopyVariant(design_id=ids["design"], preset_version_id=p["피드 4:5"], variant_no=1,
                                         headline="제목", selected=True, batch_no=1))
        db.commit()
    cid = c.post(f"/api/designs/{ids['design']}/presets/{p['피드 4:5']}/image", json={"regenerate_background": False}).json()["composite"]["id"]
    r = c.get(f"/api/generated/{cid}/thumb")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    im = Image.open(io.BytesIO(r.content))
    assert max(im.size) == 360 and im.size == (288, 360)
    comp = c.get(f"/api/designs/{ids['design']}/images").json()
    token = next(r["composite"]["file"] for r in comp if r["preset_id"] == p["피드 4:5"])
    assert token.startswith("cmp_") and (routers_images.STORAGE_DIR / "thumbs" / f"{token}_360.jpg").exists()
    assert c.get("/api/generated/999999/thumb").status_code == 404


def test_tighten_zooms_when_subject_is_small_and_keeps_copy_space():
    """피사체가 위 45% 에 작게 있고 아래가 비면 확대해 피사체 몫을 키운다(상한 1.3배). 문구 자리(아래)는 남긴다."""
    from app import compose
    src = _scene(1024, 1536)
    nw, nh, left, top, info = compose.crop_window(src, 1440, 1800)
    assert 1.0 < info["zoom"] <= compose.MAX_TIGHTEN and info["mode"] == "내용 상자+확대"
    assert not info["cut"]["top"] and not info["cut"]["left"] and not info["cut"]["right"], "피사체는 잘리지 않는다"
    x0, y0, x1, y1 = info["content_box"]
    # 확대 후 창 기준으로 피사체 아래에 문구 자리가 남는다 (창 높이의 25% 이상)
    win_h = 1800 / info["zoom"]
    assert (top / info["zoom"] + win_h) - y1 >= 0.25 * win_h
    # 가로형: 피사체를 오른쪽에 두고 왼쪽을 비운다
    nw, nh, left, top, info = compose.crop_window(_scene(1536, 1024), 1200, 600)
    assert info["zoom"] > 1.0 and not info["cut"]["right"]
    x0 = info["content_box"][0]
    assert x0 / info["zoom"] - left / info["zoom"] >= 0.2 * (1200 / info["zoom"]), "왼쪽에 문구 자리"
    # 이미 꽉 찬 피사체는 확대하지 않는다
    full = Image.new("RGB", (1024, 1024), (240, 240, 240))
    ImageDraw.Draw(full).rectangle((40, 40, 984, 984), fill=(30, 60, 120))
    assert compose.crop_window(full, 500, 500)[4]["zoom"] == 1.0


def test_busyness_does_not_treat_yellow_wall_as_skin():
    """노란 단색 벽은 로고를 올리기 좋은 자리다 — 피부색으로 오인해 점수를 올리면 안 된다."""
    from app import compose
    yellow = Image.new("RGB", (200, 120), (250, 205, 80))
    skin = Image.new("RGB", (200, 120), (225, 170, 140))
    assert compose._busyness(yellow) < 0.3 < compose._busyness(skin)


def test_text_stays_below_subject_after_tighten_on_story():
    """스토리(안전영역 위 14%·아래 35%)에서 피사체를 확대해도 문구는 피사체 아래 띠에 들어가고 겹치지 않는다."""
    from app import compose
    im = Image.new("RGB", (1024, 1536), (250, 205, 80))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 900, 1024, 1536), fill=(245, 245, 240))            # 바닥
    for x in range(200, 820, 14):
        d.rectangle((x, 560, x + 7, 900), fill=(60, 80, 120))            # 피사체(가운데)
    _, rep = compose.compose(im, 1440, 2560, "금주의 특가왕, 리빙 창고 대방출", FONT, _logo(),
                             {"top_pct": 14, "bottom_pct": 35, "left_pct": 6, "right_pct": 6}, 30, "#000000", "#FFFFFF")
    assert rep["crop"]["zoom"] > 1.0
    text = next(e for e in rep["elements"] if e["type"] == "text")
    logo = next(e for e in rep["elements"] if e["type"] == "logo")
    sx0, sy0, sx1, sy1 = rep["crop"]["content_box_canvas"]
    assert text["box"][1] >= sy1, "문구는 피사체 아래"
    assert text["font_size"] >= 80 and "겹칠 수 있습니다" not in " ".join(rep["warnings"])
    assert logo["box"][3] <= text["box"][1] or logo["box"][1] >= text["box"][3], "로고와 문구가 겹치지 않는다"


def test_generate_variant_retries_without_input_fidelity(monkeypatch):
    """gpt-image-2.5 계열처럼 input_fidelity 를 거절하는 모델이면 그 인자만 빼고 다시 보낸다 (이후 호출은 처음부터 안 보냄)."""
    import httpx
    import openai
    from app import imagegen
    calls: list[dict] = []

    class FakeImages:
        def edit(self, **kw):
            calls.append(kw)
            if "input_fidelity" in kw:
                raise openai.BadRequestError(
                    "The model does not support the 'input_fidelity' parameter.",
                    response=httpx.Response(400, request=httpx.Request("POST", "http://test")),
                    body={"error": {"message": "The model 'x' does not support the 'input_fidelity' parameter.",
                                    "param": "input_fidelity", "code": "invalid_input_fidelity_model"}})
            import base64, io
            buf = io.BytesIO(); Image.new("RGB", (kw["size"] and 16, 16), "white").save(buf, "PNG")
            return type("R", (), {"data": [type("D", (), {"b64_json": base64.b64encode(buf.getvalue()).decode()})()]})()

    class FakeClient:
        images = FakeImages()
        def with_options(self, **kw): return self
    monkeypatch.setattr(imagegen, "client", lambda: FakeClient())
    monkeypatch.setattr(imagegen, "VARIANT_FIDELITY", "high")
    monkeypatch.setattr(imagegen, "_fidelity_supported", True)
    monkeypatch.setattr(imagegen, "usage", lambda provider=None: {"exhausted": False})
    monkeypatch.setattr(imagegen, "_bump", lambda **kw: None)
    out = imagegen.generate_variant("p", 1536, 1024, Image.new("RGB", (64, 64), "white"))
    assert out.size == (16, 16)
    assert [("input_fidelity" in c) for c in calls] == [True, False]
    imagegen.generate_variant("p", 1536, 1024, Image.new("RGB", (64, 64), "white"))
    assert len(calls) == 3 and "input_fidelity" not in calls[-1], "두 번째 호출부터는 처음부터 보내지 않는다"


def test_variant_failure_falls_back_to_master(env, monkeypatch):
    """두 번째 비율군의 변형 생성이 실패해도(모델 파라미터 거절 등) 첫 배경은 버리지 않고 나머지 비율군이 그 파일을 쓴다."""
    from fastapi import HTTPException
    from app import imagegen
    def boom(prompt, w, h, reference):
        raise RuntimeError("model rejected")
    monkeypatch.setattr(imagegen, "generate_variant", boom)
    c, ids, calls = env["client"], env["ids"], env["calls"]
    out = c.post(f"/api/designs/{ids['design']}/background/regenerate", json={}).json()
    assert out["generated"] == 1 and len(calls) == 1 and out["warnings"] and "RuntimeError" in out["warnings"][0]
    rows = c.get(f"/api/designs/{ids['design']}/images").json()
    files = {r["background"]["file"] for r in rows}
    assert len(files) == 1, "모든 지면이 첫 배경 파일을 공유"
    with env["Local"]() as db:
        land = next(g for g in db.query(env["models"].GeneratedImage).filter_by(kind="background").all()
                    if g.preset_version_id == ids["presets"]["네이티브 2:1"])
        assert land.layout["template"] == "가로형" and "fallback" in land.layout


def _select_copy(env, pids):
    with env["Local"]() as db:
        for pid in pids:
            db.add(env["models"].CopyVariant(design_id=env["ids"]["design"], preset_version_id=pid, variant_no=1,
                                             headline="제목", selected=True, batch_no=1))
        db.commit()


def test_opening_editor_does_not_block_recomposite_but_saving_does(env):
    """편집 화면을 열기만 하면(기본 요소 생성) 자동 합성이 그대로 되고, 저장한 뒤에는 409 로 막는다."""
    from app import models
    c, ids = env["client"], env["ids"]
    pid = ids["presets"]["피드 4:5"]
    _select_copy(env, [pid])
    c.post(f"/api/designs/{ids['design']}/background/regenerate", json={})
    assert c.post(f"/api/designs/{ids['design']}/presets/{pid}/image", json={"regenerate_background": False}).status_code == 200
    j = c.get(f"/api/designs/{ids['design']}/presets/{pid}/edit")
    assert j.status_code == 200 and len(j.json()["elements"]) >= 3, "편집 화면을 열면 기본 요소가 생긴다"
    r = c.post(f"/api/designs/{ids['design']}/presets/{pid}/image", json={"regenerate_background": False})
    assert r.status_code == 200, r.text
    with env["Local"]() as db:
        assert db.query(models.Element).filter_by(design_id=ids["design"], preset_version_id=pid).count() == 0
    # 편집 화면에서 저장(스냅샷)하면 그때부터 막는다
    j = c.get(f"/api/designs/{ids['design']}/presets/{pid}/edit").json()
    text = next(e for e in j["elements"] if e["type"] == "text")
    r = c.put(f"/api/designs/{ids['design']}/presets/{pid}/edit",
              json={"elements": [{"id": text["id"], "props": {**text["props"], "text": "바꾼 제목"}}]})
    assert r.status_code == 200, r.text
    r = c.post(f"/api/designs/{ids['design']}/presets/{pid}/image", json={"regenerate_background": False})
    assert r.status_code == 409 and "저장한 내용" in r.json()["detail"]


def test_reset_generated_can_clear_icon_set(env, tmp_path):
    """초기화 때 include_icons 를 주면 브랜드 규칙 버전의 아이콘 세트(행·파일)도 지운다."""
    from app import models
    c, ids = env["client"], env["ids"]
    files = []
    with env["Local"]() as db:
        for i in range(1, 3):
            png = tmp_path / "storage" / f"icon_{i}.png"; png.parent.mkdir(exist_ok=True)
            Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(png)
            files.append(png)
            db.add(models.IconAsset(design_id=ids["design"], brand_version_id=ids["bv"], batch_no=1, position=i,
                                    name=f"기능{i}", elements=[], style={"render": "3d"}, svg="", png_path=str(png),
                                    width=16, height=16, violations=[]))
        db.commit()
    assert len(c.get(f"/api/designs/{ids['design']}/icons").json()) == 2
    r = c.post(f"/api/designs/{ids['design']}/reset-generated", json={"include_copy": False, "include_icons": False})
    assert r.json()["icons_deleted"] == 0 and len(c.get(f"/api/designs/{ids['design']}/icons").json()) == 2
    r = c.post(f"/api/designs/{ids['design']}/reset-generated", json={"include_copy": False, "include_icons": True})
    assert r.json()["icons_deleted"] == 2 and c.get(f"/api/designs/{ids['design']}/icons").json() == []
    assert not any(f.exists() for f in files), "아이콘 파일도 지운다"
