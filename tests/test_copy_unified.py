"""카피를 캠페인당 1회 생성해 모든 지면이 같은 문장을 쓰는지(매체 간 통일), 시안 번호 선택이 모든 지면에 적용되는지 검사.
외부 API 는 호출하지 않는다 (generate_checked 를 가짜로 바꾼다). 임시 sqlite DB 를 쓴다."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

IG = [{"field": "기본 문구", "max_chars": 125, "count_method": "글자 수", "level": "권장"},
      {"field": "제목", "max_chars": 40, "count_method": "글자 수", "level": "권장"}]
KAKAO = [{"field": "타이틀", "max_chars": 25, "count_method": "글자 수", "level": "필수"},
         {"field": "홍보문구", "max_chars": 45, "count_method": "글자 수", "level": "필수"}]
PMAX = [{"field": "광고 제목", "max_chars": 30, "count_method": "전각 2자 계산", "level": "필수"},
        {"field": "설명", "max_chars": 90, "count_method": "전각 2자 계산", "level": "필수"}]
FB = [{"field": "기본 문구", "min_chars": 50, "max_chars": 150, "count_method": "글자 수", "level": "권장"}]
BIZ = [{"field": "배너 카피", "count_method": "px 길이", "level": "필수"}]


def test_merge_limits_takes_strictest_and_converts_fullwidth():
    from app import copywriter
    per = [("Instagram 피드", copywriter.limits_for(IG)), ("네이티브 2:1", copywriter.limits_for(KAKAO)),
           ("PMax 가로", copywriter.limits_for(PMAX)), ("Facebook 피드", copywriter.limits_for(FB)),
           ("비즈보드", copywriter.limits_for(BIZ))]
    m = copywriter.merge_limits(per)
    assert m["headline"]["max"] == 15 and "PMax 가로" in m["headline"]["field"] and m["headline"]["method"] == "글자 수"
    assert m["body"]["max"] == 45 and m["body"]["min"] == 50 and "네이티브 2:1" in m["body"]["field"]
    assert m["cta"] is None
    assert any(s["field"].startswith("비즈보드") for s in m["skipped"])


def test_merge_limits_empty():
    from app import copywriter
    assert copywriter.merge_limits([]) == {"headline": None, "body": None, "cta": None, "skipped": []}
    assert copywriter.merge_limits([("x", copywriter.limits_for([]))])["headline"] is None


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app import copywriter, models
    from app.db import Base
    from app.main import app
    from app.routers_assets import get_db

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)
    calls: list[dict] = []

    def fake_generate_checked(brand_name, brand, brief, placement, limits):
        calls.append({"placement": placement, "limits": limits})
        vs = [{"headline": "짧은 제목", "body": "본문 하나", "cta": "보기"},
              {"headline": "이 제목은 네이티브 타이틀 제한을 넘기도록 일부러 길게 씁니다", "body": "본문 둘", "cta": "보기"},
              {"headline": "세 번째", "body": "본문 셋", "cta": "보기"}]
        for v in vs:
            v["violations"] = copywriter.check(v, limits, brand)
        return vs, 1
    monkeypatch.setattr(copywriter, "generate_checked", fake_generate_checked)

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[get_db] = override

    with Local() as db:
        brand = models.Brand(name="쿠팡")
        db.add(brand); db.flush()
        bv = models.BrandVersion(brand_id=brand.id, version_no=1)
        db.add(bv); db.flush()
        brand.current_version_id = bv.id
        plat = models.Platform(name="테스트")
        db.add(plat); db.flush()
        ids = {"presets": {}}
        for name, w, h, tl in [("Instagram 피드", 1440, 1800, IG), ("네이티브 2:1", 1200, 600, KAKAO), ("PMax 가로", 1200, 628, PMAX)]:
            pl = models.Placement(platform_id=plat.id, name=name, ad_type="테스트", content_kind="완성형")
            db.add(pl); db.flush()
            pv = models.PresetVersion(placement_id=pl.id, production_width=w, production_height=h, status="확인됨",
                                      text_limits=tl)
            db.add(pv); db.flush()
            ids["presets"][name] = pv.id
        d = models.Design(brand_id=brand.id, brand_version_id=bv.id, kind="광고 이미지",
                          preset_version_ids=list(ids["presets"].values()),
                          brief={"product_name": "리빙 창고 대방출", "key_message": "특가"})
        db.add(d); db.commit()
        ids["design"] = d.id

    client = TestClient(app)
    try:
        yield {"client": client, "ids": ids, "calls": calls, "Local": Local}
    finally:
        app.dependency_overrides.clear()


def test_create_copy_once_with_merged_limits_and_same_text_everywhere(env):
    c, ids, calls = env["client"], env["ids"], env["calls"]
    out = c.post(f"/api/designs/{ids['design']}/copy").json()
    assert out["failures"] == [] and len(calls) == 1, "지면 수와 상관없이 생성 호출은 1회"
    assert calls[0]["placement"]["platform"] == "모든 선택 지면 공통"
    assert calls[0]["limits"]["headline"]["max"] == 15 and calls[0]["limits"]["body"]["max"] == 45
    groups = out["groups"]
    assert len(groups) == 3
    for no in (1, 2, 3):
        texts = {(v["headline"], v["body"], v["cta"]) for g in groups for v in g["variants"] if v["variant_no"] == no}
        assert len(texts) == 1, "같은 번호 시안은 모든 지면에서 같은 문장"
    # 위반은 지면별 제한으로 검사: 긴 제목(2번)은 네이티브(25자)·PMax(전각 30자)만 걸리고 Instagram(40자)은 통과
    by = {g["name"]: {v["variant_no"]: v for v in g["variants"]} for g in groups}
    assert by["Instagram 피드"][2]["violations"] == []
    assert any(x["type"] == "length" for x in by["네이티브 2:1"][2]["violations"])
    assert any(x["type"] == "length" for x in by["PMax 가로"][2]["violations"])
    assert by["네이티브 2:1"][1]["violations"] == []


def test_select_variant_applies_to_all_placements_and_clears_old_batches(env):
    from app import models
    c, ids = env["client"], env["ids"]
    c.post(f"/api/designs/{ids['design']}/copy")
    r = c.post(f"/api/designs/{ids['design']}/copy/select-variant", json={"variant_no": 3})
    assert r.status_code == 200 and sorted(r.json()["preset_ids"]) == sorted(ids["presets"].values())
    groups = c.get(f"/api/designs/{ids['design']}/copy").json()
    for g in groups:
        assert [v["variant_no"] for v in g["variants"] if v["selected"]] == [3]
    assert c.post(f"/api/designs/{ids['design']}/copy/select-variant", json={"variant_no": 9}).status_code == 404
    # 카피를 다시 생성(2차수)하고 1번을 고르면 1차수의 3번 선택은 풀려야 한다 (합성은 selected 행을 차수와 무관하게 쓴다)
    c.post(f"/api/designs/{ids['design']}/copy")
    c.post(f"/api/designs/{ids['design']}/copy/select-variant", json={"variant_no": 1})
    with env["Local"]() as db:
        sel = db.query(models.CopyVariant).filter_by(design_id=ids["design"], selected=True).all()
        assert len(sel) == 3 and {(s.batch_no, s.variant_no) for s in sel} == {(2, 1)}
