"""제품에서 시작(/api/knowledge/products/quickstart*): 제품 조각 하나로 확인 시트 내용(브리프 제안 + 기본 지면)을 만들고,
캠페인 생성 → 카피 3안 → 1안 선택까지 한 번에. 모델은 가짜(llm.chat_json · copywriter.generate_checked). 가짜 사이트로 제품을 먼저 가져온다."""
import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app import copywriter, knowledge, llm, models, products_import
from tests.test_knowledge import fake_embed
from tests.test_products_import import client, fake_site, world  # noqa: F401  (픽스처)


@pytest.fixture
def product(client, pg_engine, world, monkeypatch):
    """가짜 사이트에서 제품 1개를 가져오고, 지면 2개와 브랜드 소개 조각을 둔다. 반환: {doc_id, preset_ids, ...world}"""
    monkeypatch.setattr(products_import, "_client", lambda: httpx.Client(transport=httpx.MockTransport(fake_site)))
    monkeypatch.setattr(knowledge, "embed", fake_embed)
    r = client.post("/api/knowledge/products/import", json={"brand_id": world["brand_id"], "seqs": ["1148"]}).json()
    doc_id = r["results"][0]["doc_id"]
    with sessionmaker(bind=pg_engine)() as s:
        plat = models.Platform(name="테스트"); s.add(plat); s.flush()
        pids = []
        for name, w, h in (("피드 4:5", 1440, 1800), ("스토리 9:16", 1440, 2560)):
            pl = models.Placement(platform_id=plat.id, name=name, ad_type="테스트", content_kind="완성형"); s.add(pl); s.flush()
            pv = models.PresetVersion(placement_id=pl.id, production_width=w, production_height=h, status="확인됨"); s.add(pv); s.flush()
            pids.append(pv.id)
        bd = models.KnowledgeDoc(source_id="brand_aestura", title="에스트라 브랜드 소개", layer="브랜드", brand_id=world["brand_id"], status="사용", source_url="", fetched_at="")
        s.add(bd); s.flush()
        s.add(models.KnowledgeChunk(doc_id=bd.id, seq=0, section="소개", text="에스트라는 민감 피부를 위한 더마 브랜드다", embed_model="t", vec=fake_embed(["x"])[0]))
        s.commit()
    return {**world, "doc_id": doc_id, "preset_ids": pids}


def _fake_copy(monkeypatch):
    def gen(brand_name, brand, brief, placement, limits, knowledge=None):
        return [{"headline": f"{brief['product_name'][:6]} {i}", "body": "속수분 장벽 크림", "cta": "보기", "angle": "a", "evidence": "e"} for i in (1, 2, 3)], 1
    monkeypatch.setattr(copywriter, "generate_checked", gen)
    monkeypatch.setattr(copywriter, "extras", lambda *a, **k: ([], {}))


def test_docs_list_carries_product_meta(client, product):
    rows = client.get("/api/knowledge/docs", params={"layer": "제품", "group_id": product["group_id"], "brand_id": product["brand_id"]}).json()
    row = next(r for r in rows if r["id"] == product["doc_id"])
    assert row["image_asset_id"] and row["quantity"] == "80ml" and row["line"] == "ATOBARRIER365"


def test_preview_fills_brief_suggests_and_picks_default_presets(client, product, pg_engine, monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "chat_json", lambda s, u, kind, schema: seen.append((kind, u)) or {"key_message": "속수분을 채우는 장벽 크림", "target": "건조하고 민감한 30대"})
    r = client.post("/api/knowledge/products/quickstart/preview", json={"doc_id": product["doc_id"]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["product_name"] == "아토베리어365 하이드로 수딩크림" and "80ml" in j["description"] and j["product_image_asset_id"]
    assert j["suggested"] and j["key_message"] == "속수분을 채우는 장벽 크림" and j["target"] == "건조하고 민감한 30대" and j["suggest_error"] is None
    kind, user = seen[0]
    assert kind == "copy_brief" and "민감 피부를 위한 더마 브랜드" in user and "80ml" in user     # 브랜드 소개 + 제품 자료가 프롬프트에
    assert j["presets"] == [] and j["presets_source"] == "없음"          # 아직 캠페인이 없고 시연 기본 세트 이름의 지면도 없다
    # 이 브랜드에 지면 있는 캠페인이 생기면 그 지면이 기본
    with sessionmaker(bind=pg_engine)() as s:
        b = s.get(models.Brand, product["brand_id"])
        s.add(models.Design(brand_id=b.id, brand_version_id=b.current_version_id, kind="광고 이미지", preset_version_ids=product["preset_ids"],
                            brief={"product_name": "x"}))
        s.commit()
    j = client.post("/api/knowledge/products/quickstart/preview", json={"doc_id": product["doc_id"]}).json()
    assert [p["id"] for p in j["presets"]] == product["preset_ids"] and j["presets_source"] == "이 브랜드의 마지막 캠페인 지면"
    assert j["presets"][0]["name"] == "피드 4:5" and j["presets"][0]["width"] == 1440


def test_preview_keeps_going_when_suggestion_fails(client, product, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(llm, "chat_json", boom)
    j = client.post("/api/knowledge/products/quickstart/preview", json={"doc_id": product["doc_id"]}).json()
    assert not j["suggested"] and j["key_message"] == "" and "제안 실패" in j["suggest_error"] and j["product_name"]


def test_quickstart_creates_campaign_copy_and_selects_first_variant(client, product, pg_engine, monkeypatch):
    _fake_copy(monkeypatch)
    r = client.post("/api/knowledge/products/quickstart", json={"doc_id": product["doc_id"], "key_message": "속수분", "target": "30대",
                                                                 "preset_version_ids": product["preset_ids"]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["has_copy"] and j["selected_variant"] == 1 and j["copy_failures"] == [] and j["preset_version_ids"] == product["preset_ids"]
    with sessionmaker(bind=pg_engine)() as s:
        d = s.get(models.Design, j["design_id"])
        assert d.brief["product_name"] == "아토베리어365 하이드로 수딩크림" and d.brief["key_message"] == "속수분" and d.brief["quickstart"] is True
        assert d.brief["product_image_asset_id"] and d.brief["from_product_doc"] == product["doc_id"]
        rows = s.query(models.CopyVariant).filter_by(design_id=d.id).all()
        assert len(rows) == 6 and sorted(v.variant_no for v in rows if v.selected) == [1, 1]
    # 시안 API 로도 열린다(프론트가 이 id 로 캠페인 화면을 연다)
    assert client.get(f"/api/designs/{j['design_id']}").json()["brief"]["quickstart"] is True


def test_quickstart_requires_presets_and_valid_product(client, product, monkeypatch):
    _fake_copy(monkeypatch)
    r = client.post("/api/knowledge/products/quickstart", json={"doc_id": product["doc_id"], "preset_version_ids": []})
    assert r.status_code == 400 and "지면" in r.json()["detail"]
    assert client.post("/api/knowledge/products/quickstart", json={"doc_id": 99999, "preset_version_ids": product["preset_ids"]}).status_code == 404
    assert client.post("/api/knowledge/products/quickstart/preview", json={"doc_id": 99999}).status_code == 404


def test_quickstart_reports_copy_failure_but_keeps_campaign(client, product, pg_engine, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(copywriter, "generate_checked", boom)
    j = client.post("/api/knowledge/products/quickstart", json={"doc_id": product["doc_id"], "preset_version_ids": product["preset_ids"]}).json()
    assert not j["has_copy"] and j["selected_variant"] is None and j["copy_failures"] and "RuntimeError" in j["copy_failures"][0]
    with sessionmaker(bind=pg_engine)() as s:
        assert s.get(models.Design, j["design_id"]) is not None      # 캠페인은 남아 기존 7단계로 이어서 할 수 있다


def test_preview_falls_back_to_demo_set_by_placement_name(client, product, pg_engine, monkeypatch):
    """캠페인이 없으면 시연 기본 세트(DEFAULT_PLACEMENTS)를 지면 이름으로 찾아 채운다. '확인 필요' 지면은 빼고, 마지막 캠페인이 생기면 그쪽이 우선."""
    from app import routers_knowledge
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"key_message": "k", "target": "t"})
    with sessionmaker(bind=pg_engine)() as s:
        plat = models.Platform(name="Meta"); s.add(plat); s.flush()
        ids = {}
        for name, st in (("Instagram 피드", "확인됨"), ("Facebook 피드", "확인됨"), ("PMax 가로 1.91:1", "확인 필요")):
            pl = models.Placement(platform_id=plat.id, name=name, ad_type="피드", content_kind="완성형"); s.add(pl); s.flush()
            pv = models.PresetVersion(placement_id=pl.id, production_width=1080, production_height=1080, status=st); s.add(pv); s.flush()
            ids[name] = pv.id
        s.commit()
    j = client.post("/api/knowledge/products/quickstart/preview", json={"doc_id": product["doc_id"]}).json()
    assert [p["id"] for p in j["presets"]] == [ids["Instagram 피드"], ids["Facebook 피드"]] and j["presets_source"] == "시연 기본 세트 2개"
    assert len(routers_knowledge.DEFAULT_PLACEMENTS) == 6
