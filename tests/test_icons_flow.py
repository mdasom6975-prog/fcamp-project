"""아이콘 세트가 광고 이미지 캠페인의 검수·내보내기에 들어가는지, 부족분 채우기(icons/fill)가 동작하는지 검사.
외부 API 는 호출하지 않는다 (기능명 제안·렌더는 가짜 함수로 바꾼다). 임시 sqlite DB 와 임시 storage 를 쓴다."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _blob() -> Image.Image:
    im = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((300, 200, 700, 900), fill=(76, 105, 254, 255))
    return im


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app import models, routers_export, routers_icons
    from app.db import Base
    from app.main import app
    from app.routers_assets import get_db

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)
    # 저장소 폴더는 conftest 의 _isolated_state 가 임시 폴더로 바꿔 둔다

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[get_db] = override

    with Local() as db:
        brand = models.Brand(name="쿠챠")
        db.add(brand); db.flush()
        bv = models.BrandVersion(brand_id=brand.id, version_no=1, levels={"icon_style": "필수", "colors": "권장"},
                                 colors=[{"name": "주조", "hex": "#4C69FE", "role": "주조색"}],
                                 icon_style={"corner": "둥근"}, forbidden_phrases=["최저가"])
        db.add(bv); db.flush()
        brand.current_version_id = bv.id
        d = models.Design(brand_id=brand.id, brand_version_id=bv.id, kind="광고 이미지", preset_version_ids=[],
                          brief={"product_name": "랜덤박스"})
        db.add(d); db.commit()
        ids = {"brand": brand.id, "bv": bv.id, "design": d.id}

    def add_icons(n: int):
        """3d 방식 아이콘 n개를 세트(batch 1)에 넣는다."""
        with Local() as db:
            d = db.get(models.Design, ids["design"]); bv = db.get(models.BrandVersion, ids["bv"])
            style, palette = routers_icons._style(bv), routers_icons._palette(bv)
            for i in range(1, n + 1):
                routers_icons._save3d(db, d, bv, 1, i, f"기능{i}", f"오브젝트 {i}", f"prompt {i}", _blob(), style, palette,
                                      256, 256, bv.forbidden_phrases)
            db.commit()

    client = TestClient(app)
    try:
        yield {"client": client, "ids": ids, "Local": Local, "add_icons": add_icons, "models": models}
    finally:
        app.dependency_overrides.clear()


def test_review_without_icon_set_has_no_icon_group(env):
    c, did = env["client"], env["ids"]["design"]
    out = c.post(f"/api/designs/{did}/review").json()
    assert [g["preset_id"] for g in out["groups"]] == []
    assert c.get(f"/api/designs/{did}/review").json()["groups"] == []


def test_review_includes_icon_set_of_ad_campaign(env):
    c, did = env["client"], env["ids"]["design"]
    env["add_icons"](8)
    out = c.post(f"/api/designs/{did}/review").json()
    icon_groups = [g for g in out["groups"] if g["preset_id"] is None]
    assert len(icon_groups) == 1 and icon_groups[0]["name"] == "아이콘 세트"
    items = {i["item"]: i for i in icon_groups[0]["items"]}
    assert items["기능 수"]["status"] == "수정 필요" and items["기능 수"]["blocking"], "8/10 은 차단"
    assert items["투명 배경"]["status"] == "통과"
    assert out["export_ready"] is False
    assert any(b["preset"] == "아이콘 세트" and b["item"] == "기능 수" for b in out["blockers"])
    # GET 도 같은 그룹을 돌려준다
    assert [g["preset_id"] for g in c.get(f"/api/designs/{did}/review").json()["groups"]] == [None]


def test_export_draft_includes_icon_files(env):
    c, did = env["client"], env["ids"]["design"]
    env["add_icons"](2)
    c.post(f"/api/designs/{did}/review")
    r = c.post(f"/api/designs/{did}/export", json={"draft": True})
    assert r.status_code == 200, r.text
    e = r.json()
    names = [f["path"] if isinstance(f, dict) else f for f in e["files"]]
    joined = " ".join(map(str, names))
    assert "icons/png/01_" in joined and "icons/src/01_" in joined and "icons/png/02_" in joined
    assert "icons/svg" not in joined, "3d 방식은 SVG 가 없다"
    z = zipfile.ZipFile(io.BytesIO(c.get(e["download_url"]).content))
    manifest = json.loads(z.read("manifest.json"))
    icon_entry = next(p for p in manifest["presets"] if p["name"] == "아이콘 세트")
    assert icon_entry["count"] == 2 and icon_entry["render"] == "3d" and icon_entry["ready"] is False
    assert "검수결과.csv" in z.namelist()


def test_export_without_icons_or_presets_is_rejected(env):
    c, did = env["client"], env["ids"]["design"]
    r = c.post(f"/api/designs/{did}/export", json={"draft": True})
    assert r.status_code == 409


def test_fill_adds_missing_icons_in_same_batch(env, monkeypatch):
    from app import icons
    c, did = env["client"], env["ids"]["design"]
    env["add_icons"](8)
    asked = {}

    def fake_more(brand_name, brief, forbidden, existing, need):
        asked.update(existing=list(existing), need=need)
        # 기존 이름과 겹치는 제안은 걸러져야 한다
        return [{"name": "기능1", "concept": "중복"}, {"name": "간편주문", "concept": "주문 버튼"}, {"name": "기대감", "concept": "별"}]

    monkeypatch.setattr(icons, "propose3d_more", fake_more)
    monkeypatch.setattr(icons, "render_many", lambda plist: [_blob() for _ in plist])
    monkeypatch.setattr(icons, "RENDER", "3d")
    r = c.post(f"/api/designs/{did}/icons/fill")
    assert r.status_code == 200, r.text
    j = r.json()
    assert asked == {"existing": [f"기능{i}" for i in range(1, 9)], "need": 2}
    assert j["made"] == 2 and j["complete"] is True and len(j["icons"]) == 10
    tail = [(a["position"], a["name"], a["batch_no"]) for a in j["icons"][-2:]]
    assert tail == [(9, "간편주문", 1), (10, "기대감", 1)]
    # 채운 뒤에는 세트가 가득 차 있으므로 다시 채우기는 409
    assert c.post(f"/api/designs/{did}/icons/fill").status_code == 409
    # 아이콘 세트 검수 항목만 무효화되었고, 재검수하면 기능 수가 통과
    out = c.post(f"/api/designs/{did}/review").json()
    items = {i["item"]: i for g in out["groups"] if g["preset_id"] is None for i in g["items"]}
    assert items["기능 수"]["status"] == "통과"


def test_fill_without_set_is_rejected(env):
    c, did = env["client"], env["ids"]["design"]
    assert c.post(f"/api/designs/{did}/icons/fill").status_code == 409


def test_fill_invalidates_only_icon_review_items(env, monkeypatch):
    from app import icons
    from sqlalchemy import select
    c, did, Local, models = env["client"], env["ids"]["design"], env["Local"], env["models"]
    env["add_icons"](9)
    c.post(f"/api/designs/{did}/review")
    with Local() as db:   # 지면 검수 결과가 있다고 가정한 행 (검수 실행 뒤에 넣어야 검수 자체의 무효화를 피한다)
        db.add(models.ReviewResult(design_id=did, preset_version_id=12345, category="규격", item="가짜", status="통과",
                                   level=None, message="", blocking=False, needs_human=False))
        db.commit()
    monkeypatch.setattr(icons, "propose3d_more", lambda *a, **k: [{"name": "새기능", "concept": "x"}])
    monkeypatch.setattr(icons, "render_many", lambda plist: [_blob() for _ in plist])
    assert c.post(f"/api/designs/{did}/icons/fill").status_code == 200
    with Local() as db:
        rows = db.scalars(select(models.ReviewResult).where(models.ReviewResult.design_id == did)).all()
        assert all(not r.valid for r in rows if r.preset_version_id is None)
        assert all(r.valid for r in rows if r.preset_version_id == 12345), "지면 결과는 유지"


def test_icon_set_uses_candidates_from_copy_call_without_proposal(env, monkeypatch):
    """브리프에 기능명 후보(카피 호출에서 받은 것)가 세트 크기 이상 있으면 제안 호출 없이 그대로 렌더한다.
    금지 표현이 든 이름은 빼고, 여유분(15개)에서 10개를 쓴다."""
    from app import icons, models
    c, did, Local = env["client"], env["ids"]["design"], env["Local"]
    cands = [{"name": "최저가보장", "concept": "x"}] + [{"name": f"후보{i}", "concept": f"오브젝트 {i}"} for i in range(1, 15)]
    with Local() as db:
        d = db.get(models.Design, did); d.kind = "아이콘 세트"; d.brief = {**d.brief, "icon_candidates": cands}; db.commit()   # 아이콘 세트 캠페인: 합성본 없이 생성 가능
    monkeypatch.setattr(icons, "propose3d", lambda *a, **k: (_ for _ in ()).throw(AssertionError("제안 호출이 있으면 안 된다")))
    rendered: list[str] = []
    monkeypatch.setattr(icons, "render_many", lambda plist: (rendered.extend(plist), [_blob() for _ in plist])[1])
    monkeypatch.setattr(icons, "RENDER", "3d")
    out = c.post(f"/api/designs/{did}/icons").json()
    assert out["made"] == 10 and len(rendered) == 10
    names = [a["name"] for a in out["icons"]]
    assert names == [f"후보{i}" for i in range(1, 11)], "금지 표현 이름은 빠지고 순서대로 10개"


def test_icon_set_falls_back_to_proposal_when_candidates_short(env, monkeypatch):
    from app import icons, models
    c, did, Local = env["client"], env["ids"]["design"], env["Local"]
    with Local() as db:
        d = db.get(models.Design, did); d.kind = "아이콘 세트"; d.brief = {**d.brief, "icon_candidates": [{"name": "하나", "concept": "x"}]}; db.commit()
    called: list[int] = []
    monkeypatch.setattr(icons, "propose3d", lambda *a, **k: (called.append(1), [{"name": f"제안{i}", "concept": "o"} for i in range(15)])[1])
    monkeypatch.setattr(icons, "render_many", lambda plist: [_blob() for _ in plist])
    monkeypatch.setattr(icons, "RENDER", "3d")
    out = c.post(f"/api/designs/{did}/icons").json()
    assert called == [1] and out["made"] == 10 and out["icons"][0]["name"] == "제안0"


def _icon_set_design(env):
    """아이콘 세트 캠페인(합성본 없이 생성 가능)으로 바꾸고 후보 15개를 둔다."""
    from app import models
    did, Local = env["ids"]["design"], env["Local"]
    cands = [{"name": f"후보{i}", "concept": f"오브젝트 {i}"} for i in range(1, 16)]
    with Local() as db:
        d = db.get(models.Design, did); d.kind = "아이콘 세트"; d.brief = {**d.brief, "icon_candidates": cands}; db.commit()
    return did


def test_candidates_endpoint_lists_without_call_and_marks_rendered(env, monkeypatch):
    from app import icons
    c = env["client"]
    did = _icon_set_design(env)
    monkeypatch.setattr(icons, "propose3d", lambda *a, **k: (_ for _ in ()).throw(AssertionError("후보가 있으면 제안 호출 없음")))
    j = c.get(f"/api/designs/{did}/icons/candidates").json()
    assert j["source"] == "copy" and len(j["candidates"]) == 15 and not any(x["rendered"] for x in j["candidates"])
    env["add_icons"](2)       # 기능1, 기능2 (후보 밖 이름)
    j = c.get(f"/api/designs/{did}/icons/candidates").json()
    assert j["rendered"] == 2 and not any(x["rendered"] for x in j["candidates"])


def test_candidates_endpoint_proposes_once_when_missing(env, monkeypatch):
    from app import icons, models
    c, did, Local = env["client"], env["ids"]["design"], env["Local"]
    with Local() as db:
        d = db.get(models.Design, did); d.kind = "아이콘 세트"; db.commit()
    calls: list[int] = []
    monkeypatch.setattr(icons, "RENDER", "3d")
    monkeypatch.setattr(icons, "propose3d", lambda *a, **k: (calls.append(1), [{"name": f"제안{i}", "concept": "o"} for i in range(15)])[1])
    j = c.get(f"/api/designs/{did}/icons/candidates").json()
    assert j["source"] == "proposal" and len(j["candidates"]) == 15 and calls == [1]
    c.get(f"/api/designs/{did}/icons/candidates")
    assert calls == [1], "두 번째부터는 브리프에 저장된 후보를 쓴다"


def test_render_only_picked_names_then_append_more(env, monkeypatch):
    """고른 이름만 렌더(이미지 N장)하고, 세트가 생긴 뒤에도 고른 이름을 이어 붙인다. 후보 밖 이름·초과 선택은 거절."""
    from app import icons
    c = env["client"]
    did = _icon_set_design(env)
    rendered: list[str] = []
    monkeypatch.setattr(icons, "RENDER", "3d")
    monkeypatch.setattr(icons, "render_many", lambda plist: (rendered.extend(plist), [_blob() for _ in plist])[1])
    monkeypatch.setattr(icons, "propose3d", lambda *a, **k: (_ for _ in ()).throw(AssertionError("제안 호출 없음")))
    j = c.post(f"/api/designs/{did}/icons", json={"names": ["후보3", "후보1", "후보3"]}).json()
    assert j["made"] == 2 and len(rendered) == 2 and [a["name"] for a in j["icons"]] == ["후보3", "후보1"] and not j["complete"]
    assert [a["position"] for a in j["icons"]] == [1, 2]
    assert c.post(f"/api/designs/{did}/icons", json={"names": ["없는이름"]}).status_code == 400
    assert c.post(f"/api/designs/{did}/icons", json={"names": ["후보1"]}).status_code == 409, "이미 있는 이름만 고르면 409"
    assert c.post(f"/api/designs/{did}/icons", json={}).status_code == 409, "세트가 있으면 자동 생성은 막힌다"
    j = c.post(f"/api/designs/{did}/icons", json={"names": [f"후보{i}" for i in range(4, 12)]}).json()
    assert j["made"] == 8 and j["complete"] and len(j["icons"]) == 10 and j["icons"][-1]["position"] == 10
    r = c.post(f"/api/designs/{did}/icons", json={"names": ["후보12"]})
    assert r.status_code == 400 and "넘칩니다" in r.json()["detail"]
    cj = c.get(f"/api/designs/{did}/icons/candidates").json()
    assert cj["rendered"] == 10 and sum(x["rendered"] for x in cj["candidates"]) == 10


def _grid_image(k: int, cols: int = 2, rows: int = 2, size=(1024, 1024), skip=(), shift=None, center_last=False, crumbs=False):
    """cols×rows 격자에 k 개의 불투명 원을 둔 투명 이미지 (모델 결과 흉내).
    skip: 비워 둘 칸 번호(1부터). shift(row) → 그 줄의 세로 이동(px, 칸 경계를 넘기는 흉내). center_last: 마지막 줄을 가운데 정렬.
    crumbs: 원 위에 떨어진 작은 조각(김·반짝임)을 하나씩 더 그린다."""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", size, (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
    cw, ch = size[0] / cols, size[1] / rows
    rad = min(cw, ch) * 0.3
    for i in range(k):
        if i + 1 in skip:
            continue
        r, c = divmod(i, cols)
        cx, cy = (c + 0.5) * cw, (r + 0.5) * ch
        if center_last and r == rows - 1:
            cx += (cols - (k - r * cols)) / 2 * cw
        if shift:
            cy += shift(r)
        dr.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=(40, 90, 200, 255))
        if crumbs:
            dr.ellipse((cx - rad * 0.08, cy - rad * 1.45, cx + rad * 0.08, cy - rad * 1.25), fill=(40, 90, 200, 255))
    return im


def _grid_env(monkeypatch, cols, rows, size):
    from app import icons
    monkeypatch.setattr(icons, "RENDER", "3d")
    monkeypatch.setattr(icons, "GRID_COLS", cols); monkeypatch.setattr(icons, "GRID_ROWS", rows)
    monkeypatch.setattr(icons, "GRID_CELLS", cols * rows); monkeypatch.setattr(icons, "GRID_SIZE", size)


def test_grid_default_renders_ten_icons_in_one_call(env, monkeypatch):
    """기본 ICON_GRID=4x3: 가로 캔버스 1장에 12칸을 모두 채워(10개 + 남은 후보 2개) 그리고 앞 10개만 저장한다 → 호출 1회."""
    from app import icons
    c = env["client"]
    did = _icon_set_design(env)
    _grid_env(monkeypatch, 4, 3, "1536x1024")
    seen: list[tuple[str, object]] = []
    def fake_many(plist, fn=None):
        seen.extend((p, fn) for p in plist)
        return [_grid_image(12, 4, 3, (1536, 1024), crumbs=True) for _ in plist]
    monkeypatch.setattr(icons, "render_many", fake_many)
    j = c.post(f"/api/designs/{did}/icons", json={}).json()
    assert j["made"] == 10 and j["complete"] and len(seen) == 1, "10개 → 격자 1장"
    p, fn = seen[0]
    head = p.split("[배치]")[0]
    assert fn is icons.render_grid and "가로 4칸 × 세로 3줄" in p and '10. "후보10"' in head
    assert '11. "후보11"' in head and '12. "후보12"' in head and "13." not in head, "빈 칸은 남은 후보로 채운다"
    assert "12개의 칸에 오브젝트를 정확히 하나씩" in p
    assert all(not a["violations"] for a in j["icons"])
    assert [a["name"] for a in j["icons"]] == [f"후보{i}" for i in range(1, 11)], "채움 칸(11·12)은 저장하지 않는다"
    assert all(abs(max(b[2] - b[0], b[3] - b[1]) - 20) < 0.6 for b in (a["style"]["bbox"] for a in j["icons"])), "긴 변이 live area 에 맞춰짐"
    assert all(a["style"]["origin"] == {"grid": "4x3", "cell": a["position"], "method": "order", "blobs": 12, "requested": 12, "mismatch": False}
               for a in j["icons"])
    s = c.get("/api/icon-settings").json()
    assert s["grid"] == "4x3" and s["grid_cells"] == 12 and s["grid_size"] == "1536x1024"


def test_grid_retries_failed_cells_with_single_render(env, monkeypatch):
    """모델이 한 칸을 비우면(덩어리 11개) 그 아이콘만 1장 렌더로 다시 그린다. 수가 안 맞은 격자의 나머지는 '짝 어긋남' 경고."""
    from app import icons
    c = env["client"]
    did = _icon_set_design(env)
    _grid_env(monkeypatch, 4, 3, "1536x1024")
    calls: list[tuple[list[str], object]] = []
    def fake_many(plist, fn=None):
        calls.append((list(plist), fn))
        if fn is icons.render_grid:
            return [_grid_image(12, 4, 3, (1536, 1024), skip=(5,)) for _ in plist]
        return [_grid_image(1, 1, 1, (1024, 1024)) for _ in plist]
    monkeypatch.setattr(icons, "render_many", fake_many)
    j = c.post(f"/api/designs/{did}/icons", json={}).json()
    assert j["made"] == 10 and j["complete"] and len(calls) == 2, "격자 1장 + 실패 칸 1장"
    assert calls[1][1] is None and len(calls[1][0]) == 1 and '"후보5" 기능을 상징하는' in calls[1][0][0]
    by = {a["name"]: a for a in j["icons"]}
    assert by["후보5"]["style"]["origin"]["retried"] is True and not by["후보5"]["violations"]
    assert all(by[f"후보{i}"]["style"]["origin"]["method"] == "cell" for i in (1, 2, 3, 4, 6))
    assert all(any(v["type"] == "grid" for v in by[f"후보{i}"]["violations"]) for i in (1, 6, 10)), "11/12 → 짝 어긋남 경고"


def test_grid_quota_exhausted_is_not_retried(env, monkeypatch):
    from app import icons, imagegen
    c = env["client"]
    did = _icon_set_design(env)
    _grid_env(monkeypatch, 4, 3, "1536x1024")
    calls: list[object] = []
    def fake_many(plist, fn=None):
        calls.append(fn)
        return [imagegen.QuotaExceeded("오늘 설정한 이미지 생성 한도(150회)를 모두 사용했습니다") for _ in plist]
    monkeypatch.setattr(icons, "render_many", fake_many)
    r = c.post(f"/api/designs/{did}/icons", json={})
    assert r.status_code == 429 and len(calls) == 1, "한도 소진이면 1장 렌더로 재시도하지 않는다"


def test_grid_2x2_uses_three_calls(env, monkeypatch):
    """ICON_GRID=2: 10개를 2×2 격자 3장(4+4+2)으로 렌더한다. 셋째 장의 빈 두 칸은 남은 후보로 채운다."""
    from app import icons
    c = env["client"]
    did = _icon_set_design(env)
    _grid_env(monkeypatch, 2, 2, "1024x1024")
    prompts_seen: list[str] = []
    def fake_many(plist, fn=None):
        prompts_seen.extend(plist)
        return [_grid_image(4) for _ in plist]
    monkeypatch.setattr(icons, "render_many", fake_many)
    j = c.post(f"/api/designs/{did}/icons", json={}).json()
    assert j["made"] == 10 and len(prompts_seen) == 3
    assert "가로 2칸 × 세로 2줄" in prompts_seen[0] and '2. "후보10"' in prompts_seen[2] and '4. "후보12"' in prompts_seen[2]
    assert all(not a["violations"] for a in j["icons"])


def test_grid_fillers_fall_back_to_generic_objects(env, monkeypatch):
    """남은 후보가 없으면 채움 오브젝트(GRID_FILLERS)로 칸을 메운다. 저장되지는 않는다."""
    from app import icons, models
    c, Local = env["client"], env["Local"]
    did = _icon_set_design(env)
    with Local() as db:
        d = db.get(models.Design, did); d.brief = {**d.brief, "icon_candidates": d.brief["icon_candidates"][:10]}; db.commit()
    _grid_env(monkeypatch, 4, 3, "1536x1024")
    prompts_seen: list[str] = []
    monkeypatch.setattr(icons, "render_many", lambda plist, fn=None: (prompts_seen.extend(plist), [_grid_image(12, 4, 3, (1536, 1024)) for _ in plist])[1])
    j = c.post(f"/api/designs/{did}/icons", json={}).json()
    head = prompts_seen[0].split("[배치]")[0]
    assert j["made"] == 10 and '11. "채움 구"' in head and '12. "채움 정육면체"' in head
    assert not any(a["name"].startswith("채움") for a in j["icons"])


def test_grid_parse():
    from app import icons
    assert icons._parse_grid("4x3") == (4, 3) and icons._parse_grid("2") == (2, 2) and icons._parse_grid("0") == (0, 0)
    assert icons._parse_grid("1") == (0, 0) and icons._parse_grid("") == (0, 0)


def test_split_blobs_aligned_grid_in_reading_order():
    """격자를 지킨 12개: 덩어리 수가 맞으니 읽기 순서로, 조각은 여백을 두고 잘려 가장자리에 닿지 않는다. 떨어진 작은 조각(김)은 합친다."""
    from app import icons
    pieces, info = icons.split_blobs(_grid_image(12, 4, 3, (1536, 1024), crumbs=True), 4, 3, 12)
    assert info == {"method": "order", "blobs": 12, "failed": [], "mismatch": False}
    assert all(p is not None for p in pieces)
    for p in pieces:
        bb = p.getchannel("A").getbbox()
        assert bb[0] > 0 and bb[1] > 0 and bb[2] < p.width and bb[3] < p.height, "조각 안에서 오브젝트가 가장자리에 닿지 않는다"
        assert p.height > p.width, "김 조각까지 한 오브젝트로 포함"
    # 읽기 순서: 조각 k 는 칸 k 의 중심 근처에서 왔다 (칸 2 조각이 칸 1 보다 오른쪽) — 왼쪽 위 칸이 먼저
    assert pieces[0].size == pieces[5].size


def test_split_blobs_survives_rows_crossing_cell_borders():
    """줄을 어긋나게 그려 1행 오브젝트가 칸 경계를 넘어도(고정 등분이면 잘림) 덩어리로 자르니 온전하다."""
    from app import icons
    im = _grid_image(12, 4, 3, (1536, 1024), shift=lambda r: {0: 60, 1: -30}.get(r, 0))
    pieces, info = icons.split_blobs(im, 4, 3, 12)
    assert info["method"] == "order" and info["failed"] == []
    rad = int(min(1536 / 4, 1024 / 3) * 0.3)
    assert all(abs(p.getchannel("A").getbbox()[3] - p.getchannel("A").getbbox()[1] - 2 * rad) <= 2 for p in pieces), "원이 통째로"


def test_split_blobs_centered_last_row_matches_by_order():
    """마지막 줄 2개가 가운데 정렬돼도 수가 맞으면 읽기 순서로 짝이 맞는다(10개 요청·10개 그림)."""
    from app import icons
    pieces, info = icons.split_blobs(_grid_image(10, 4, 3, (1536, 1024), center_last=True), 4, 3, 10)
    assert info["method"] == "order" and info["failed"] == [] and all(pieces)


def test_split_blobs_mismatch_falls_back_to_cells_and_marks_failures():
    """수가 안 맞으면(12칸 요청·10개, 마지막 줄 가운데 정렬) 중심이 든 칸으로 짝 → 9·12 번 칸은 실패, 캔버스에 잘린 덩어리도 실패."""
    from app import icons
    from PIL import ImageDraw
    im = _grid_image(10, 4, 3, (1536, 1024), center_last=True)
    pieces, info = icons.split_blobs(im, 4, 3, 12)
    assert info["method"] == "cell" and info["mismatch"] and info["failed"] == [9, 12] and info["blobs"] == 10
    im2 = _grid_image(12, 4, 3, (1536, 1024))
    ImageDraw.Draw(im2).ellipse((-120, 760, 30, 1000), fill=(40, 90, 200, 255))   # 9번 칸 왼쪽에 큰 덩어리 하나 더(캔버스 밖으로 나감)
    pieces, info = icons.split_blobs(im2, 4, 3, 12)
    assert info["method"] == "cell" and info["blobs"] == 13 and 9 in info["failed"], "덩어리 둘인 칸은 실패"
    pieces, info = icons.split_blobs(_grid_image(12, 4, 3, (1536, 1024), skip=(9,)).crop((0, 0, 1536, 900)).resize((1536, 1024)), 4, 3, 12)
    assert 9 in info["failed"] and all(k in info["failed"] for k in (10, 11, 12)), "아래가 잘린 3행 오브젝트는 실패"


def test_split_blobs_merged_neighbours_fail():
    """이웃과 붙어 칸보다 커진 덩어리는 쓰지 않는다(두 칸 모두 실패 → 1장 렌더)."""
    from app import icons
    from PIL import ImageDraw
    im = _grid_image(12, 4, 3, (1536, 1024))
    ImageDraw.Draw(im).rectangle((384 * 0.5, 341 * 0.4, 384 * 1.5, 341 * 0.6), fill=(40, 90, 200, 255))   # 1·2번 칸을 잇는 막대
    pieces, info = icons.split_blobs(im, 4, 3, 12)
    assert info["method"] == "cell" and info["failed"] == [1, 2]


def test_icon_reset_deletes_set_and_files_but_keeps_candidates(env):
    from app import models, storage
    c, did, Local = env["client"], env["ids"]["design"], env["Local"]
    with Local() as db:
        d = db.get(models.Design, did); d.brief = {**d.brief, "icon_candidates": [{"name": "후보1", "concept": "x"}]}; db.commit()
    env["add_icons"](3)
    with Local() as db:
        files = [a.png_path for a in db.query(models.IconAsset).all()]
    assert len(files) == 3 and all(storage.exists(f) for f in files)
    r = c.post(f"/api/designs/{did}/icons/reset", json={})
    assert r.status_code == 200 and r.json() == {"icons_deleted": 3, "deleted_files": 6}, r.text
    assert c.get(f"/api/designs/{did}/icons").json() == [] and not any(storage.exists(f) for f in files)
    assert c.get(f"/api/designs/{did}").json()["brief"]["icon_candidates"] == [{"name": "후보1", "concept": "x"}]
    assert c.post(f"/api/designs/{did}/icons/reset", json={}).json()["icons_deleted"] == 0
