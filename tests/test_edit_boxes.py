"""이미지 편집: 본문·CTA 편집과 드래그앤드랍에 필요한 서버 응답 검사.

- 미리보기 응답이 끌어 옮길 요소의 상자(요소 id → 캔버스 px)와 안전영역 여백을 준다.
- 본문은 제목처럼 편집하고, CTA 는 위치를 옮길 수 있으며, 저장한 레이아웃에도 본문·CTA 가 남는다.
- 범위를 벗어난 위치는 거절한다(화면의 드래그는 같은 범위로 자른다).
외부 API 는 호출하지 않는다. 임시 sqlite DB 와 임시 storage 를 쓴다."""
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

W = H = 1080


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app import models, routers_edit
    from app.db import Base
    from app.main import app
    from app.routers_assets import get_db

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)
    store = tmp_path / "storage"
    store.mkdir()
    monkeypatch.setattr(routers_edit, "STORAGE_DIR", store)

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[get_db] = override

    bg_path, comp_path = store / "bg.png", store / "comp.png"
    Image.new("RGB", (W, H), (60, 80, 120)).save(bg_path)
    Image.new("RGB", (W, H), (60, 80, 120)).save(comp_path)
    layout = {"template": "정사각", "canvas": [W, H], "scrim": {"mode": "정사각", "color": "#000000", "opacity": 0.5},
              "elements": [
                  {"type": "logo", "box": [60, 60, 260, 110]},
                  {"type": "text", "role": "제목", "text": "오늘의 한정 혜택", "box": [60, 300, 1020, 420], "font_size": 90, "color": "#FFFFFF"},
                  {"type": "body", "role": "본문", "text": "지금 가입하면 첫 달이 무료입니다", "lines": ["지금 가입하면 첫 달이 무료입니다"],
                   "box": [60, 450, 1020, 500], "font_size": 44, "color": "#FFFFFF"},
                  {"type": "cta", "role": "CTA", "text": "지금 보기", "box": [60, 900, 360, 980], "font_size": 40,
                   "bg_color": "#4C69FE", "text_color": "#FFFFFF", "corner": "둥근"}]}
    with Local() as db:
        brand = models.Brand(name="쿠챠")
        db.add(brand); db.flush()
        bv = models.BrandVersion(brand_id=brand.id, version_no=1, levels={}, colors=[{"name": "주조", "hex": "#4C69FE", "role": "주조색"}])
        db.add(bv); db.flush()
        brand.current_version_id = bv.id
        plat = models.Platform(name="테스트플랫폼"); db.add(plat); db.flush()
        pl = models.Placement(platform_id=plat.id, ad_type="피드", name="정사각 피드", content_kind="완성형")
        db.add(pl); db.flush()
        pv = models.PresetVersion(placement_id=pl.id, production_width=W, production_height=H, ratio="1:1", status="확인됨",
                                  safe_area={"left_pct": 5, "right_pct": 5, "top_pct": 10, "bottom_pct": 10})
        db.add(pv); db.flush()
        d = models.Design(brand_id=brand.id, brand_version_id=bv.id, kind="광고 이미지", preset_version_ids=[pv.id],
                          brief={"product_name": "랜덤박스"})
        db.add(d); db.flush()
        db.add(models.GeneratedImage(design_id=d.id, preset_version_id=pv.id, kind="background", file_path=str(bg_path), width=W, height=H))
        db.add(models.GeneratedImage(design_id=d.id, preset_version_id=pv.id, kind="composite", file_path=str(comp_path), width=W, height=H, layout=layout))
        db.commit()
        ids = {"design": d.id, "preset": pv.id}
    try:
        yield {"client": TestClient(app), "ids": ids, "base": f"/api/designs/{ids['design']}/presets/{ids['preset']}/edit"}
    finally:
        app.dependency_overrides.clear()


def _els(env):
    return {e["type"]: e for e in env["client"].get(env["base"]).json()["elements"]}


def _payload(els, **changes):
    """잠기지 않은 요소의 값만 보낸다(화면과 같다). changes = {type: {props 변경}}"""
    return [{"id": e["id"], "props": {**e["props"], **changes.get(e["type"], {})}} for e in els.values() if not e["locked"]]


def test_edit_screen_has_body_and_cta_elements(env):
    els = _els(env)
    assert set(els) == {"image", "shape", "logo", "text", "body", "cta"}
    assert els["body"]["props"]["text"] == "지금 가입하면 첫 달이 무료입니다" and els["body"]["props"]["max_lines"] == 1
    assert (els["cta"]["props"]["x"], els["cta"]["props"]["y"], els["cta"]["props"]["w"], els["cta"]["props"]["h"]) == (60, 900, 300, 80)
    assert els["logo"]["locked"] is True and not els["body"]["locked"] and not els["cta"]["locked"]


def test_preview_returns_boxes_and_safe_margins_for_dragging(env):
    c, els = env["client"], _els(env)
    j = c.post(env["base"] + "/preview", json={"elements": _payload(els)}).json()
    boxes = j["boxes"]
    assert set(boxes) == {str(els[t]["id"]) for t in ("text", "body", "cta")}, "그려진 제목·본문·CTA 만(로고 자산이 없어 로고는 뺀다)"
    assert boxes[str(els["cta"]["id"])] == [60, 900, 360, 980], "CTA 상자는 x, y, x+w, y+h"
    tx0, ty0, tx1, _ = boxes[str(els["text"]["id"])]
    assert (tx0, ty0, tx1) == (60, 300, 1020), "제목 상자의 위치·너비는 props 그대로, 높이는 줄 수로 잰다"
    assert j["margins"] == [65, 108, 65, 108], "기본 여백(짧은 변의 6%=65)과 지면 안전영역(좌우 5%·상하 10%) 중 큰 값, 좌·상·우·하 순서"
    assert j["size"] == [W, H]


def test_moved_cta_and_resized_body_show_in_preview_boxes(env):
    c, els = env["client"], _els(env)
    ch = {"cta": {"x": 700, "y": 820}, "body": {"w": 500, "text": "문장이 길어서 두 줄이 되는 본문입니다. 너비를 줄이면 줄 바꿈이 늘어납니다", "max_lines": 3}}
    boxes = c.post(env["base"] + "/preview", json={"elements": _payload(els, **ch)}).json()["boxes"]
    assert boxes[str(els["cta"]["id"])] == [700, 820, 1000, 900], "CTA 는 옮긴 자리에 크기 그대로"
    b = boxes[str(els["body"]["id"])]
    assert b[0] == 60 and b[2] == 560, "본문 너비 500"
    narrow = c.post(env["base"] + "/preview", json={"elements": _payload(els, body={"w": 500, "max_lines": 3})}).json()["boxes"][str(els["body"]["id"])]
    assert b[3] - b[1] >= narrow[3] - narrow[1] > 0, "줄이 늘면 상자가 길어진다(화면의 상자 높이도 이 값을 따른다)"


def test_save_keeps_body_and_cta_in_edited_layout(env):
    c, els = env["client"], _els(env)
    ch = {"body": {"text": "수정한 본문 문구", "color": "#F5E6B8"}, "cta": {"x": 700, "y": 820}}
    r = c.put(env["base"], json={"elements": _payload(els, **ch)})
    assert r.status_code == 200, r.text
    lay = r.json()["composite"]["layout"]
    by = {e["type"]: e for e in lay["elements"]}
    assert lay["edited"] is True and set(by) == {"text", "body", "cta"}, "본문·CTA 가 저장 레이아웃에서 빠지지 않는다"
    assert by["body"]["text"] == "수정한 본문 문구" and by["body"]["color"] == "#F5E6B8" and by["body"]["lines"] == ["수정한 본문 문구"]
    assert by["cta"]["box"] == [700, 820, 1000, 900] and by["cta"]["text"] == "지금 보기"
    assert lay["in_image"] == ["headline", "body", "cta"]
    again = _els(env)       # 다시 열어도 저장한 값이 남아 있다
    assert again["body"]["props"]["text"] == "수정한 본문 문구" and (again["cta"]["props"]["x"], again["cta"]["props"]["y"]) == (700, 820)
    assert again["body"]["props"]["text_override"] is False, "본문 수정은 공통 제목 변경과 무관하다"


def test_out_of_range_position_is_rejected(env):
    c, els = env["client"], _els(env)
    for ch, word in (({"cta": {"x": 5 * W}}, "x"), ({"body": {"y": -5 * H}}, "y"), ({"body": {"w": 5}}, "w"), ({"body": {"max_lines": 9}}, "max_lines")):
        r = c.post(env["base"] + "/preview", json={"elements": _payload(els, **ch)})
        assert r.status_code == 400 and word in r.json()["detail"], (ch, r.text)


def test_locked_logo_cannot_be_moved(env):
    c, els = env["client"], _els(env)
    r = c.post(env["base"] + "/preview", json={"elements": [{"id": els["logo"]["id"], "props": {**els["logo"]["props"], "x": 300}}]})
    assert r.status_code == 400 and "잠긴" in r.json()["detail"]


def test_element_boxes_helper_skips_unrendered_elements():
    from app.routers_edit import element_boxes
    rows = [(1, "image"), (2, "shape"), (3, "logo"), (4, "text"), (5, "body"), (6, "cta")]
    measure = {"logo": {"missing": True}, "text": {"box": [1.0, 2.0, 30.0, 40.0]}, "cta": {"box": [5, 6, 7, 8]}}
    assert element_boxes(rows, measure) == {"4": [1, 2, 30, 40], "6": [5, 6, 7, 8]}
