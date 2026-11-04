"""지식 열람 API(routers_knowledge): 그룹 기준 요약·문서 목록·조각과 '쓰인 카피'·검색 미리보기. 임베딩은 가짜(과금 없음)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app import knowledge, models, routers_assets
from app.main import app
from tests.test_knowledge import fake_embed


@pytest.fixture
def client(pg_engine, monkeypatch):
    Local = sessionmaker(bind=pg_engine, autoflush=False)

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[routers_assets.get_db] = override
    monkeypatch.setattr(knowledge, "embed", fake_embed)          # 검색 미리보기의 질의 임베딩
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def world(pg_engine):
    """그룹(아모레) 에 브랜드 2, 다른 단독 브랜드 1. 규제 1·그룹 1·브랜드 1·제품 2(브랜드별 1). 시안 하나가 규제·제품 조각을 참고했다."""
    with sessionmaker(bind=pg_engine)() as s:
        g = models.BrandGroup(name="아모레퍼시픽"); s.add(g); s.flush()
        b1 = models.Brand(name="설화수", group_id=g.id); b2 = models.Brand(name="라네즈", group_id=g.id); solo = models.Brand(name="나이키")
        s.add_all([b1, b2, solo]); s.flush()
        for b in (b1, b2, solo):
            v = models.BrandVersion(brand_id=b.id, version_no=1); s.add(v); s.flush(); b.current_version_id = v.id
        add = lambda **kw: knowledge.add_document(s, embedder=fake_embed, **kw)
        law = add(layer="규제", source_id="law13", title="제13조", industry="화장품",
                  chunks=[("제13조 제1항", "의약품 오인 광고 금지"), ("제13조 제2항", "총리령으로 정한다")])
        add(layer="그룹", source_id="ap_rni", title="녹차 연구", chunks=[("녹차 1", "신품종 녹차 보습")], group_id=g.id)
        add(layer="브랜드", source_id="brand_s", title="설화수 소개", chunks=[("소개", "홀리스틱 뷰티")], brand_id=b1.id)
        p1 = add(layer="제품", source_id="prod_s", title="자음생크림", chunks=[("자음생크림", "인삼 탄력 크림")], brand_id=b1.id,
                 product_name="자음생크림", aliases=["자음생"], functional=["주름 개선"], visual={"motifs": "인삼 뿌리"})
        add(layer="제품", source_id="prod_l", title="슬리핑 마스크", chunks=[("슬리핑 마스크", "밤새 수분")], brand_id=b2.id, product_name="슬리핑 마스크")
        add(layer="제품", source_id="prod_s", title="자음생크림 v2", chunks=[("자음생크림", "인삼 탄력 크림 2")], brand_id=b1.id,
            product_name="자음생크림")       # 같은 출처 재등록 → 이전 판은 보관
        s.flush()
        law_chunk = s.query(models.KnowledgeChunk).filter_by(doc_id=law.id, seq=1).one()
        p_chunk = s.query(models.KnowledgeChunk).filter_by(doc_id=p1.id).one()
        d = models.Design(brand_id=b1.id, brand_version_id=b1.current_version_id, kind="광고 이미지", brief={
            "product_name": "자음생크림", "description": "탄력 크림",
            "knowledge": {"law_chunk_ids": [law_chunk.id], "guide": [{"chunk_id": p_chunk.id, "score": 0.9}]}})
        s.add(d); s.flush()
        s.commit()
        return {"group_id": g.id, "b1": b1.id, "b2": b2.id, "solo": solo.id, "law_chunk": law_chunk.id, "p_chunk": p_chunk.id,
                "p1": p1.id, "design": d.id}


def test_summary_counts_group_scope_only(client, world):
    r = client.get("/api/knowledge/summary", params={"group_id": world["group_id"]}).json()
    assert r["group"]["name"] == "아모레퍼시픽"
    layers = {x["layer"]: x for x in r["layers"]}
    assert layers["규제"] == {"layer": "규제", "docs": 1, "chunks": 2}
    assert layers["그룹"]["docs"] == 1 and layers["브랜드"]["docs"] == 1
    assert layers["제품"]["docs"] == 2, "보관된 이전 판은 세지 않는다"
    assert [b["docs"] for b in r["brands"]] == [2, 1]
    assert r["last_registered"] is not None
    # 그룹이 없으면 규제만 보인다
    r0 = client.get("/api/knowledge/summary").json()
    assert r0["group"] is None and {x["layer"]: x["docs"] for x in r0["layers"]}["제품"] == 0


def test_docs_list_filters_and_counts_usage(client, world):
    rows = client.get("/api/knowledge/docs", params={"layer": "제품", "group_id": world["group_id"]}).json()
    assert [(d["title"], d["brand"], d["chunks"], d["archived"], d["used_designs"]) for d in rows] == [
        ("슬리핑 마스크", "라네즈", 1, 0, 0), ("자음생크림 v2", "설화수", 1, 1, 0)], "보관 판은 목록에 없고 archived 로 센다"
    only = client.get("/api/knowledge/docs", params={"layer": "제품", "group_id": world["group_id"], "brand_id": world["b2"]}).json()
    assert [d["title"] for d in only] == ["슬리핑 마스크"]
    law = client.get("/api/knowledge/docs", params={"layer": "규제", "group_id": world["group_id"]}).json()
    assert law[0]["used_designs"] == 1 and law[0]["industry"] == "화장품"
    assert client.get("/api/knowledge/docs", params={"layer": "엉뚱"}).status_code == 400


def test_chunks_carry_text_meta_and_used_copies(client, world):
    r = client.get(f"/api/knowledge/docs/{world['p1']}/chunks", params={"group_id": world["group_id"]}).json()
    assert r["doc"]["motifs"] == "인삼 뿌리" and r["doc"]["status"] == "보관"
    c = r["chunks"][0]
    assert c["product_name"] == "자음생크림" and c["aliases"] == ["자음생"] and c["functional"] == ["주름 개선"] and c["has_vector"]
    assert [(u["design_id"], u["role"], u["brand"], u["product_name"]) for u in c["used"]] == [(world["design"], "근거", "설화수", "자음생크림")]
    law = client.get("/api/knowledge/docs/{}/chunks".format(
        client.get("/api/knowledge/docs", params={"layer": "규제"}).json()[0]["id"]), params={"group_id": world["group_id"]}).json()
    assert [len(c["used"]) for c in law["chunks"]] == [1, 0] and law["chunks"][0]["used"][0]["role"] == "규제"
    assert client.get("/api/knowledge/docs/99999/chunks").status_code == 404


def test_search_preview_uses_same_search_as_copy(client, world):
    r = client.post("/api/knowledge/search", json={"group_id": world["group_id"], "brand_id": world["b1"],
                                                   "product_name": "자음생크림", "description": "인삼 탄력 크림"}).json()
    layers = {x["layer"]: x["rows"] for x in r["layers"]}
    assert layers["제품"][0]["exact_product"] and layers["제품"][0]["source_id"] == "prod_s"
    assert all(row["source_id"] != "prod_l" for row in layers["제품"]), "다른 브랜드 제품은 나오지 않는다"
    assert r["query"].startswith("자음생크림 / 인삼 탄력 크림") and r["mode"] == "sql"
    assert client.post("/api/knowledge/search", json={}).status_code == 400
