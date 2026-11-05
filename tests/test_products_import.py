"""제품 가져오기(products_import + /api/knowledge/products/*): 가짜 사이트(MockTransport)로 목록 발견 → 가져오기(지식 조각 + 제품 이미지 자산)
→ 제품으로 캠페인 만들기. 네트워크·임베딩 과금 없음."""
import io

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import knowledge, models, products_import, routers_assets
from app.main import app
from tests.test_knowledge import fake_embed

LIST_HTML = """
<ul><li><div class="pdtItem"><a href="javascript:void(0);" class="linkArea"
 prdNm= "에스트라 아토베리어365 하이드로 수딩크림" prdSeq="1148" salPrc="33000" catCd1stNm="AESTURA 365" catCd2ndNm="ATOBARRIER365"></a></div></li>
<li><div class="pdtItem"><a prdNm= "에스트라 에이시카365 마스크" prdSeq="1147" salPrc="" catCd1stNm="AESTURA 365" catCd2ndNm="A-CICA365"></a></div></li></ul>
"""
DETAIL_HTML = """
<div class="thumbnail"><div class="item on"><picture><source srcset="https://image.example/upload/product/1148_1088_DSPIMG_S.webp?ver=1" type="image/webp" />
<img src="x.png" alt=""></picture></div></div>
<div class="pdtInfo"><div class="top"><p class="pdtName">에스트라 아토베리어365 하이드로 수딩크림</p>
<p class="intro">민감피부의 속수분을 채워주는 장벽수분 캡슐크림</p>
<p class="desc"> 민감하고 수분이 부족한 지성 피부의 유수분 밸런스를 맞추는 캡슐크림 </p><p class="quantity">80ml</p></div>
<div class="toggleBox2"><a href="#" class="btn_toggle">전성분</a><div class="txtBox" style="white-space:pre-line;">세라마이드, 콜레스테롤, 지방산 &amp; 히알루론산</div></div></div>
"""


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (1600, 1200), (10, 110, 140, 255)).save(buf, format="PNG")
    return buf.getvalue()


def fake_site(request: httpx.Request) -> httpx.Response:
    u = str(request.url)
    if "list.do" in u:
        return httpx.Response(200, text=LIST_HTML)
    if "view.do" in u and "prdSeq=1148" in u:
        return httpx.Response(200, text=DETAIL_HTML)
    if "view.do" in u:
        return httpx.Response(500, text="boom")
    if "DSPIMG_L.webp" in u:
        return httpx.Response(200, content=_png(), headers={"content-type": "image/png"})
    return httpx.Response(404)


@pytest.fixture
def world(pg_engine, monkeypatch):
    monkeypatch.setattr(products_import, "SLEEP", 0)
    with sessionmaker(bind=pg_engine)() as s:
        g = models.BrandGroup(name="아모레퍼시픽"); s.add(g); s.flush()
        b = models.Brand(name="에스트라", group_id=g.id); s.add(b); s.flush()
        v = models.BrandVersion(brand_id=b.id, version_no=1); s.add(v); s.flush(); b.current_version_id = v.id
        s.commit()
        return {"brand_id": b.id, "group_id": g.id}


@pytest.fixture
def client(pg_engine):
    Local = sessionmaker(bind=pg_engine, autoflush=False)

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[routers_assets.get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_aestura_rules_parse_list_and_detail():
    found = products_import.aestura_list(LIST_HTML)
    assert [(f.seq, f.name, f.line, f.price) for f in found] == [
        ("1148", "에스트라 아토베리어365 하이드로 수딩크림", "ATOBARRIER365", 33000), ("1147", "에스트라 에이시카365 마스크", "A-CICA365", None)]
    p = products_import.aestura_detail(DETAIL_HTML, products_import.Product(seq="1148", name="x"))
    assert p.intro.startswith("민감피부의") and p.quantity == "80ml" and p.ingredients == "세라마이드, 콜레스테롤, 지방산 & 히알루론산"
    assert p.image_url.endswith("1148_1088_DSPIMG_L.webp?ver=1") and p.problems == []
    assert products_import.clean_name(p.name, "에스트라") == "아토베리어365 하이드로 수딩크림"
    assert products_import._aliases("아토베리어365 하이드로 수딩크림") == ["아토베리어 365 하이드로 수딩크림", "하이드로 수딩크림"]


def test_import_registers_chunk_and_image_and_isolates_failures(pg_engine, world):
    with sessionmaker(bind=pg_engine)() as s:
        brand = s.get(models.Brand, world["brand_id"])
        res = products_import.import_products(s, brand, ["1148", "1147", "9999"], embedder=fake_embed,
                                              client=httpx.Client(transport=httpx.MockTransport(fake_site)))
        s.commit()
        ok, bad, missing = res
        assert ok["ok"] and ok["name"] == "아토베리어365 하이드로 수딩크림" and ok["asset_id"] and ok["problems"] == []
        assert not bad["ok"] and "HTTPStatusError" in bad["problems"][0], "상세 실패는 그 제품만 실패로 남고 다음 제품은 계속"
        assert not missing["ok"] and missing["problems"] == ["목록에 없는 제품"]
        doc = s.get(models.KnowledgeDoc, ok["doc_id"])
        ch = s.scalar(select(models.KnowledgeChunk).where(models.KnowledgeChunk.doc_id == doc.id))
        assert doc.layer == "제품" and doc.source_id == "prod_aestura_site_1148" and doc.brand_id == brand.id
        assert ch.product_name == "아토베리어365 하이드로 수딩크림" and "하이드로 수딩크림" in ch.aliases
        assert "80ml" in ch.text and "세라마이드" in ch.text and "기능성 화장품 표시 여부" in ch.text and ch.vec is not None
        a = s.get(models.Asset, ok["asset_id"])
        assert a.kind == "product" and a.mime == "image/webp" and a.width == 1024 and a.height == 768, "긴 변 1024 로 줄여 WEBP"
        assert doc.visual["image_asset_id"] == a.id and doc.visual["price"] == 33000
        # 다시 가져오면 이전 판은 보관, 새 자산
        res2 = products_import.import_products(s, brand, ["1148"], embedder=fake_embed, client=httpx.Client(transport=httpx.MockTransport(fake_site)))
        s.commit()
        assert res2[0]["ok"] and res2[0]["doc_id"] != doc.id
        assert s.get(models.KnowledgeDoc, doc.id).status == "보관"
        assert products_import.imported(s, brand.id) == {"prod_aestura_site_1148": res2[0]["doc_id"]}


def test_api_discover_import_and_campaign(client, pg_engine, world, monkeypatch):
    monkeypatch.setattr(products_import, "_client", lambda: httpx.Client(transport=httpx.MockTransport(fake_site)))
    monkeypatch.setattr(knowledge, "embed", fake_embed)
    r = client.get("/api/knowledge/products/discover", params={"brand_id": world["brand_id"]}).json()
    assert r["supported"] and [p["seq"] for p in r["products"]] == ["1148", "1147"] and r["products"][0]["doc_id"] is None
    r = client.post("/api/knowledge/products/import", json={"brand_id": world["brand_id"], "seqs": ["1148"]}).json()
    doc_id = r["results"][0]["doc_id"]
    assert r["results"][0]["ok"] and doc_id
    assert client.get("/api/knowledge/products/discover", params={"brand_id": world["brand_id"]}).json()["products"][0]["doc_id"] == doc_id
    # 조각 응답에 제품 이미지·가격·용량
    ch = client.get(f"/api/knowledge/docs/{doc_id}/chunks", params={"group_id": world["group_id"]}).json()
    assert ch["doc"]["image_asset_id"] == r["results"][0]["asset_id"] and ch["doc"]["quantity"] == "80ml" and ch["doc"]["brand_id"] == world["brand_id"]
    # 제품으로 캠페인: 상품명·설명·제품 이미지가 채워진다
    c = client.post("/api/knowledge/products/campaign", json={"doc_id": doc_id, "key_message": "속수분"}).json()
    with sessionmaker(bind=pg_engine)() as s:
        d = s.get(models.Design, c["design_id"])
    assert d.brand_id == world["brand_id"] and d.kind == "광고 이미지"
    assert d.brief["product_name"] == "아토베리어365 하이드로 수딩크림" and d.brief["product_image_asset_id"] == c["product_image_asset_id"]
    assert "80ml" in d.brief["description"] and "기능성 화장품 표시 여부" not in d.brief["description"] and d.brief["key_message"] == "속수분"
    assert d.brief["from_product_doc"] == doc_id
    # 지원하지 않는 브랜드
    with sessionmaker(bind=pg_engine)() as s:
        other = models.Brand(name="나이키"); s.add(other); s.commit(); oid = other.id
    assert client.get("/api/knowledge/products/discover", params={"brand_id": oid}).json()["supported"] is False
    assert client.post("/api/knowledge/products/import", json={"brand_id": oid, "seqs": ["1"]}).status_code == 400
    assert client.post("/api/knowledge/products/campaign", json={"doc_id": 99999}).status_code == 404
