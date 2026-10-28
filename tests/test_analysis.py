"""분석 화면(analysis.py): 색 거리·팔레트, 입력 반영 표, 프롬프트 목록(기록/저장/재구성), 비전 채점 저장. 외부 API 는 부르지 않는다."""
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import analysis, calllog


def test_delta_e_and_palette():
    assert analysis.delta_e("#4C69FE", "#4C69FE") == 0.0
    assert analysis.delta_e("#000000", "#FFFFFF") == 100.0
    assert analysis.delta_e("#4C69FE", "#5A70F0") < analysis.NEAR_DE < analysis.delta_e("#4C69FE", "#FE4C4C")
    im = Image.new("RGBA", (100, 100), (76, 105, 254, 255))
    im.paste((254, 150, 76, 255), (0, 0, 25, 100))
    pal = analysis.palette(im, 4)
    assert [p["hex"] for p in pal] == ["#4C69FE", "#FE964C"] and pal[0]["share"] == 0.75
    # 투명 픽셀은 세지 않는다 (아이콘 PNG)
    icon = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(icon).rectangle((0, 0, 31, 63), fill=(254, 150, 76, 255))
    assert analysis.palette(icon) == [{"hex": "#FE964C", "share": 1.0}]
    assert analysis.palette(Image.new("RGBA", (8, 8), (0, 0, 0, 0))) == []


def test_color_match_coverage():
    pal = [{"hex": "#4C69FE", "share": 0.6}, {"hex": "#808080", "share": 0.4}]
    m = analysis.color_match(pal, [{"role": "주조색", "hex": "#4c69fe"}, {"role": "강조색", "hex": "#FE964C"}])
    assert m["coverage"] == 0.6
    assert m["per_color"][0] == {"role": "주조색", "hex": "#4C69FE", "nearest": "#4C69FE", "delta_e": 0.0, "share": 0.6}
    assert m["per_color"][1]["delta_e"] > analysis.NEAR_DE
    assert analysis.color_match(pal, [])["coverage"] is None
    # 배경색(흰색)·텍스트색은 어느 이미지에나 있어 coverage 에서 뺀다. per_color 에는 남는다
    m2 = analysis.color_match([{"hex": "#FFFFFF", "share": 0.7}, {"hex": "#4C69FE", "share": 0.3}],
                              [{"role": "주조색", "hex": "#4C69FE"}, {"role": "배경색", "hex": "#FFFFFF"}])
    assert m2["coverage"] == 0.3 and m2["coverage_roles"] == ["주조색"] and len(m2["per_color"]) == 2
    assert analysis.color_match([], [{"hex": "#000000"}])["coverage"] is None


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app import models
    from app.db import Base
    from app.main import app
    from app.routers_assets import get_db

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[get_db] = override
    with Local() as db:
        brand = models.Brand(name="쿠챠", description="랜덤 커머스")
        db.add(brand); db.flush()
        bv = models.BrandVersion(brand_id=brand.id, version_no=1, levels={"colors": "필수"},
                                 colors=[{"role": "주조색", "hex": "#4C69FE"}, {"role": "강조색", "hex": "#FE964C"}],
                                 icon_style={"corner": "둥근"}, image_tone={"description": "밝고 중립적인 배경, 무광 레진 재질"},
                                 voice={"description": "친근함", "polite": True}, required_phrases=["쿠챠"], forbidden_phrases=["최저가"])
        db.add(bv); db.flush()
        brand.current_version_id = bv.id
        plat = models.Platform(name="Meta"); db.add(plat); db.flush()
        pl = models.Placement(platform_id=plat.id, ad_type="피드", name="Instagram 피드", content_kind="완성형"); db.add(pl); db.flush()
        pv = models.PresetVersion(placement_id=pl.id, production_width=1080, production_height=1080, ratio="1:1", status="확인됨",
                                  text_limits=[{"field": "기본 문구", "max_chars": 125, "count_method": "공백 포함"}])
        db.add(pv); db.flush()
        d = models.Design(brand_id=brand.id, brand_version_id=bv.id, kind="광고 이미지", preset_version_ids=[pv.id],
                          brief={"product_name": "랜덤 캡슐", "description": "실물 굿즈", "key_message": "오늘의 발견", "target": "20대"})
        db.add(d); db.flush()
        bg_path = tmp_path / "bg.png"
        Image.new("RGB", (64, 64), (76, 105, 254)).save(bg_path)
        for _ in range(2):   # 같은 파일이 지면마다 한 행씩 연결되는 구조
            db.add(models.GeneratedImage(design_id=d.id, preset_version_id=pv.id, kind="background", file_path=str(bg_path),
                                         width=64, height=64, prompt="[표현 방식] 테스트 배경 프롬프트 랜덤 캡슐",
                                         layout={"template": "정사각", "style": "실사 제품·공간", "quality": "low", "provider": "openai",
                                                 "model": "gpt-image-1", "prompts": ["image/background@1.4#abcd1234"]}))
        db.add(models.CopyVariant(design_id=d.id, preset_version_id=pv.id, batch_no=1, variant_no=1, headline="오늘의 발견",
                                  body="쿠챠에서", cta="보기", selected=True))
        db.commit()
        ids = {"design": d.id, "bg_path": str(bg_path)}
    with TestClient(app) as c:
        yield c, ids, Local
    app.dependency_overrides.clear()


def test_analysis_page_payload(env):
    c, ids, _ = env
    r = c.get(f"/api/designs/{ids['design']}/analysis")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["brand"]["name"] == "쿠챠" and j["design"]["brief"]["product_name"] == "랜덤 캡슐"
    # 입력 반영 표: 색은 배경 프롬프트에 안 들어가고 아이콘·합성에서만 쓰인다 (학습 포인트)
    colors = next(m for m in j["mapping"] if m["key"] == "colors")
    assert colors["stages"]["background"] and colors["stages"]["icon"] and colors["stages"]["compose"]
    tone = next(m for m in j["mapping"] if m["key"] == "image_tone")
    assert tone["stages"]["background"] and tone["stages"]["copy"] is None
    # 같은 파일의 배경 행 2개 → 배경 1장, 팔레트는 주조색 그대로 → 일치 비율 1.0
    bgs = j["results"]["backgrounds"]
    assert len(bgs) == 1 and bgs[0]["match"]["coverage"] == 1.0 and bgs[0]["match"]["per_color"][0]["delta_e"] == 0.0
    # 프롬프트: 배경은 DB 저장분(stored), 카피는 기록이 없어 재구성(reconstructed)
    kinds = {(p["kind"], p["source"]) for p in j["prompts"]}
    assert ("background", "stored") in kinds and ("copy", "reconstructed") in kinds
    copy = next(p for p in j["prompts"] if p["kind"] == "copy")
    assert "랜덤 캡슐" in copy["user"] and "최저가" in copy["user"] and copy["system"]
    assert j["variables"]["product_name"] == "랜덤 캡슐"
    assert set(j["cost"]["stages"]) == {"copy", "background", "icon", "judge"}
    assert j["judgments"] == [] and j["judge_estimate"]["usd"] is not None


def test_recorded_prompt_shows_before_reconstruction(env, monkeypatch):
    """호출 기록에 전문이 있으면 그것을 recorded 로 보여 주고 재구성은 하지 않는다. 배경 기록은 같은 문장의 DB 행과 합쳐진다."""
    c, ids, _ = env
    token = calllog._design.set(ids["design"])      # 라우터 밖에서 기록하므로 시안 id 를 직접 넣고, 다른 테스트에 남지 않게 되돌린다
    try:
        calllog.record("chat", "copy", "anthropic", "claude-opus-5-5", tokens_in=10, tokens_out=5,
                       prompt_text={"system": "SYS", "user": "USER 랜덤 캡슐"})
        calllog.record("images.generate", "background", "openai", "gpt-image-1", size="1024x1024", quality="low",
                       prompt_text={"prompt": "[표현 방식] 테스트 배경 프롬프트 랜덤 캡슐"})
    finally:
        calllog._design.reset(token)
    j = c.get(f"/api/designs/{ids['design']}/analysis").json()
    copy = [p for p in j["prompts"] if p["kind"] == "copy"]
    assert len(copy) == 1 and copy[0]["source"] == "recorded" and copy[0]["system"] == "SYS"
    bg = [p for p in j["prompts"] if p["stage"] == "background"]
    assert len(bg) == 1 and bg[0]["source"] == "recorded" and bg[0]["image"]["template"] == "정사각" and bg[0]["refs"]
    assert j["cost"]["rows"][0]["kind"] == "background" and j["cost"]["rows"][0]["prompt_id"]


def test_judge_saves_result_and_records_call(env, monkeypatch):
    c, ids, _ = env
    from app import llm
    seen = {}

    def fake_vision(system, user, images, kind, schema):
        seen.update(system=system, user=user, n=len(images), kind=kind)
        calllog.record("chat", kind, "anthropic", "claude-opus-5-5", tokens_in=2000, tokens_out=300, images=1)   # 라우터 안이라 시안 id 가 붙는다
        return {"scores": [{"item": it, "score": 4, "reason": "ok"} for it in analysis.JUDGE_ITEMS], "overall": 4, "summary": "무난"}
    monkeypatch.setattr(llm, "vision_json", fake_vision)
    r = c.post(f"/api/designs/{ids['design']}/analysis/judge", json={})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["overall"] == 4 and len(j["scores"]) == 5 and j["cost_usd"] is not None
    assert seen["n"] == 1 and seen["kind"] == "judge" and "무광 레진 재질" in seen["user"] and "랜덤 캡슐" in seen["user"]
    assert "1. 톤 일치" in seen["user"] and "아트 디렉터" in seen["system"]
    page = c.get(f"/api/designs/{ids['design']}/analysis").json()
    assert len(page["judgments"]) == 1 and page["judgments"][0]["background_id"] == j["background_id"]
    assert page["cost"]["stages"]["judge"]["count"] == 1
    assert c.post(f"/api/designs/{ids['design']}/analysis/judge", json={"background_id": 9999}).status_code == 404
