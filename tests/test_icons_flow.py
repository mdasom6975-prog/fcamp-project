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
    monkeypatch.setattr(routers_icons, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(routers_export, "EXPORT_DIR", tmp_path / "exports")

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


def _grid_image(k: int, n: int = 2, side: int = 1024):
    """n×n 격자에 k 개의 불투명 원을 칸 중앙에 둔 투명 이미지 (모델이 격자를 지킨 결과를 흉내)."""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (side, side), (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
    cw = side / n
    for i in range(k):
        r, c = divmod(i, n)
        cx, cy = (c + 0.5) * cw, (r + 0.5) * cw
        dr.ellipse((cx - cw * 0.3, cy - cw * 0.3, cx + cw * 0.3, cy + cw * 0.3), fill=(40, 90, 200, 255))
    return im


def test_grid_render_uses_fewer_calls(env, monkeypatch):
    """ICON_GRID=2: 10개를 2×2 격자 3장(4+4+2)으로 렌더해 잘라 저장한다. 각 아이콘은 투명·정상 크기로 맞춰진다."""
    from app import icons
    c = env["client"]
    did = _icon_set_design(env)
    monkeypatch.setattr(icons, "RENDER", "3d")
    monkeypatch.setattr(icons, "GRID", 2)
    prompts_seen: list[str] = []
    def fake_many(plist):
        prompts_seen.extend(plist)
        return [_grid_image(min(4, 10 - 4 * i)) for i, _ in enumerate(plist)]
    monkeypatch.setattr(icons, "render_many", fake_many)
    j = c.post(f"/api/designs/{did}/icons", json={}).json()
    assert j["made"] == 10 and j["complete"] and len(prompts_seen) == 3, "10개 → 격자 3장"
    assert "2×2 격자" in prompts_seen[0] and '1. "후보1"' in prompts_seen[0] and '4. "후보4"' in prompts_seen[0]
    assert '2. "후보10"' in prompts_seen[2] and "3." not in prompts_seen[2].split("[배치]")[0]
    assert all(not a["violations"] for a in j["icons"]), "칸 모서리가 비어 투명 배경으로 판정"
    assert all(abs(a["style"]["bbox"][2] - a["style"]["bbox"][0] - 20) < 0.6 for a in j["icons"]), "live area 에 맞춰짐"


def test_split_grid_cells_in_order():
    from app import icons
    im = _grid_image(4)
    cells = icons.split_grid(im, 2)
    assert len(cells) == 4 and all(cell.size == (492, 492) for cell in cells), "칸 512px 에서 경계 2%(10px)씩 뺀 크기"
    assert all(cell.getchannel("A").getbbox() for cell in cells)
