"""RAG 지식 저장소(knowledge.py)·수집 도구(fetch_sources.py): 자르기, 재등록 시 보관, 층별 검색과 브랜드 필터, 임베딩 비용.
외부 API 는 호출하지 않는다 — 임베딩은 단어 해시로 만든 가짜 벡터를 쓴다."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent


def fake_embed(texts: list[str]) -> list[list[float]]:
    """단어마다 해시 칸에 1 을 더한 64차원 벡터. 같은 단어를 많이 공유할수록 가깝다."""
    out = []
    for t in texts:
        v = [0.0] * 64
        for w in t.replace("[", " ").replace("]", " ").split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
        out.append(v)
    return out


@pytest.fixture
def session(tmp_path):
    from app.db import Base
    eng = create_engine(f"sqlite:///{tmp_path / 'k.db'}")
    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as s:
        yield s


LAW = ("제13조(부당한 표시ㆍ광고 행위 등의 금지) ① 영업자 또는 판매자는 다음 각 호의 어느 하나에 해당하는 표시 또는 광고를 하여서는 아니 된다. "
       "<개정 2018. 3. 13.> 1. 의약품으로 잘못 인식할 우려가 있는 표시 또는 광고 2. 기능성화장품이 아닌 화장품을 기능성화장품으로 잘못 인식할 우려가 있는 표시 "
       "② 제1항에 따른 표시ㆍ광고의 범위와 그 밖에 필요한 사항은 총리령으로 정한다.")


def test_chunk_law_splits_paragraphs_and_drops_amendments():
    from app.knowledge import chunk_law
    chunks = chunk_law(LAW, "화장품법")
    assert [sec for sec, _ in chunks] == ["화장품법 제13조 제1항", "화장품법 제13조 제2항"]
    assert all(t.startswith("[화장품법 제13조(부당한 표시ㆍ광고 행위 등의 금지)]") for _, t in chunks)
    assert "<개정" not in chunks[0][1] and "의약품으로 잘못 인식" in chunks[0][1]


def test_chunk_law_splits_long_article_by_items_keeping_lead():
    from app.knowledge import MAX_CHARS, chunk_law
    items = " ".join(f"{i}. 항목 {i} 설명 " + "가" * 120 for i in range(1, 12))
    chunks = chunk_law(f"제2조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다. {items}", "화장품법")
    assert len(chunks) > 1 and all(len(t) <= MAX_CHARS + 120 for _, t in chunks)
    assert all("용어의 뜻은 다음과 같다" in t for _, t in chunks), "호로 잘라도 머리말이 남아야 무엇의 목록인지 안다"
    assert chunks[0][0] == "화장품법 제2조 (1)"


def test_chunk_paragraphs_merges_short_lines_and_drops_tail():
    from app.knowledge import chunk_paragraphs
    text = "진세노믹스\n\n인삼 사포닌을 연구해 온 과정을 설명하는 충분히 긴 문단입니다. 피부 연구의 역사를 담고 있습니다.\n\nX"
    chunks = chunk_paragraphs(text, "연구")
    assert len(chunks) == 1
    assert chunks[0][1].startswith("[연구] 진세노믹스 인삼 사포닌")


def _add(s, **kw):
    from app import knowledge
    return knowledge.add_document(s, embedder=fake_embed, **kw)


def test_reregister_archives_previous_version(session):
    from app.models import KnowledgeDoc
    _add(session, layer="브랜드", source_id="brand_a", title="A", chunks=[("A 1", "옛 소개")], brand_id=1)
    _add(session, layer="브랜드", source_id="brand_a", title="A", chunks=[("A 1", "새 소개")], brand_id=1)
    docs = session.query(KnowledgeDoc).filter_by(source_id="brand_a").order_by(KnowledgeDoc.id).all()
    assert [d.status for d in docs] == ["보관", "사용"]


def test_search_filters_by_brand_and_keeps_layer_k(session):
    from app import knowledge
    _add(session, layer="규제", source_id="law", title="화장품법", chunks=[("제13조", "의약품 오인 광고 금지 아토피 치료"),
                                                                       ("제2조", "기능성 화장품 미백 주름 자외선")])
    _add(session, layer="그룹", source_id="grp", title="그룹", chunks=[("g", "녹차 연구 보습")], group_id=7)
    _add(session, layer="제품", source_id="prod_a_cream", title="A 크림", chunks=[("A 크림", "세라마이드 보습 크림 민감 피부")],
         brand_id=1, product_name="A 크림")
    _add(session, layer="제품", source_id="prod_b_cream", title="B 크림", chunks=[("B 크림", "세라마이드 보습 크림 민감 피부")],
         brand_id=2, product_name="B 크림")
    res = knowledge.search(session, "세라마이드 보습 크림 민감 피부", brand_id=1, group_id=7,
                           layer_k={"규제": 1, "제품": 5, "그룹": 1, "브랜드": 2}, embedder=fake_embed)
    assert [r["source_id"] for r in res["제품"]] == ["prod_a_cream"], "다른 브랜드 제품은 유사도가 같아도 나오면 안 된다"
    assert len(res["규제"]) == 1 and res["브랜드"] == []
    assert res["그룹"][0]["source_id"] == "grp"
    # 그룹이 다른 브랜드는 그룹 자료를 받지 않는다
    assert knowledge.search(session, "녹차", brand_id=1, group_id=99, embedder=fake_embed)["그룹"] == []


def test_exact_product_name_ranks_first(session):
    from app import knowledge
    _add(session, layer="제품", source_id="p1", title="수분 크림", chunks=[("수분 크림", "수분 보습 크림 촉촉")], brand_id=1,
         product_name="수분 크림")
    _add(session, layer="제품", source_id="p2", title="선세럼", chunks=[("선세럼", "자외선 차단 세럼")], brand_id=1,
         product_name="데일리 선세럼", functional=["자외선 차단"])
    res = knowledge.search(session, "수분 보습 크림 촉촉", brand_id=1, group_id=None, product_name="데일리 선세럼",
                           embedder=fake_embed)
    assert res["제품"][0]["source_id"] == "p2" and res["제품"][0]["exact_product"]
    assert res["제품"][0]["functional"] == ["자외선 차단"]


def test_embedding_cost_is_priced():
    from app import pricing
    usd, est = pricing.cost_of({"ok": True, "api": "embeddings", "model": "text-embedding-3-small", "tokens_in": 1_000_000})
    assert usd == pytest.approx(0.02) and not est


def _fetch_module():
    spec = importlib.util.spec_from_file_location("fetch_sources", ROOT / "tools" / "fetch_sources.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_fetch_extractors():
    f = _fetch_module()
    page = ("<html><body><div>메뉴</div><p>제13조(부당한 표시ㆍ광고 행위 등의 금지) ① 의약품으로 잘못 인식할 우려</p>"
            "<p>위로 아래로 검색조문선택 전체저장</p></body></html>")
    assert f.extract_law(page) == "제13조(부당한 표시ㆍ광고 행위 등의 금지) ① 의약품으로 잘못 인식할 우려"
    html = ("<nav>메뉴</nav><main><!-- 주석 --><h2>브랜드</h2><p>설화수는 전통의 지혜에서 영감을 받습니다.</p>"
            "<script>x=1</script><p>Visit and Follow Us</p><p>브랜드 사이트 방문하기</p></main>")
    text = f.extract_html(html)
    assert "설화수는 전통의 지혜" in text and "주석" not in text and "x=1" not in text and "방문하기" not in text


def test_sources_json_is_valid():
    """수집 목록: id 가 겹치지 않고, 층·브랜드·제품 정보가 등록 도구가 쓰는 형태다. 원문 폴더는 git 에서 제외."""
    from app.knowledge import LAYERS
    doc = json.loads((ROOT / "brands" / "amorepacific" / "sources.json").read_text(encoding="utf-8"))
    ids = [s["id"] for s in doc["sources"]]
    assert len(ids) == len(set(ids))
    for s in doc["sources"]:
        assert s["layer"] in LAYERS and s["kind"] in ("law", "html", "manual")
        if s["layer"] in ("브랜드", "제품"):
            assert (ROOT / "brands" / s["brand"] / "brand.json").exists()
        if s["layer"] == "제품":
            assert s["product_name"] and isinstance(s["functional"], list)
        if s["kind"] == "law":
            assert s.get("law")
    assert "brands/*/raw/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_rag_cases_reference_known_sources():
    import csv
    doc = json.loads((ROOT / "brands" / "amorepacific" / "sources.json").read_text(encoding="utf-8"))
    ids = {s["id"] for s in doc["sources"]}
    cases = list(csv.DictReader((ROOT / "eval" / "rag_cases.csv").open(encoding="utf-8")))
    assert len(cases) == 20
    for c in cases:
        for want in c["expect"].split("|"):
            assert set(want.split("/")) <= ids, f"{c['id']}: 모르는 출처 {want}"
