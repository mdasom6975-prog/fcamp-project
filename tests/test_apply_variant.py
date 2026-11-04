"""시안(A/B/C)을 바꾸면 이미 만든 합성본에도 새 문구가 반영된다.

- 편집 화면에서 저장한 지면: 위치·크기는 그대로, 문구만 바뀐다. 직접 고친 문구는 overwrite_custom 일 때만 덮어쓴다.
- 편집하지 않은 지면: 배경을 유지한 채 새 문구로 다시 합성한다(이미지 생성 호출 없음).
외부 API 는 호출하지 않는다. 임시 Postgres DB 와 임시 storage 를 쓴다."""
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

W = H = 1080
A = {"headline": "오늘의 한정 혜택", "body": "지금 가입하면 첫 달이 무료입니다", "cta": "지금 보기"}
B = {"headline": "이번 주말 특가", "body": "주말에만 만나는 한정 굿즈", "cta": "구경하기"}


@pytest.fixture
def env(tmp_path, pg_engine):
    from app import models
    from app.db import Base
    from app.main import app
    from app.routers_assets import get_db

    engine = pg_engine
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)
    store = tmp_path / "storage"
    store.mkdir(exist_ok=True)

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
                  {"type": "text", "role": "제목", "text": A["headline"], "box": [60, 300, 1020, 420], "font_size": 90, "color": "#FFFFFF"},
                  {"type": "body", "role": "본문", "text": A["body"], "lines": [A["body"]], "box": [60, 450, 1020, 500], "font_size": 44, "color": "#FFFFFF"},
                  {"type": "cta", "role": "CTA", "text": A["cta"], "box": [60, 900, 360, 980], "font_size": 40,
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
        v = {}
        for no, c in ((1, A), (2, B)):
            v[no] = models.CopyVariant(design_id=d.id, preset_version_id=pv.id, batch_no=1, variant_no=no, selected=(no == 1),
                                       limits={}, violations=[], attempts=1, **c)
            db.add(v[no])
        db.flush()
        db.add(models.GeneratedImage(design_id=d.id, preset_version_id=pv.id, kind="background", file_path=str(bg_path), width=W, height=H))
        db.add(models.GeneratedImage(design_id=d.id, preset_version_id=pv.id, kind="composite", file_path=str(comp_path), width=W, height=H,
                                     layout=layout, copy_variant_id=v[1].id))
        db.commit()
        ids = {"design": d.id, "preset": pv.id, "v1": v[1].id, "v2": v[2].id}
    try:
        yield {"client": TestClient(app), "ids": ids, "Local": Local, "d": f"/api/designs/{ids['design']}",
               "edit": f"/api/designs/{ids['design']}/presets/{ids['preset']}/edit"}
    finally:
        app.dependency_overrides.clear()


def _els(env):
    return {e["type"]: e for e in env["client"].get(env["edit"]).json()["elements"]}


def _save(env, **changes):
    """편집 화면에서 저장(화면과 같게 잠기지 않은 요소만 보낸다). changes = {type: {props 변경}}"""
    els = _els(env)
    payload = [{"id": e["id"], "props": {**e["props"], **changes.get(e["type"], {})}} for e in els.values() if not e["locked"]]
    r = env["client"].put(env["edit"], json={"elements": payload})
    assert r.status_code == 200, r.text
    return {e["type"]: e for e in r.json()["elements"]}


def _selected_no(env):
    from app import models
    with env["Local"]() as db:
        return db.scalar(select(models.CopyVariant.variant_no).where(models.CopyVariant.selected.is_(True)))


def _latest_comp(env):
    from app import models
    with env["Local"]() as db:
        g = db.scalars(select(models.GeneratedImage).where(models.GeneratedImage.kind == "composite")
                       .order_by(models.GeneratedImage.id.desc())).first()
        return {"copy_variant_id": g.copy_variant_id, "edited": bool((g.layout or {}).get("edited")), "layout": g.layout}


def test_edited_preset_keeps_position_and_swaps_text(env):
    c = env["client"]
    _save(env, cta={"x": 700, "y": 820})
    imp = c.get(f"{env['d']}/copy/select-impact", params={"variant_no": 2}).json()
    assert imp["recompose"] == ["정사각 피드"] and imp["custom"] == [], "문구를 직접 고치지 않았으니 확인할 것이 없다"
    r = c.post(f"{env['d']}/copy/apply-variant", json={"variant_no": 2}).json()
    assert r["recomposed"] == [env["ids"]["preset"]] and not r["skipped"] and not r["kept_custom"]
    els = _els(env)
    assert (els["text"]["props"]["text"], els["body"]["props"]["text"], els["cta"]["props"]["text"]) == (B["headline"], B["body"], B["cta"])
    assert (els["cta"]["props"]["x"], els["cta"]["props"]["y"]) == (700, 820), "옮긴 CTA 위치는 그대로"
    comp = _latest_comp(env)
    assert comp["edited"] and comp["copy_variant_id"] == env["ids"]["v2"]
    assert _selected_no(env) == 2


def test_custom_headline_needs_confirmation_and_is_kept_unless_overwritten(env):
    c = env["client"]
    _save(env, text={"text": "내가 직접 쓴 제목"})
    imp = c.get(f"{env['d']}/copy/select-impact", params={"variant_no": 2}).json()
    assert [x["fields"] for x in imp["custom"]] == [["제목"]]
    keep = c.post(f"{env['d']}/copy/apply-variant", json={"variant_no": 2}).json()
    assert keep["kept_custom"] == [{"preset_id": env["ids"]["preset"], "name": "정사각 피드", "field": "제목"}]
    els = _els(env)
    assert els["text"]["props"]["text"] == "내가 직접 쓴 제목" and els["body"]["props"]["text"] == B["body"], "직접 고친 제목만 남기고 나머지는 갱신"
    over = c.post(f"{env['d']}/copy/apply-variant", json={"variant_no": 2, "overwrite_custom": True}).json()
    assert not over["kept_custom"]
    assert _els(env)["text"]["props"]["text"] == B["headline"], "덮어쓰기를 고르면 새 시안 문구로 바뀐다"
    assert not _els(env)["text"]["props"].get("text_override"), "덮어쓴 뒤에는 직접 고친 표시를 지운다"


def test_unsaved_editor_open_and_plain_preset_recompose_from_new_variant(env):
    c = env["client"]
    _els(env)       # 편집 화면을 열기만 했다(저장 안 함): 합성본은 자동 합성 상태
    r = c.post(f"{env['d']}/copy/apply-variant", json={"variant_no": 2})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["recomposed"] == [env["ids"]["preset"]] and not j["skipped"], j
    comp = _latest_comp(env)
    assert comp["copy_variant_id"] == env["ids"]["v2"] and not comp["edited"]
    text = next(e for e in comp["layout"]["elements"] if e["type"] == "text")
    assert text["text"] == B["headline"]


def test_unknown_variant_is_404_and_changes_nothing(env):
    r = env["client"].post(f"{env['d']}/copy/apply-variant", json={"variant_no": 9})
    assert r.status_code == 404 and _selected_no(env) == 1
