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

    def fake_generate_checked(brand_name, brand, brief, placement, limits, knowledge=None):
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


def test_copy_call_also_stores_icon_candidates(env, monkeypatch):
    """카피 호출이 (변형, 시도, 아이콘 후보) 를 돌려주면 후보를 브리프 icon_candidates 에 둔다 (아이콘 세트의 제안 호출 대체)."""
    from app import copywriter
    cands = [{"name": f"기능{i}", "concept": f"오브젝트 {i}"} for i in range(1, 16)]

    def fake3(brand_name, brand, brief, placement, limits, knowledge=None):
        vs = [{"headline": "제목", "body": "본문", "cta": "보기", "violations": []}] * 3
        return vs, 1, cands
    monkeypatch.setattr(copywriter, "generate_checked", fake3)
    c, ids = env["client"], env["ids"]
    out = c.post(f"/api/designs/{ids['design']}/copy").json()
    assert out["failures"] == [] and out["icon_candidates"] == 15
    brief = c.get(f"/api/designs/{ids['design']}").json()["brief"]
    assert brief["icon_candidates"] == cands and brief["product_name"] == "리빙 창고 대방출"


def test_parse_icons_and_generate_checked_returns_candidates(monkeypatch):
    from app import copywriter
    data = {"icons": [{"name": "정기배송", "concept": "상자"}, {"name": " ", "concept": "x"}, {"name": "정기배송", "concept": "중복"},
                      {"name": "빠른도착"}] + [{"name": f"n{i}", "concept": ""} for i in range(20)]}
    got = copywriter.parse_icons(data)
    assert got[:2] == [{"name": "정기배송", "concept": "상자"}, {"name": "빠른도착", "concept": ""}] and len(got) == 15

    def fake_generate(brand_name, brand, brief, placement, limits, icons_out=None, assets_out=None, knowledge=None):
        if icons_out is not None:
            icons_out[:] = [{"name": "기능", "concept": "c"}]
        if assets_out is not None:
            assets_out["광고 제목"] = ["x"]
        return [{"headline": "h", "body": "b", "cta": "c"}]
    monkeypatch.setattr(copywriter, "generate", fake_generate)
    variants, attempts, icons, assets = copywriter.generate_checked("b", {}, {}, {}, copywriter.limits_for([]))
    assert attempts == 1 and icons == [{"name": "기능", "concept": "c"}] and variants[0]["violations"] == []
    assert assets == {"광고 제목": ["x"]}


def test_generate_checked_calls_once_unless_auto_retry_and_no_clean_variant(monkeypatch):
    """기본 1회 호출. COPY_AUTO_RETRY(MAX_ATTEMPTS=2)여도 통과 변형이 하나라도 있으면 1회, 전부 위반일 때만 2회."""
    from app import copywriter
    limits = copywriter.limits_for([{"field": "제목", "max_chars": 5, "count_method": "글자 수", "level": "필수"}])
    calls: list[int] = []

    def fake_generate(brand_name, brand, brief, placement, limits, icons_out=None, assets_out=None, knowledge=None):
        calls.append(1)
        return [{"headline": "너무너무긴제목입니다", "body": "본문", "cta": "보기"}, {"headline": "짧다", "body": "본문", "cta": "보기"}]
    monkeypatch.setattr(copywriter, "generate", fake_generate)
    monkeypatch.setattr(copywriter, "MAX_ATTEMPTS", 2)
    variants, attempts, *_ = copywriter.generate_checked("b", {}, {}, {}, limits)
    assert attempts == 1 and len(calls) == 1 and [bool(v["violations"]) for v in variants] == [True, False]

    def all_bad(brand_name, brand, brief, placement, limits, icons_out=None, assets_out=None, knowledge=None):
        calls.append(1)
        return [{"headline": "너무너무긴제목입니다", "body": "본문", "cta": "보기"}]
    monkeypatch.setattr(copywriter, "generate", all_bad)
    calls.clear()
    _, attempts, *_ = copywriter.generate_checked("b", {}, {}, {}, limits)
    assert attempts == 2 and len(calls) == 2
    monkeypatch.setattr(copywriter, "MAX_ATTEMPTS", 1)
    calls.clear()
    _, attempts, *_ = copywriter.generate_checked("b", {}, {}, {}, limits)
    assert attempts == 1 and len(calls) == 1, "기본(자동 재시도 끔)은 전부 위반이어도 1회"


def test_asset_spec_and_parse_counts():
    from types import SimpleNamespace
    from app import copyfields, copywriter
    assert copyfields.parse_asset_counts("광고 제목 3~15개, 긴 광고 제목 1~5개, 설명 2~5개") == [
        {"field": "광고 제목", "min": 3, "max": 15}, {"field": "긴 광고 제목", "min": 1, "max": 5}, {"field": "설명", "min": 2, "max": 5}]
    assert copyfields.parse_asset_counts("없음") == [] and copyfields.parse_asset_counts(None) == []
    pv = SimpleNamespace(placement=SimpleNamespace(name="PMax 정사각형 1:1"),
                         conditions=[{"name": "문구 수량", "value": "광고 제목 3~15개, 설명 2~5개"}],
                         text_limits=[{"field": "광고 제목", "max_chars": 30, "count_method": "전각 2자 계산"},
                                      {"field": "설명", "max_chars": 90, "count_method": "전각 2자 계산"}])
    other = SimpleNamespace(placement=SimpleNamespace(name="Instagram 피드"), conditions=[], text_limits=[])
    spec = copyfields.asset_spec([other, pv])
    assert [(a["field"], a["count"], a["max_chars"], a["method"]) for a in spec] == [("광고 제목", 5, 30, "전각 2자 계산"), ("설명", 4, 90, "전각 2자 계산")]
    data = {"assets": {"광고 제목": ["a", " a ", "", "b", "c", "d", "e", "f"], "설명": ["x"], "엉뚱": ["y"]}}
    got = copywriter.parse_assets(data, [{"field": "광고 제목", "max": 5}, {"field": "설명", "max": 5}])
    assert got == {"광고 제목": ["a", "b", "c", "d", "e"], "설명": ["x"]}
    # 프롬프트에 요구 줄이 들어간다 (limits["assets"])
    limits = {**copywriter.limits_for([]), "assets": spec}
    _, user = copywriter._prompt("b", {}, {"product_name": "p"}, {"platform": "x", "name": "y"}, limits)
    assert "[추가 문구 자산]" in user and "- 광고 제목: 정확히 5개, 각 30자 이하" in user and "PMax 정사각형 1:1" in user
    _, user2 = copywriter._prompt("b", {}, {"product_name": "p"}, {"platform": "x", "name": "y"}, copywriter.limits_for([]))
    assert "[추가 문구 자산]" not in user2


def test_copy_call_requests_and_stores_text_assets(env, monkeypatch):
    """선택 지면에 '문구 수량' 조건이 있으면 카피 호출의 limits["assets"] 에 요구가 들어가고, 받은 자산이 브리프에 저장된다."""
    from app import copywriter, models
    c, ids, Local = env["client"], env["ids"], env["Local"]
    with Local() as db:
        pv = db.get(models.PresetVersion, ids["presets"]["PMax 가로"])
        pv.conditions = [{"name": "문구 수량", "value": "광고 제목 3~15개, 설명 2~5개", "level": "강제", "status": "확인됨"}]
        db.commit()
    seen: list[dict] = []
    def fake4(brand_name, brand, brief, placement, limits, knowledge=None):
        seen.append(limits)
        vs = [{"headline": "제목", "body": "본문", "cta": "보기", "violations": []}] * 3
        return vs, 1, [], {"광고 제목": ["하나", "둘", "셋", "넷", "다섯"], "설명": ["설명 하나", "설명 둘"]}
    monkeypatch.setattr(copywriter, "generate_checked", fake4)
    out = c.post(f"/api/designs/{ids['design']}/copy").json()
    assert out["failures"] == [] and out["text_assets"] == {"광고 제목": 5, "설명": 2}
    assert [a["field"] for a in seen[0]["assets"]] == ["광고 제목", "설명"] and seen[0]["assets"][0]["count"] == 5
    assert "assets" not in out["merged_limits"]
    brief = c.get(f"/api/designs/{ids['design']}").json()["brief"]
    assert brief["text_assets"]["광고 제목"] == ["하나", "둘", "셋", "넷", "다섯"]


def test_fields_for_keeps_cta_as_reference_when_media_has_no_cta_field():
    from app import copyfields
    texts = {"headline": "제목", "body": "본문입니다", "cta": "주문하기"}
    tl = [{"field": "제목", "max_chars": 40, "count_method": "글자 수"}, {"field": "기본 문구", "max_chars": 125, "count_method": "글자 수"}]
    f = copyfields.fields_for(tl, texts, "브랜드")
    assert [x["field"] for x in f] == ["제목", "기본 문구", "CTA"]
    assert f[-1]["value"] == "주문하기" and f[-1]["max_chars"] is None and "플랫폼 버튼 설정" in f[-1]["source"]
    # 버튼명이 있는 매체는 CTA 가 정식 필드
    f = copyfields.fields_for([{"field": "내용", "max_chars": 400, "count_method": "글자 수"}, {"field": "버튼명", "max_chars": 8, "count_method": "글자 수"}], texts, "브랜드")
    assert [x["field"] for x in f] == ["내용", "버튼명", "제목"], "제목은 매체 필드가 없어도 참고로 남는다"
    assert "플랫폼" not in f[1]["source"]


def test_text_assets_only_endpoint_keeps_variants(env, monkeypatch):
    """추가 문구 자산만 생성: 카피 시안·선택은 그대로, brief.text_assets 만 바뀐다. 요구 지면이 없으면 400."""
    from app import copywriter, models
    c, ids, Local = env["client"], env["ids"], env["Local"]
    did = ids["design"]
    assert c.post(f"/api/designs/{did}/copy/assets").status_code == 400
    with Local() as db:
        pv = db.get(models.PresetVersion, ids["presets"]["PMax 가로"])
        pv.conditions = [{"name": "문구 수량", "value": "광고 제목 3~15개", "level": "강제", "status": "확인됨"}]
        db.commit()
    assert c.get(f"/api/designs/{did}").json()["text_asset_fields"] == ["광고 제목"]
    c.post(f"/api/designs/{did}/copy")
    c.post(f"/api/designs/{did}/copy/select-variant", json={"variant_no": 2})
    seen: list[dict] = []
    def fake_generate(brand_name, brand, brief, placement, limits, icons_out=None, assets_out=None, knowledge=None):
        seen.append(limits)
        if assets_out is not None:
            assets_out.update({"광고 제목": ["하나", "둘", "셋"]})
        return []
    monkeypatch.setattr(copywriter, "generate", fake_generate)
    r = c.post(f"/api/designs/{did}/copy/assets")
    assert r.status_code == 200 and r.json() == {"text_assets": {"광고 제목": 3}, "fields": ["광고 제목"]}, r.text
    assert seen[0]["assets"][0]["field"] == "광고 제목"
    groups = c.get(f"/api/designs/{did}/copy").json()
    assert all([v["variant_no"] for v in g["variants"] if v["selected"]] == [2] for g in groups), "시안·선택 유지"
    assert c.get(f"/api/designs/{did}").json()["brief"]["text_assets"] == {"광고 제목": ["하나", "둘", "셋"]}


def test_fit_copy_to_preset_rewrites_only_violating_fields(env, monkeypatch):
    """지면 맞춤 줄이기: 그 지면의 선택 카피 중 제한을 넘는 필드만 Claude 1회로 고치고, 다른 지면은 그대로."""
    from app import llm, models
    c, ids, Local = env["client"], env["ids"], env["Local"]
    did = ids["design"]
    nat, ig = ids["presets"]["네이티브 2:1"], ids["presets"]["Instagram 피드"]
    c.post(f"/api/designs/{did}/copy")
    c.post(f"/api/designs/{did}/copy/select-variant", json={"variant_no": 2})     # 2번: 제목이 네이티브 25자를 넘는다
    assert c.post(f"/api/designs/{did}/presets/{ig}/copy/fit").status_code == 400, "Instagram 은 위반이 없다"
    seen: list[tuple[str, str]] = []
    def fake(system, user, kind, schema):
        seen.append((kind, user))
        return {"headline": "짧게 고친 제목", "body": "무시될 본문", "cta": "무시될 CTA"}
    monkeypatch.setattr(llm, "chat_json", fake)
    r = c.post(f"/api/designs/{did}/presets/{nat}/copy/fit")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["changed"] == ["headline"] and j["headline"] == "짧게 고친 제목" and j["body"] == "본문 둘" and j["fits"] is True
    assert seen[0][0] == "copy_fit" and "headline: 25자 이하" in seen[0][1] and "네이티브 2:1" in seen[0][1]
    with Local() as db:
        rows = {v.preset_version_id: v for v in db.query(models.CopyVariant).filter_by(design_id=did, selected=True).all()}
        assert rows[nat].headline == "짧게 고친 제목" and rows[nat].violations == []
        assert rows[ig].headline != "짧게 고친 제목", "다른 지면의 카피는 그대로"


def test_copy_records_knowledge_refs(env):
    """카피 생성은 넣은 지식(규제·검색 조각)을 캠페인 입력에 남기고 응답에 요약을 준다. 지식이 없는 브랜드는 빈 기록."""
    c, ids = env["client"], env["ids"]
    out = c.post(f"/api/designs/{ids['design']}/copy").json()
    assert out["knowledge"] == {"law": 0, "guide": 0, "industries": out["knowledge"]["industries"], "error": None,
                                "functional_mismatch": None}
    brief = c.get(f"/api/designs/{ids['design']}").json()["brief"]
    assert brief["knowledge"]["law_chunk_ids"] == [] and brief["knowledge"]["guide"] == []
