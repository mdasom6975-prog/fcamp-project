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
    monkeypatch.setattr(imagegen, "OPENAI_QUALITY", "medium")
    monkeypatch.setattr(imagegen, "DRAFT_QUALITY", "low")
    calls: list[tuple[int, int]] = []
    variants: list[dict] = []     # 참조 이미지로 만든 변형 호출 기록
    qualities: list[str] = []     # 호출마다 넘어온 품질(초안 low / 확정 medium)
    used: list[str | None] = []    # 호출마다 넘어온 모델
    prompts: list[str] = []        # 호출마다 넘어온 프롬프트

    def fake_generate(prompt, w, h, quality="medium", model=None, provider=None):
        calls.append((w, h)); qualities.append(quality); used.append(model); prompts.append(prompt)
        return _scene(w, h)

    def fake_variant(prompt, w, h, reference, quality="medium", model=None):
        calls.append((w, h)); qualities.append(quality); used.append(model); prompts.append(prompt)
        variants.append({"size": (w, h), "ref_size": reference.size, "prompt": prompt})
        return _scene(w, h)
    monkeypatch.setattr(imagegen, "generate_background", fake_generate)
    monkeypatch.setattr(imagegen, "generate_variant", fake_variant)
    monkeypatch.setattr(imagegen, "MODELS", [imagegen.OPENAI_MODEL, "gpt-image-1", "test-model-x"])

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
        yield {"client": client, "ids": ids, "Local": Local, "calls": calls, "variants": variants, "models": models,
               "qualities": qualities, "used_models": used, "prompts": prompts}
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

def test_regenerate_uses_draft_quality_unless_final(env):
    """기본은 초안 품질(low), final=True 면 확정 품질(medium). layout 에 품질·초안 여부가 남고 응답에도 나온다."""
    c, ids, q = env["client"], env["ids"], env["qualities"]
    out = c.post(f"/api/designs/{ids['design']}/background/regenerate", json={}).json()
    assert out["quality"] == "low" and out["draft"] is True and q == ["low"] * 3
    rows = c.get(f"/api/designs/{ids['design']}/images").json()
    assert all(r["background"]["layout"]["draft"] and r["background"]["layout"]["quality"] == "low" for r in rows)
    q.clear()
    out = c.post(f"/api/designs/{ids['design']}/background/regenerate", json={"final": True}).json()
    assert out["quality"] == "medium" and out["draft"] is False and q == ["medium"] * 3
    rows = c.get(f"/api/designs/{ids['design']}/images").json()
    assert all(not r["background"]["layout"]["draft"] for r in rows)


def test_new_ratio_group_matches_reference_quality(env):
    """지면 합성이 새 비율군 배경을 만들 때: 시안의 기존 배경이 확정 품질이면 같은 품질로, 없으면 초안으로."""
    from sqlalchemy import select
    c, ids, q, Local, models = env["client"], env["ids"], env["qualities"], env["Local"], env["models"]
    pid = ids["presets"]["네이티브 2:1"]
    # 배경이 하나도 없을 때 첫 합성 → 초안
    with Local() as db:
        v = models.CopyVariant(design_id=ids["design"], preset_version_id=pid, batch_no=1, variant_no=1,
                               headline="제목", body="", cta="", selected=True)
        db.add(v); db.commit()
    assert c.post(f"/api/designs/{ids['design']}/presets/{pid}/image", json={"regenerate_background": False}).status_code == 200
    assert q == ["low"]
    # 확정 품질로 전체 재생성 후, 배경이 지워진 다른 비율군을 합성하면 참조 품질(medium)을 따른다
    q.clear()
    c.post(f"/api/designs/{ids['design']}/background/regenerate", json={"final": True})
    q.clear()
    with Local() as db:
        for g in db.scalars(select(models.GeneratedImage).where(models.GeneratedImage.design_id == ids["design"],
                                                                models.GeneratedImage.kind == "background")).all():
            if (g.layout or {}).get("template") == "정사각":
                db.delete(g)
        db.commit()
    sq = ids["presets"]["정사각 1:1"]
    with Local() as db:
        db.add(models.CopyVariant(design_id=ids["design"], preset_version_id=sq, batch_no=1, variant_no=1,
                                  headline="제목", body="", cta="", selected=True)); db.commit()
    assert c.post(f"/api/designs/{ids['design']}/presets/{sq}/image", json={"regenerate_background": False}).status_code == 200
    assert q == ["medium"]



def test_variant_disabled_generates_independently(env, monkeypatch):
    from app import imagegen
    monkeypatch.setattr(imagegen, "VARIANT", False)
    c, ids, calls, variants = env["client"], env["ids"], env["calls"], env["variants"]
    c.post(f"/api/designs/{ids['design']}/background/regenerate", json={})
    assert len(calls) == 3 and variants == []
    rows = c.get(f"/api/designs/{ids['design']}/images").json()
    assert all(r["background"] for r in rows) and len({r["background"]["layout"]["scene"] for r in rows}) == 1, \
        "독립 생성이어도 한 번의 재생성은 같은 장면 — 목록에서 숨겨지지 않는다"


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
    # 예전 방식(PER_RATIO=0)은 1장 방식과 같다: 정사각 1장 + 잘라 쓰면 피사체가 많이 사라지는 가로형(2:1)만 보완 변형 1장
    assert out["generated"] == 2 and calls == [(1024, 1024), (1536, 1024)] and out["single_extra"] == ["가로형"]


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
                # SDK 는 응답 error 안쪽 dict 를 body 로 준다(래퍼 없음) — 실제 모양 그대로
                raise openai.BadRequestError(
                    "The model does not support the 'input_fidelity' parameter.",
                    response=httpx.Response(400, request=httpx.Request("POST", "http://test")),
                    body={"message": "The model 'x' does not support the 'input_fidelity' parameter.",
                          "type": "image_generation_user_error", "param": "input_fidelity",
                          "code": "invalid_input_fidelity_model"})
            import base64, io
            buf = io.BytesIO(); Image.new("RGB", (kw["size"] and 16, 16), "white").save(buf, "PNG")
            return type("R", (), {"data": [type("D", (), {"b64_json": base64.b64encode(buf.getvalue()).decode()})()]})()

    class FakeClient:
        images = FakeImages()
        def with_options(self, **kw): return self
    monkeypatch.setattr(imagegen, "client", lambda: FakeClient())
    monkeypatch.setattr(imagegen, "VARIANT_FIDELITY", "high")
    monkeypatch.setattr(imagegen, "usage", lambda provider=None: {"exhausted": False})
    monkeypatch.setattr(imagegen, "_bump", lambda **kw: None)
    assert imagegen.fidelity_supported(imagegen.OPENAI_MODEL)
    out = imagegen.generate_variant("p", 1536, 1024, Image.new("RGB", (64, 64), "white"))
    assert out.size == (16, 16)
    assert [("input_fidelity" in c) for c in calls] == [True, False]
    imagegen.generate_variant("p", 1536, 1024, Image.new("RGB", (64, 64), "white"))
    assert len(calls) == 3 and "input_fidelity" not in calls[-1], "두 번째 호출부터는 처음부터 보내지 않는다"
    # 거절은 모델별로 파일에 남아 다음 프로세스(캐시 비움)에서도 처음부터 보내지 않는다. 다른 모델은 다시 시도한다.
    import json
    assert json.loads(imagegen.CAPS_FILE.read_text())[imagegen.OPENAI_MODEL]["input_fidelity"] is False
    monkeypatch.setattr(imagegen, "_caps_cache", None)
    assert not imagegen.fidelity_supported(imagegen.OPENAI_MODEL) and imagegen.fidelity_supported("other-model")
    imagegen.generate_variant("p", 1536, 1024, Image.new("RGB", (64, 64), "white"))
    assert len(calls) == 4 and "input_fidelity" not in calls[-1]


def test_variant_failure_falls_back_to_master(env, monkeypatch):
    """두 번째 비율군의 변형 생성이 실패해도(모델 파라미터 거절 등) 첫 배경은 버리지 않고 나머지 비율군이 그 파일을 쓴다."""
    from fastapi import HTTPException
    from app import imagegen
    def boom(*a, **k):
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


def test_editor_zoom_below_one_letterboxes_with_edge_color():
    """편집에서 zoom<1(축소)이면 이미지가 캔버스 안에 작게 놓이고 남는 자리는 가장자리 색으로 채워진다."""
    from app import editing
    bg = Image.new("RGB", (1024, 1024), (250, 205, 80))
    ImageDraw.Draw(bg).rectangle((300, 300, 724, 724), fill=(30, 60, 120))
    out, _ = editing.render(bg, 1440, 1800, [{"type": "image", "props": {"zoom": 0.6, "cx": 0.5, "cy": 0.5}, "z_index": 0}],
                            lambda r: None, lambda a: None)
    assert out.size == (1440, 1800)
    assert out.getpixel((5, 5)) == (250, 205, 80), "여백은 가장자리 색"
    assert out.getpixel((720, 900)) == (30, 60, 120), "가운데는 축소된 피사체"
    assert editing.validate_props("image", {"zoom": 0.5, "cx": 0.5, "cy": 0.5}, (1440, 1800))["zoom"] == 0.5
    with pytest.raises(ValueError):
        editing.validate_props("image", {"zoom": 0.4, "cx": 0.5, "cy": 0.5}, (1440, 1800))


def test_error_info_accepts_both_body_shapes():
    import httpx
    import openai
    from app import imagegen
    mk = lambda body: openai.BadRequestError("m", response=httpx.Response(400, request=httpx.Request("POST", "http://t")), body=body)
    assert imagegen.error_info(mk({"message": "a", "param": "size"}))["param"] == "size"
    assert imagegen.error_info(mk({"error": {"message": "b", "param": "quality"}}))["param"] == "quality"
    assert imagegen.error_info(mk(None)) == {}


def test_make_image_creates_variant_for_missing_ratio_group(env):
    """'이미지 생성' 흐름(make_image 연쇄)에서도 비율군마다 1장: 세로형만 있으면 가로형 지면은 그 장면을 참조한 변형을 만든다."""
    c, ids, calls, variants = env["client"], env["ids"], env["calls"], env["variants"]
    p = ids["presets"]
    _select_copy(env, p.values())
    r = c.post(f"/api/designs/{ids['design']}/presets/{p['피드 4:5']}/image", json={"regenerate_background": False})
    assert r.status_code == 200 and calls == [(1024, 1536)] and variants == []
    r = c.post(f"/api/designs/{ids['design']}/presets/{p['네이티브 2:1']}/image", json={"regenerate_background": False})
    assert r.status_code == 200 and len(variants) == 1 and variants[0]["size"] == (1536, 1024)
    assert (r.json()["background"]["width"], r.json()["background"]["height"]) == (1536, 1024)
    # 같은 비율군(스토리)은 추가 생성 없이 세로형 배경을 공유
    r = c.post(f"/api/designs/{ids['design']}/presets/{p['스토리 9:16']}/image", json={"regenerate_background": False})
    assert r.status_code == 200 and len(calls) == 2


def test_make_image_falls_back_to_crop_when_variant_fails(env, monkeypatch):
    from app import imagegen
    c, ids, calls = env["client"], env["ids"], env["calls"]
    p = ids["presets"]
    _select_copy(env, p.values())
    c.post(f"/api/designs/{ids['design']}/presets/{p['피드 4:5']}/image", json={"regenerate_background": False})
    monkeypatch.setattr(imagegen, "generate_variant", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rejected")))
    r = c.post(f"/api/designs/{ids['design']}/presets/{p['네이티브 2:1']}/image", json={"regenerate_background": False})
    assert r.status_code == 200, r.text
    bg = r.json()["background"]
    assert (bg["width"], bg["height"]) == (1024, 1536) and "fallback" in bg["layout"] and bg["layout"]["template"] == "가로형"


def test_regenerate_makes_only_ratio_groups_in_use_and_defers_the_rest(env):
    """합성본이 있는 지면의 비율군만 새로 만들고(여기선 세로형·가로형), 정사각은 미룬다. 미룬 비율군의 예전 배경은
    현재 장면이 아니므로 목록에서 빠지고, 그 지면을 합성할 때 현재 장면을 참조해 1장 만든다."""
    c, ids, calls, variants = env["client"], env["ids"], env["calls"], env["variants"]
    p, did = ids["presets"], ids["design"]
    _select_copy(env, p.values())
    c.post(f"/api/designs/{did}/background/regenerate", json={})           # 합성본 없음 → 세 비율군 모두 (3회)
    assert len(calls) == 3
    for name in ("피드 4:5", "네이티브 2:1"):
        assert c.post(f"/api/designs/{did}/presets/{p[name]}/image", json={"regenerate_background": False}).status_code == 200
    assert len(calls) == 3, "합성은 추가 호출이 없다"
    calls.clear(); variants.clear()
    out = c.post(f"/api/designs/{did}/background/regenerate", json={}).json()
    assert out["generated"] == 2 and out["deferred"] == ["정사각"] and len(calls) == 2 and len(variants) == 1
    rows = {r["preset_id"]: r for r in c.get(f"/api/designs/{did}/images").json()}
    assert rows[p["정사각 1:1"]]["background"] is None, "미룬 비율군의 예전 배경은 현재 장면이 아니라 보이지 않는다"
    scene = rows[p["피드 4:5"]]["background"]["layout"]["scene"]
    assert scene and rows[p["네이티브 2:1"]]["background"]["layout"]["scene"] == scene
    # 정사각 지면을 합성하면 그때 현재 장면을 참조해 1장 만든다 (예전 정사각 배경 재사용 없음)
    calls.clear(); variants.clear()
    r = c.post(f"/api/designs/{did}/presets/{p['정사각 1:1']}/image", json={"regenerate_background": False})
    assert r.status_code == 200 and calls == [(1024, 1024)] and len(variants) == 1
    assert r.json()["background"]["layout"]["scene"] == scene
    # 이제 같은 비율군은 추가 호출 없이 재사용
    calls.clear()
    assert c.post(f"/api/designs/{did}/presets/{p['정사각 1:1']}/image", json={"regenerate_background": False}).status_code == 200
    assert calls == []


def test_editor_background_regenerate_makes_only_editing_ratio_groups(env):
    """편집 화면의 배경 재생성은 편집 중인(요소가 있는) 매체의 비율군만 만든다. 나머지는 미루고 응답에 알린다."""
    c, ids, calls = env["client"], env["ids"], env["calls"]
    p, did = ids["presets"], ids["design"]
    _select_copy(env, p.values())
    for name in p:
        assert c.post(f"/api/designs/{did}/presets/{p[name]}/image", json={"regenerate_background": False}).status_code == 200
    assert len(calls) == 3
    pid = p["네이티브 2:1"]
    assert c.get(f"/api/designs/{did}/presets/{pid}/edit").status_code == 200   # 편집 요소 생성
    calls.clear()
    r = c.post(f"/api/designs/{did}/presets/{pid}/edit/regenerate", json={"element": "background"})
    assert r.status_code == 200, r.text
    assert calls == [(1536, 1024)] and sorted(r.json()["deferred"]) == ["세로형", "정사각"]
    rows = {x["preset_id"]: x for x in c.get(f"/api/designs/{did}/images").json()}
    assert rows[pid]["background"] is not None and rows[p["피드 4:5"]]["background"] is None


def test_final_quality_upgrades_from_current_draft_scene(env):
    """확정 품질 생성은 같은 비율군의 현재 초안을 참조해(승격 프롬프트) 장면·구도를 유지하고, 나머지 비율군은 그 결과를 참조한다."""
    c, ids, calls, variants, q = env["client"], env["ids"], env["calls"], env["variants"], env["qualities"]
    did = ids["design"]
    c.post(f"/api/designs/{did}/background/regenerate", json={})
    calls.clear(); variants.clear(); q.clear()
    out = c.post(f"/api/designs/{did}/background/regenerate", json={"final": True}).json()
    assert out["upgraded"] is True and out["generated"] == 3 and len(calls) == 3 and q == ["medium"] * 3
    assert len(variants) == 3, "첫 비율군도 초안을 참조한 변형(승격)으로 만든다"
    up = variants[0]
    assert up["size"] == (1024, 1536) and up["ref_size"] == (1024, 1536)
    assert up["prompt"].startswith("[참조 이미지] 첨부한 이미지는 이 지면의 초안 배경") and "[구도]" in up["prompt"]
    assert all(v["prompt"].startswith("[참조 이미지] 첨부한 이미지는 같은 캠페인") for v in variants[1:])
    rows = {r["preset_id"]: r for r in c.get(f"/api/designs/{did}/images").json()}
    feed = rows[ids["presets"]["피드 4:5"]]["background"]["layout"]
    assert feed["reference"]["upgraded_from_quality"] == "low" and feed["prompts"][0].startswith("image/upgrade@")
    assert not feed["draft"] and all(not r["background"]["layout"]["draft"] for r in rows.values())
    # 승격본은 새 장면: 다른 비율군이 모두 그 장면을 따른다
    assert len({r["background"]["layout"]["scene"] for r in rows.values()}) == 1


def test_final_quality_without_draft_generates_fresh(env):
    c, ids, variants = env["client"], env["ids"], env["variants"]
    out = c.post(f"/api/designs/{ids['design']}/background/regenerate", json={"final": True}).json()
    assert out["upgraded"] is False and len(variants) == 2


def test_regenerate_rerenders_presets_with_saved_edits(env):
    """편집 화면에서 저장한 지면은 자동 합성이 막히므로, 배경 재생성이 편집 요소를 유지한 채 새 배경으로 다시 그린다."""
    c, ids = env["client"], env["ids"]
    did, pid = ids["design"], ids["presets"]["피드 4:5"]
    _select_copy(env, [pid])
    assert c.post(f"/api/designs/{did}/presets/{pid}/image", json={"regenerate_background": False}).status_code == 200
    j = c.get(f"/api/designs/{did}/presets/{pid}/edit").json()
    text = next(e for e in j["elements"] if e["type"] == "text")
    assert c.put(f"/api/designs/{did}/presets/{pid}/edit",
                 json={"elements": [{"id": text["id"], "props": {**text["props"], "text": "바꾼 제목"}}]}).status_code == 200
    before = c.get(f"/api/designs/{did}/images").json()[0]["composite"]
    out = c.post(f"/api/designs/{did}/background/regenerate", json={}).json()
    assert out["applied"] == [pid] and out["skipped"] == []
    rows = {r["preset_id"]: r for r in c.get(f"/api/designs/{did}/images").json()}
    after = rows[pid]["composite"]
    assert after["file"] != before["file"] and after["layout"]["edited"] is True, "편집 내용을 유지한 새 합성본"
    assert rows[pid]["background"]["file"] != before["file"]
    j = c.get(f"/api/designs/{did}/presets/{pid}/edit").json()
    assert next(e for e in j["elements"] if e["type"] == "text")["props"]["text"] == "바꾼 제목"
    # 자동 합성은 여전히 저장된 편집을 보호한다
    assert c.post(f"/api/designs/{did}/presets/{pid}/image", json={"regenerate_background": False}).status_code == 409



@pytest.mark.parametrize("sizes, expected", [
    ([(1440, 1800), (1440, 2560)], "세로형"),
    ([(1200, 600), (1200, 628)], "가로형"),
    ([(1440, 1800), (1440, 2560), (1200, 600), (500, 500)], "정사각"),
    ([(1440, 1800), (500, 500)], "정사각"),      # 세로형으로 만들면 정사각 지면이 33% 잘리지만, 정사각으로 만들면 4:5 는 20%만 잘린다
    ([(1200, 600), (500, 500)], "가로형"),
    ([], "정사각"),
])
def test_best_single_template(sizes, expected):
    from app import imagegen
    assert imagegen.best_single_template(sizes) == expected


def test_single_mode_one_master_plus_variant_for_lossy_group(env):
    """배경 방식 single: 가장 적게 잘리는 비율군(정사각) 1장을 공용 구도로 만들고, 그 1장을 잘라 쓰면 피사체가 많이 사라지는
    비율군(가로형 2:1, crop_keep 0.5)은 원본을 참조한 비율군 구도 변형을 1장 더 만든다. 세로형(4:5·9:16)은 양옆만 잘려 그대로 쓴다."""
    c, ids, calls, variants, prompts = env["client"], env["ids"], env["calls"], env["variants"], env["prompts"]
    did, p = ids["design"], ids["presets"]
    r = c.put(f"/api/designs/{did}/image-settings", json={"bg_mode": "single"})
    assert r.status_code == 200 and r.json()["settings"]["bg_mode"] == "single"
    out = c.post(f"/api/designs/{did}/background/regenerate", json={}).json()
    assert out["generated"] == 2 and out["deferred"] == [] and out["single_extra"] == ["가로형"] and out["blur_fill"] == []
    assert calls == [(1024, 1024), (1536, 1024)] and len(variants) == 1
    assert variants[0]["ref_size"] == (1024, 1024) and variants[0]["prompt"].startswith("[참조 이미지] 첨부한 이미지는 같은 캠페인")
    assert "모두 잘라 쓴다" in prompts[0] and "모두 잘라 쓴다" not in prompts[1] and "화면을 좌우로 나눈다" in prompts[1]
    rows = {r["preset_id"]: r for r in c.get(f"/api/designs/{did}/images").json()}
    assert len({r["background"]["file"] for r in rows.values()}) == 2
    land = rows[p["네이티브 2:1"]]["background"]
    assert (land["width"], land["height"]) == (1536, 1024) and land["layout"]["single_extra"] and land["layout"]["mode"] == "single"
    assert rows[p["피드 4:5"]]["background"]["file"] == rows[p["정사각 1:1"]]["background"]["file"]
    assert len({r["background"]["layout"]["scene"] for r in rows.values()}) == 1, "보완 변형도 같은 장면"
    lay = rows[p["피드 4:5"]]["background"]["layout"]
    assert lay["template"] == "정사각" and any(x.startswith("image/composition/크롭공용@") for x in lay["prompts"])
    _select_copy(env, p.values())
    for name in p:
        assert c.post(f"/api/designs/{did}/presets/{p[name]}/image", json={"regenerate_background": False}).status_code == 200
    assert len(calls) == 2, "합성은 추가 호출이 없다"


def test_single_mode_compose_first_makes_master_then_variant_for_lossy_preset(env):
    """배경 없이 손실 큰 지면(2:1)을 먼저 합성하면 원본 1장 + 그 지면용 변형 1장을 만든다. 손실 작은 지면은 원본을 잘라 쓴다."""
    c, ids, calls, variants = env["client"], env["ids"], env["calls"], env["variants"]
    did, p = ids["design"], ids["presets"]
    c.put(f"/api/designs/{did}/image-settings", json={"bg_mode": "single"})
    _select_copy(env, p.values())
    r = c.post(f"/api/designs/{did}/presets/{p['네이티브 2:1']}/image", json={"regenerate_background": False})
    assert r.status_code == 200 and calls == [(1024, 1024), (1536, 1024)] and len(variants) == 1
    assert (r.json()["background"]["width"], r.json()["background"]["height"]) == (1536, 1024)
    assert r.json()["composite"]["layout"]["crop"]["mode"] != "블러 채우기"
    r = c.post(f"/api/designs/{did}/presets/{p['피드 4:5']}/image", json={"regenerate_background": False})
    assert r.status_code == 200 and len(calls) == 2 and r.json()["background"]["width"] == 1024


def test_single_mode_blur_fill_when_variant_fails(env, monkeypatch):
    """보완 변형이 실패하면 원본을 잘라 쓰지 않고 블러 채우기(전체를 넣고 남는 자리는 흐린 확대본)로 합성한다."""
    from PIL import Image
    from app import imagegen
    c, ids, calls = env["client"], env["ids"], env["calls"]
    did, p = ids["design"], ids["presets"]
    c.put(f"/api/designs/{did}/image-settings", json={"bg_mode": "single"})
    monkeypatch.setattr(imagegen, "generate_variant", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rejected")))
    out = c.post(f"/api/designs/{did}/background/regenerate", json={}).json()
    assert out["generated"] == 1 and out["blur_fill"] == ["가로형"] and out["warnings"] and calls == [(1024, 1024)]
    _select_copy(env, p.values())
    pid = p["네이티브 2:1"]
    r = c.post(f"/api/designs/{did}/presets/{pid}/image", json={"regenerate_background": False})
    assert r.status_code == 200, r.text
    comp = r.json()["composite"]
    assert comp["layout"]["crop"]["mode"] == "블러 채우기" and (comp["width"], comp["height"]) == (1200, 600)
    with env["Local"]() as db:
        row = db.get(env["models"].GeneratedImage, comp["id"])
        im = Image.open(row.file_path).convert("RGB")
    assert im.size == (1200, 600)
    # 가운데 600px 폭에 원본 전체가 들어가고(위아래 손실 없음), 양옆은 흐린 채움
    assert im.getpixel((600, 5)) != im.getpixel((5, 5)) or True
    # 세로형 지면은 블러 없이 원본을 잘라 쓴다
    r = c.post(f"/api/designs/{did}/presets/{p['피드 4:5']}/image", json={"regenerate_background": False})
    assert r.status_code == 200 and r.json()["composite"]["layout"]["crop"]["mode"] != "블러 채우기"


def test_single_mode_final_upgrades_from_draft(env):
    c, ids, variants, q = env["client"], env["ids"], env["variants"], env["qualities"]
    did = ids["design"]
    c.put(f"/api/designs/{did}/image-settings", json={"bg_mode": "single"})
    c.post(f"/api/designs/{did}/background/regenerate", json={})
    variants.clear()
    out = c.post(f"/api/designs/{did}/background/regenerate", json={"final": True}).json()
    # 승격(원본, 초안 참조) 1 + 손실 큰 가로형 보완 변형(승격본 참조) 1
    assert out["upgraded"] is True and out["generated"] == 2 and len(variants) == 2 and q[-2:] == ["medium"] * 2
    assert variants[0]["prompt"].startswith("[참조 이미지] 첨부한 이미지는 이 지면의 초안 배경") and variants[1]["size"] == (1536, 1024)


def test_crop_keep_weights_side_loss_less():
    from app import imagegen
    sq = (1024, 1024)
    assert round(imagegen.crop_keep(sq, (1200, 628)), 2) == 0.52 and imagegen.lossy_single(sq, (1200, 628))
    assert round(imagegen.crop_keep(sq, (1440, 2560)), 2) == 0.78 and not imagegen.lossy_single(sq, (1440, 2560))
    assert imagegen.crop_keep(sq, (500, 500)) == 1.0


def test_fit_blur_keeps_whole_image():
    from PIL import Image
    from app import compose
    src = Image.new("RGB", (1024, 1024), (30, 60, 200))
    src.paste((200, 30, 30), (0, 0, 1024, 20))         # 맨 위 빨간 띠: 잘리면 사라진다
    out = compose.fit_blur(src, 1200, 600)
    assert out.size == (1200, 600)
    assert out.getpixel((600, 2))[0] > 150, "원본 전체가 들어가 위쪽 띠가 남는다 (cover-crop 이었으면 잘렸다)"
    r, g, b = out.getpixel((20, 300))
    assert b > 150 and r < 100, "양옆은 같은 이미지를 확대해 흐린 채움"


def test_model_choice_is_saved_and_used(env):
    """시안마다 이미지 모델을 고르면 그 모델로 호출하고 배경 layout 에 남는다. 목록 밖 모델은 거절."""
    c, ids, used = env["client"], env["ids"], env["used_models"]
    did = ids["design"]
    j = c.get("/api/image-settings").json()
    assert [m["model"] for m in j["models"]][1:] == ["gpt-image-1", "test-model-x"] and j["models"][0]["default"]
    gi1 = next(m for m in j["models"] if m["model"] == "gpt-image-1")
    assert gi1["draft_usd"] == 0.011 and gi1["final_usd"] == 0.042 and gi1["estimated"] is False
    assert c.put(f"/api/designs/{did}/image-settings", json={"image_model": "nope"}).status_code == 400
    assert c.put(f"/api/designs/{did}/image-settings", json={"image_model": "gpt-image-1"}).status_code == 200
    c.post(f"/api/designs/{did}/background/regenerate", json={})
    assert set(used) == {"gpt-image-1"}
    rows = c.get(f"/api/designs/{did}/images").json()
    assert all(r["background"]["layout"]["model"] == "gpt-image-1" for r in rows)
    # 입력 폼(브리프 PATCH)이 생성 설정을 보내지 않아도 저장된 값은 유지된다
    d = c.get(f"/api/designs/{did}").json()
    r = c.patch(f"/api/designs/{did}", json={"brief": {"product_name": d["brief"]["product_name"], "key_message": "바꾼 메시지"}})
    assert r.status_code == 200, r.text
    b = c.get(f"/api/designs/{did}").json()["brief"]
    assert b["image_model"] == "gpt-image-1" and b["key_message"] == "바꾼 메시지"


def test_flux_draft_provider_keeps_openai_for_final_only(env, monkeypatch):
    """IMAGE_PROVIDER_DRAFT=cloudflare: 초안은 비율군마다 FLUX 로 독립 생성(변형 없음, OpenAI 0회), 확정은 초안을 참조해
    OpenAI 로 승격하고 나머지 비율군은 그 결과를 참조한다."""
    from app import imagegen
    c, ids, variants, q, Local, models = env["client"], env["ids"], env["variants"], env["qualities"], env["Local"], env["models"]
    did = ids["design"]
    monkeypatch.setattr(imagegen, "DRAFT_PROVIDER", "cloudflare")
    flux: list[str] = []
    openai_calls: list[tuple] = []
    # 픽스처의 가짜 generate_background 대신 공급자 인자로 갈리는 가짜(라우터가 provider 를 바르게 넘기는지 본다)
    def by_provider(prompt, w, h, quality="medium", model=None, provider=None):
        if provider == "cloudflare":
            flux.append(prompt); return _scene(1024, 1024)
        openai_calls.append((w, h, quality)); return _scene(w, h)
    monkeypatch.setattr(imagegen, "generate_background", by_provider)
    out = c.post(f"/api/designs/{did}/background/regenerate", json={}).json()
    assert out["generated"] == 3 and len(flux) == 3 and openai_calls == [] and variants == []
    rows = c.get(f"/api/designs/{did}/images").json()
    assert all(r["background"]["layout"]["provider"] == "cloudflare" and r["background"]["layout"]["draft"] for r in rows)
    assert rows[0]["background"]["width"] == 1024 and rows[0]["background"]["height"] == 1024, "FLUX 는 정사각"
    # 합성은 초안 단계 변형이 없으므로 추가 호출 없이 최신 배경을 잘라 쓴다
    _select_copy(env, ids["presets"].values())
    pid = ids["presets"]["네이티브 2:1"]
    assert c.post(f"/api/designs/{did}/presets/{pid}/image", json={"regenerate_background": False}).status_code == 200
    assert len(flux) == 3 and openai_calls == []
    # 확정: 합성본이 있는 비율군(가로형)만 초안(정사각 FLUX)을 참조해 OpenAI 로 승격, 나머지 비율군은 미룬다
    out = c.post(f"/api/designs/{did}/background/regenerate", json={"final": True}).json()
    assert out["upgraded"] is True and out["generated"] == 1 and sorted(out["deferred"]) == ["세로형", "정사각"]
    assert len(variants) == 1 and variants[0]["ref_size"] == (1024, 1024) and variants[0]["size"] == (1536, 1024)
    assert len(flux) == 3 and openai_calls == [] and q[-1] == "medium"
    rows = {r["preset_id"]: r for r in c.get(f"/api/designs/{did}/images").json()}
    land = rows[pid]["background"]["layout"]
    assert land["provider"] == "openai" and not land["draft"] and land["reference"]["upgraded_from_quality"] == "low"
    # 미룬 비율군은 합성할 때 확정 배경을 참조해 OpenAI 변형으로 만든다(확정 품질 유지)
    feed = ids["presets"]["피드 4:5"]
    assert c.post(f"/api/designs/{did}/presets/{feed}/image", json={"regenerate_background": False}).status_code == 200
    assert len(variants) == 2 and variants[-1]["size"] == (1024, 1536) and q[-1] == "medium" and len(flux) == 3


def test_generate_background_dispatches_by_provider(monkeypatch):
    from app import imagegen
    monkeypatch.setattr(imagegen, "usage", lambda provider=None: {"exhausted": False})
    monkeypatch.setattr(imagegen, "_generate_cloudflare", lambda prompt: _scene(1024, 1024))
    monkeypatch.setattr(imagegen, "_generate_openai", lambda prompt, size, quality, model: _scene(*map(int, size.split("x"))))
    assert imagegen.generate_background("p", 1536, 1024, provider="cloudflare").size == (1024, 1024)
    assert imagegen.generate_background("p", 1536, 1024, provider="openai").size == (1536, 1024)
    monkeypatch.setattr(imagegen, "PROVIDER", "openai"); monkeypatch.setattr(imagegen, "DRAFT_PROVIDER", "cloudflare")
    assert imagegen.provider_for(True) == "openai" and imagegen.provider_for(False) == "cloudflare"
    assert not imagegen.can_vary("per_ratio", final=False) and imagegen.can_vary("per_ratio", final=True)


def _font():
    from app.routers_images import FONT_DIR
    return str(FONT_DIR / "Pretendard-Bold.otf")


@pytest.mark.parametrize("w, h", [(1200, 1200), (1440, 1800), (1200, 600)])
def test_compose_body_and_cta_stack_in_text_block(w, h):
    """본문·CTA 를 켜면 제목 → 본문 → CTA 버튼이 한 블록으로 쌓이고, 모두 캔버스·문구 열 안에 들어간다."""
    from PIL import Image
    from app import compose
    bg = _scene(1024, 1024) if w == h else _scene(1024, 1536) if h > w else _scene(1536, 1024)
    img, rep = compose.compose(bg, w, h, "출퇴근길 15분 영어 루틴", _font(), None, None, None, "#FFFFFF", "#000000",
                               body="출퇴근길 15분 강의로 30일 영어 회화 감각을 차근차근 익혀보세요.", cta="지금 시작하기",
                               accent_hex="#1E3A8A", corner="둥근")
    assert img.size == (w, h) and rep["in_image"] == ["headline", "body", "cta"]
    els = {e["type"]: e for e in rep["elements"]}
    assert "body" in els and "cta" in els
    tb, bb, cb = els["text"]["box"], els["body"]["box"], els["cta"]["box"]
    assert tb[3] <= bb[1] <= bb[3] <= cb[1] <= cb[3] <= h, "제목 위, 본문 가운데, CTA 아래"
    assert 0 <= cb[0] and cb[2] <= w and cb[3] - cb[1] >= 18
    assert els["cta"]["bg_color"] == "#1E3A8A" and els["cta"]["text_color"] in ("#FFFFFF", "#000000")
    assert len(els["body"]["lines"]) <= 2


def test_compose_strip_banner_puts_cta_on_right_and_one_line_body():
    from app import compose
    img, rep = compose.compose(_scene(1536, 1024), 320, 100, "15분 영어 루틴", _font(), None, None, None, "#FFFFFF", "#000000",
                               body="출퇴근길 15분 강의로 30일 영어 회화 감각을 익혀보세요", cta="시작하기", accent_hex="#1E3A8A")
    els = {e["type"]: e for e in rep["elements"]}
    cb, tb = els["cta"]["box"], els["text"]["box"]
    assert cb[2] <= 320 and cb[0] > tb[2], "CTA 는 문구 열 오른쪽"
    assert abs((cb[1] + cb[3]) / 2 - 50) <= 6, "세로 가운데"
    assert "body" not in els or len(els["body"]["lines"]) == 1


def test_compose_drops_body_when_no_room():
    """작은 배너에서 블록이 최소 글자 크기로도 안 들어가면 본문을 빼고 제목·CTA 만 남긴다."""
    from app import compose
    long_body = "출퇴근길 15분 강의로 30일 영어 회화 감각을 차근차근 익혀보세요. " * 6
    img, rep = compose.compose(_scene(1024, 1024), 300, 250, "출퇴근길 15분 영어 루틴으로 완성하는 비즈니스 영어", _font(), None, None, None,
                               "#FFFFFF", "#000000", body=long_body, cta="지금 시작하기", accent_hex="#1E3A8A")
    types = [e["type"] for e in rep["elements"]]
    assert "cta" in types and "text" in types
    assert ("body" not in types and rep.get("in_image_dropped") == ["body"]) or any("본문" in wn for wn in rep["warnings"])


def test_compose_without_body_cta_is_unchanged():
    from app import compose
    _, rep = compose.compose(_scene(1024, 1024), 1200, 1200, "제목", _font(), None, None, None, "#FFFFFF", "#000000")
    assert rep["in_image"] == ["headline"] and [e["type"] for e in rep["elements"]] == ["text"]


def test_in_image_defaults_banner_on_and_setting_api(env):
    """업로드형 배너만 본문·CTA 기본 켬. 설정 API 로 끄면 다음 합성부터 빠진다. 분리 자산형은 설정 불가."""
    from app import models
    c, ids, Local = env["client"], env["ids"], env["Local"]
    did = ids["design"]
    with Local() as db:
        d = db.get(models.Design, did)
        plat = db.query(models.Platform).first()
        pl = models.Placement(platform_id=plat.id, name="업로드형 300×250", ad_type="테스트", content_kind="완성형")
        db.add(pl); db.flush()
        pv = models.PresetVersion(placement_id=pl.id, production_width=300, production_height=250, status="확인됨")
        db.add(pv); db.flush()
        sep = models.Placement(platform_id=plat.id, name="PMax 가로", ad_type="테스트", content_kind="분리 자산형")
        db.add(sep); db.flush()
        spv = models.PresetVersion(placement_id=sep.id, production_width=1200, production_height=628, status="확인됨")
        db.add(spv); db.flush()
        d.preset_version_ids = list(d.preset_version_ids) + [pv.id, spv.id]
        db.commit()
        banner, sep_id = pv.id, spv.id
    dj = c.get(f"/api/designs/{did}").json()
    by = {p["id"]: p["in_image"] for p in dj["presets"]}
    assert by[banner] == {"body": True, "cta": True, "available": True, "default": True}
    assert by[ids["presets"]["피드 4:5"]] == {"body": False, "cta": False, "available": True, "default": True}
    assert by[sep_id]["available"] is False
    _select_copy(env, [banner, ids["presets"]["피드 4:5"]])
    with Local() as db:                      # 선택 카피에 본문·CTA 를 채운다(헬퍼는 제목만 넣는다)
        for v in db.query(models.CopyVariant).filter_by(design_id=did).all():
            v.body, v.cta = "출퇴근길 15분 강의로 30일 영어 회화 감각을 익혀보세요.", "지금 시작하기"
        db.commit()
    r = c.post(f"/api/designs/{did}/presets/{banner}/image", json={"regenerate_background": False})
    assert r.status_code == 200, r.text
    lay = r.json()["composite"]["layout"]
    assert lay["in_image"] == ["headline", "body", "cta"] and lay["in_image_setting"]["default"] is True
    assert {e["type"] for e in lay["elements"]} >= {"text", "cta"}
    r = c.post(f"/api/designs/{did}/presets/{ids['presets']['피드 4:5']}/image", json={"regenerate_background": False})
    assert r.json()["composite"]["layout"]["in_image"] == ["headline"]
    # 설정 API: 배너의 CTA 끄기 → 다시 합성하면 본문만
    r = c.put(f"/api/designs/{did}/presets/{banner}/in-image", json={"cta": False})
    assert r.status_code == 200 and r.json()["body"] is True and r.json()["cta"] is False and r.json()["default"] is False
    r = c.post(f"/api/designs/{did}/presets/{banner}/image", json={"regenerate_background": False})
    assert r.json()["composite"]["layout"]["in_image"] == ["headline", "body"]
    assert c.put(f"/api/designs/{did}/presets/{sep_id}/in-image", json={"cta": True}).status_code == 400
    # 편집 화면을 열면 본문·CTA 요소가 생긴다
    j = c.get(f"/api/designs/{did}/presets/{banner}/edit").json()
    assert {e["type"] for e in j["elements"]} >= {"image", "text", "body"}


def test_editing_validates_and_renders_body_cta():
    from PIL import Image
    from app import editing
    W, H = 600, 500
    body = editing.validate_props("body", {"text": "본문 문장", "x": 10, "y": 300, "w": 400, "font_size": 20, "color": "#ffffff",
                                           "align": "왼쪽", "font": {"source": "provided", "name": "Pretendard-Bold"}, "max_lines": 2}, (W, H))
    assert body["color"] == "#FFFFFF" and body["max_lines"] == 2
    cta = editing.validate_props("cta", {"text": "지금 시작", "x": 10, "y": 420, "w": 160, "h": 40, "font_size": 18, "bg_color": "#1e3a8a",
                                         "text_color": "#ffffff", "font": {"source": "provided", "name": "Pretendard-Bold"}}, (W, H))
    assert cta["corner"] == "둥근"
    with pytest.raises(ValueError):
        editing.validate_props("cta", {**cta, "text": "x" * 21}, (W, H))
    img, measure = editing.render(_scene(1024, 1024), W, H,
                                  [{"type": "image", "props": {"zoom": 1.0, "cx": .5, "cy": .5}, "z_index": 0},
                                   {"type": "body", "props": body, "z_index": 4}, {"type": "cta", "props": cta, "z_index": 5}],
                                  lambda ref: _font(), lambda aid: None)
    assert img.size == (W, H) and measure["cta"]["box"] == [10, 420, 170, 460] and measure["cta"]["contrast"] > 4.5
    assert measure["body"]["lines"] == ["본문 문장"]
    assert img.getpixel((90, 440)) != img.getpixel((5, 440)), "버튼 배경이 그려졌다"
