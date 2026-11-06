"""RAG 지식 저장소(knowledge.py)·수집 도구(fetch_sources.py): 자르기, 재등록 시 보관, 층별 검색과 브랜드 필터, 임베딩 비용.
외부 API 는 호출하지 않는다 — 임베딩은 단어 해시로 만든 가짜 벡터를 쓴다."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
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
def session(pg_engine):
    with sessionmaker(bind=pg_engine)() as s:
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


def test_chunk_annex_names_items():
    from app.knowledge import chunk_annex
    text = ("별표 5 제목\n1. 광고 매체: 가. 신문 나. 방송\n\n2. 준수사항 가. 의약품으로 잘못 인식할 우려가 있는 광고를 하지 말 것\n\n"
            "2. 준수사항 나. 기능성화장품으로 잘못 인식할 우려가 있는 광고를 하지 말 것")
    secs = [sec for sec, _ in chunk_annex(text, "별표 5")]
    assert secs == ["별표 5", "별표 5 제2호 가목", "별표 5 제2호 나목"]


def test_grade_accepts_alternatives_and_sections():
    spec = importlib.util.spec_from_file_location("rag_search", ROOT / "eval" / "rag_search.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    res = {"규제": [{"source_id": "annex5", "section": "별표 5 제2호 가목"}], "제품": [{"source_id": "p1", "section": "p1"}]}
    assert m.grade("p1|law13/annex5#가목", res) == (2, 2, [])
    assert m.grade("annex5#나목", res) == (0, 1, ["annex5#나목"])


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


def test_add_document_embeds_before_writing(session):
    """임베딩 응답을 기다리는 동안 쓰기 잠금을 잡지 않는다: 호출 시점에 세션에 쓴 내용이 없어야 한다."""
    from app import knowledge
    from app.models import KnowledgeDoc
    _add(session, layer="브랜드", source_id="brand_a", title="A", chunks=[("A 1", "옛 소개")], brand_id=1)

    def embedder(texts):
        assert not session.in_transaction() and not session.new and not session.dirty
        return fake_embed(texts)

    knowledge.add_document(session, layer="브랜드", source_id="brand_a", title="A", chunks=[("A 1", "새 소개")],
                           brand_id=1, embedder=embedder)
    assert session.query(KnowledgeDoc).filter_by(source_id="brand_a", status="사용").count() == 1


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
    assert len(cases) == 60, "3단계에서 60건으로 확장(브랜드 4 × 15)"
    for c in cases:
        for want in c["expect"].split("|"):
            assert {a.partition("#")[0] for a in want.split("/")} <= ids, f"{c['id']}: 모르는 출처 {want}"


# ---- 카피 생성에 붙이기 (1-b) ----

def _seed_copy_knowledge(s):
    """규제(화장품) 2문서 · 업종 없는 정의 조문 1 · 브랜드 1 의 제품 2개 · 그룹 자료 1."""
    _add(s, layer="규제", source_id="law13", title="제13조", industry="화장품",
         chunks=[("화장품법 제13조 제1항", "[화장품법 제13조] 의약품으로 잘못 인식할 우려가 있는 광고 금지")])
    _add(s, layer="규제", source_id="annex5", title="별표 5", industry="화장품",
         chunks=[("별표 5 제2호 가목", "[별표 5] 가. 의약품 오인 금지"), ("별표 5 제2호 나목", "[별표 5] 나. 기능성 오인 금지")])
    _add(s, layer="규제", source_id="law2", title="제2조", chunks=[("화장품법 제2조", "[화장품법 제2조] 정의")])
    _add(s, layer="제품", source_id="p_cream", title="수분 크림", chunks=[("수분 크림", "[수분 크림] 세라마이드 보습 크림")],
         brand_id=1, product_name="수분 크림", functional=[])
    _add(s, layer="제품", source_id="p_sun", title="선세럼", chunks=[("선세럼", "[선세럼] 자외선 차단 세럼")],
         brand_id=1, product_name="선세럼", functional=["자외선 차단"])
    _add(s, layer="그룹", source_id="grp", title="그룹", chunks=[("그룹 1", "[그룹] 녹차 보습 연구")], group_id=7)


def test_for_copy_puts_all_industry_law_and_searches_guide(session):
    from app import knowledge
    _seed_copy_knowledge(session)
    calls = []
    emb = lambda texts: calls.append(texts) or fake_embed(texts)
    brief = {"product_name": "수분 크림", "description": "세라마이드 보습 크림", "functional": ["자외선 차단"]}
    k = knowledge.for_copy(session, 1, 7, brief, embedder=emb)
    assert k["industries"] == ["화장품"]
    assert [r["section"] for r in k["law"]] == ["화장품법 제13조 제1항", "별표 5 제2호 가목", "별표 5 제2호 나목"], \
        "업종 표시가 있는 규제는 검색 없이 전부, 순서대로. 업종 표시가 없는 정의 조문은 넣지 않는다"
    assert [r["source_id"] for r in k["guide"]][:1] == ["p_cream"] and {r["layer"] for r in k["guide"]} == {"제품", "그룹"}
    assert len(calls) == 1, "검색 질의 임베딩 1회뿐(규제는 검색하지 않는다)"
    assert "다릅니다" in k["functional_mismatch"], "제품 자료는 기능성 없음인데 자외선 차단을 입력"
    # 기록 → 재구성(임베딩 호출 없음)
    again = knowledge.from_refs(session, knowledge.refs(k))
    assert [r["chunk_id"] for r in again["law"] + again["guide"]] == [r["chunk_id"] for r in k["law"] + k["guide"]]
    assert len(calls) == 1


def test_for_copy_without_knowledge_makes_no_embedding_call(session):
    from app import knowledge
    _seed_copy_knowledge(session)
    def boom(texts):
        raise AssertionError("지식 없는 브랜드는 임베딩을 호출하지 않는다")
    k = knowledge.for_copy(session, 99, None, {"product_name": "캡슐 토이"}, embedder=boom)
    assert k["law"] == [] and k["guide"] == [] and k["error"] is None and k["functional_mismatch"] is None


def test_for_copy_survives_embedding_failure(session):
    from app import knowledge
    _seed_copy_knowledge(session)
    def fail(texts):
        raise RuntimeError("network")
    k = knowledge.for_copy(session, 1, 7, {"product_name": "수분 크림", "description": "크림"}, embedder=fail)
    assert k["error"] == "RuntimeError" and k["guide"] == [] and len(k["law"]) == 3, "검색이 실패해도 규제 원문은 들어간다"


def test_functional_violations():
    from app import legal
    assert legal.functional_violations("주름 개선 크림", None) == [], "입력하지 않은 캠페인은 검사하지 않는다"
    assert legal.functional_violations("주름 개선 크림", ["주름 개선"]) == []
    v = legal.functional_violations("SPF50 으로 주름까지", ["미백"])
    assert {x["phrase"] for x in v} == {"SPF", "주름"} and all(x["severity"] == "필수" for x in v)
    assert legal.functional_violations("촉촉한 보습", []) == []
    assert [x["phrase"] for x in legal.violations("주름 개선 크림")] == ["주름 개선"]
    assert legal.violations("주름 개선 크림", functional=["주름 개선"]) == [], "입력한 기능성의 경고 표현은 알리지 않는다"
    assert "여드름 치료" in [x["phrase"] for x in legal.violations("여드름 치료 크림", functional=["여드름성 피부 완화"])], "금지 표현은 그대로"


def test_brief_functional_validation():
    from pydantic import ValidationError

    from app.routers_designs import BriefIn
    assert BriefIn(product_name="p", functional=["미백", "미백"]).functional == ["미백"]
    assert BriefIn(product_name="p").functional is None
    with pytest.raises(ValidationError):
        BriefIn(product_name="p", functional=["피부 재생"])


def test_copy_check_flags_functional_outside_brief():
    from app import copywriter
    rules = {"forbidden_phrases": [], "required_phrases": [], "levels": {}, "source_text": "", "functional": []}
    v = copywriter.check({"headline": "미백 크림", "body": "본문", "cta": "보기"}, copywriter.limits_for([]), rules)
    assert any(x["type"] == "functional" and x["phrase"] == "미백" for x in v)


def test_alias_matches_product_and_drops_other_products(session):
    """별칭이 브리프 상품명과 같으면 그 제품 자료만 쓴다(비슷한 다른 제품을 섞지 않는다). 기능성 불일치도 잡는다."""
    from app import knowledge
    _add(session, layer="제품", source_id="rich", title="리치", chunks=[("자음생크림 리치", "[리치] 인삼 크림")], brand_id=1,
         product_name="자음생크림 리치", aliases=["자음생크림 60주년"], functional=["주름 개선"])
    _add(session, layer="제품", source_id="yunjo", title="윤조", chunks=[("윤조에센스", "[윤조] 인삼 에센스")], brand_id=1,
         product_name="윤조에센스", functional=["주름 개선"])
    brief = {"product_name": "자음생크림 60 주년", "functional": ["주름 개선", "미백"]}
    k = knowledge.for_copy(session, 1, 7, brief, embedder=fake_embed)
    assert [r["source_id"] for r in k["guide"]] == ["rich"] and k["product_note"] is None
    assert "미백" in k["functional_mismatch"]


def test_unknown_product_name_gives_note(session):
    from app import knowledge
    _add(session, layer="제품", source_id="rich", title="리치", chunks=[("자음생크림 리치", "[리치] 인삼 크림")], brand_id=1,
         product_name="자음생크림 리치")
    k = knowledge.for_copy(session, 1, 7, {"product_name": "새 크림"}, embedder=fake_embed)
    assert "이름이 같은 제품 자료가 없어" in k["product_note"] and k["guide"]


def test_guide_skips_test_numbers_except_product_cards(session):
    """시험 수치가 든 그룹·브랜드 조각은 재료로 고르지 않는다."""
    from app import knowledge
    _add(session, layer="그룹", source_id="clinical", title="임상", chunks=[("임상", "[임상] 인삼 주름 17.99% 감소")], group_id=7)
    _add(session, layer="그룹", source_id="story", title="이야기", chunks=[("이야기", "[이야기] 인삼 연구의 역사")], group_id=7)
    k = knowledge.for_copy(session, None, 7, {"product_name": "인삼"}, embedder=fake_embed)
    assert [r["source_id"] for r in k["guide"]] == ["story"]


def test_visual_knowledge_reaches_background_prompt(session):
    """정확히 맞는 제품의 시각 소재와 브랜드 연출 후보가 배경 프롬프트에 들어간다. 실사가 아닌 형태는 공통 연출을 쓴다."""
    from app import imagegen, knowledge
    _add(session, layer="브랜드", source_id="brand_a", title="A", chunks=[("A 1", "[A] 브랜드 소개")], brand_id=1,
         visual={"directions": ["한지 위 낮은 좌탁, 부드러운 오전 빛"]})
    _add(session, layer="제품", source_id="rich", title="리치", chunks=[("리치", "[리치] 크림")], brand_id=1,
         product_name="리치", visual={"motifs": "인삼 뿌리, 한지 결"})
    k = knowledge.for_copy(session, 1, 7, {"product_name": "리치"}, embedder=fake_embed)
    brief = {"product_name": "리치", "key_message": "메시지", "knowledge": knowledge.refs(k)}
    text = imagegen.build_prompt("브랜드", {}, brief, "정사각", "실사 제품·공간")
    assert "[제품의 소재]" in text and "인삼 뿌리, 한지 결" in text and "한지 위 낮은 좌탁" in text
    assert "청량한 여름" not in text and "김 서린 창가" not in text
    assert imagegen.pick_direction("플랫 일러스트", brief) != "한지 위 낮은 좌탁, 부드러운 오전 빛"
    other = knowledge.for_copy(session, 1, 7, {"product_name": "모르는 크림"}, embedder=fake_embed)
    assert other["motifs"] is None and other["directions"], "다른 제품의 소재를 쓰지 않되 브랜드 연출은 쓴다"
    plain = imagegen.build_prompt("브랜드", {}, {"product_name": "리치"}, "정사각", "실사 제품·공간")
    assert "[제품의 소재]" not in plain


def test_sources_visual_cover_all_products_and_brands():
    doc = json.loads((ROOT / "brands" / "amorepacific" / "sources.json").read_text(encoding="utf-8"))
    assert all(s.get("motifs") for s in doc["sources"] if s["layer"] == "제품")
    assert all(len(s.get("directions", [])) >= 3 for s in doc["sources"] if s["layer"] == "브랜드")


def test_sql_and_python_search_paths_agree(session):
    """2단계: pgvector(sql) 경로와 예전 JSON 코사인(python) 경로가 같은 조각을 같은 순서로 준다.
    제품명 정확 일치가 상위 후보 밖에 있어도 sql 경로가 따로 찾아 맨 앞에 둔다."""
    from app import knowledge
    for i in range(50):      # SQL_CANDIDATES(40) 보다 많은 조각: 상위 후보 자르기가 실제로 일어난다
        _add(session, layer="제품", source_id=f"p{i}", title=f"제품 {i}", chunks=[(f"제품 {i}", f"보습 크림 성분 {i} 촉촉 피부")],
             brand_id=1, product_name=f"제품 {i}")
    _add(session, layer="제품", source_id="far", title="먼 제품", chunks=[("먼 제품", "완전히 다른 이야기 포장재 종이")], brand_id=1,
         product_name="먼 제품")
    _add(session, layer="규제", source_id="law", title="법", chunks=[("제1조", "의약품 오인 광고 금지"), ("제2조", "기능성 미백 주름")])
    kw = dict(brand_id=1, group_id=None, embedder=fake_embed, layer_k={"규제": 2, "제품": 3})
    for name in (None, "먼 제품"):
        a = knowledge.search(session, "보습 크림 촉촉 피부", product_name=name, mode="sql", hybrid=False, **kw)
        b = knowledge.search(session, "보습 크림 촉촉 피부", product_name=name, mode="python", **kw)
        strip = lambda res: {k: [(r["source_id"], r["exact_product"], round(r["score"], 3)) for r in v] for k, v in res.items()}
        assert strip(a) == strip(b), name
    assert a["제품"][0]["source_id"] == "far" and a["제품"][0]["exact_product"], "유사도가 낮은 정확 일치 제품도 맨 앞"


def test_vector_column_backfills_from_json_once(pg_empty_engine, monkeypatch):
    """옛 배포가 남긴 DB(vec 없음, JSON embedding 만): _added_columns 가 vec 를 만들고 채우고 HNSW 인덱스를 만든다. 두 번 돌려도 같다."""
    from sqlalchemy import inspect, text
    from app import db, main, models
    eng = pg_empty_engine
    db.Base.metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(text("ALTER TABLE knowledge_chunks DROP COLUMN vec"))
    import json
    with eng.begin() as c:       # 옛 코드처럼 JSON 만 쓴다(ORM 은 vec 를 알고 있으므로 raw SQL)
        c.execute(text("INSERT INTO knowledge_docs (id, layer, source_id, title, status, created_at) VALUES (1, '규제', 'law', '법', '사용', now())"))
        c.execute(text("INSERT INTO knowledge_chunks (doc_id, seq, section, text, embedding, embed_model, aliases, functional) "
                       "VALUES (1, 1, '제1조', 't', :e, 'm', '[]', '[]')"), {"e": json.dumps(fake_embed(["의약품 오인"])[0])})
    monkeypatch.setattr(main, "engine", eng)
    for _ in range(2):
        main._added_columns()
    insp = inspect(eng)
    assert "vec" in {c["name"] for c in insp.get_columns("knowledge_chunks")}
    assert any(i["name"] == "ix_knowledge_chunks_vec" for i in insp.get_indexes("knowledge_chunks"))
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM knowledge_chunks WHERE vec IS NULL")).scalar() == 0
        assert c.execute(text("SELECT vector_dims(vec) FROM knowledge_chunks")).scalar() == models.EMBED_DIMS


def test_hybrid_finds_substring_match_that_vectors_miss(session):
    """3단계 하이브리드: 질의와 낱말이 하나도 같지 않아 벡터(가짜 임베딩은 낱말 해시)로는 0점인 조각도, 글자가 겹치면(pg_trgm)
    후보에 들어온다. 벡터만으로는 다른 조각이 앞서고, 하이브리드면 '최고' 조각이 앞선다."""
    from app import knowledge
    _add(session, layer="규제", source_id="annex", title="별표", chunks=[
        ("바목", "경쟁상품과 비교하는 광고는 배타성을 띤 최고 또는 최상 등의 절대적 표현을 하지 말 것"),
        ("자목", "저속하거나 혐오감을 주는 표현 도안 사진 등을 이용하는 광고를 하지 말 것")])
    q = "최고의 탄력을 주는 크림 광고를 하지 말 것"      # '광고를' '하지' '말' '것' 은 두 조각 모두에, '최고의' 는 어느 쪽에도 없다
    plain = knowledge.search(session, q, brand_id=None, group_id=None, layer_k={"규제": 1}, embedder=fake_embed, hybrid=False)
    hybrid = knowledge.search(session, q, brand_id=None, group_id=None, layer_k={"규제": 1}, embedder=fake_embed, hybrid=True)
    assert plain["규제"][0]["section"] == "자목", "낱말만 보면 두 조각이 동점이라 먼저 등록된 쪽… 이 아니라 공유 낱말 수가 같은 자목"
    assert hybrid["규제"][0]["section"] == "바목"
    assert 0 < hybrid["규제"][0]["score"] < 0.05, "RRF 점수는 1/(K+순위) 의 합"


def test_search_filters_law_by_industry_keeping_untagged(session):
    """3-B: 규제가 여러 업종으로 늘면 업종 필터로 거른다. 업종 없는 문서(정의 조문)는 어느 업종에도 들어간다."""
    from app import knowledge
    _add(session, layer="규제", source_id="cos", title="화장품법", industry="화장품", chunks=[("제13조", "의약품 오인 광고 금지 표시")])
    _add(session, layer="규제", source_id="food", title="식품표시광고법", industry="건강기능식품", chunks=[("제8조", "질병 예방 치료 효능 표시 광고 금지")])
    _add(session, layer="규제", source_id="def", title="정의", chunks=[("제2조", "화장품이란 인체를 청결 미화 광고 표시")])
    kw = dict(brand_id=None, group_id=None, layer_k={"규제": 5}, embedder=fake_embed)
    for mode in ("sql", "python"):
        got = {r["source_id"] for r in knowledge.search(session, "광고 표시 금지", industries=["화장품"], mode=mode, hybrid=False, **kw)["규제"]}
        assert got == {"cos", "def"}, mode
    assert {r["source_id"] for r in knowledge.search(session, "광고 표시 금지", hybrid=False, **kw)["규제"]} == {"cos", "food", "def"}, "업종을 안 주면 전부"


def test_for_copy_switches_law_to_search_when_many(session, monkeypatch):
    """규제 조각이 LAW_ALL_MAX 이하면 전부 넣고(검색 없음), 넘으면 업종 필터 + 검색 상위 LAW_K 만 넣는다."""
    from app import knowledge
    for i in range(6):
        _add(session, layer="규제", source_id=f"cos{i}", title=f"화장품법 {i}", industry="화장품",
             chunks=[(f"제{i}조", "의약품 오인 광고 금지" if i == 0 else f"영업 등록 신고 서류 {i}")])
    brief = {"product_name": "크림", "description": "아토피 치료 화장품 광고", "key_message": "의약품 오인"}
    monkeypatch.setattr(knowledge, "LAW_ALL_MAX", 10)
    k = knowledge.for_copy(session, None, 7, brief, embedder=fake_embed)
    assert k["law_mode"] == "all" and len(k["law"]) == 6 and k["law_total"] == 6
    monkeypatch.setattr(knowledge, "LAW_ALL_MAX", 3)
    monkeypatch.setattr(knowledge, "LAW_K", 2)
    k = knowledge.for_copy(session, None, 7, brief, embedder=fake_embed)
    assert k["law_mode"] == "core+search" and k["law_core"] == 0 and len(k["law"]) == 2 and k["law"][0]["source_id"] == "cos0", "핵심 규제가 없으면 검색 상위만"
    assert knowledge.refs(k)["law_mode"] == "core+search"


def test_core_law_always_injected_when_many(session, monkeypatch):
    """S4: 규제 조각이 많아 검색 모드여도 핵심 규제(CORE_LAW)는 유사도와 무관하게 전부 들어가고, 검색은 핵심 밖에서만 보충한다."""
    from app import knowledge
    monkeypatch.setattr(knowledge, "CORE_LAW", {"화장품": ("law13", "annex5")})
    _add(session, layer="규제", source_id="law13", title="제13조", industry="화장품", chunks=[("제13조", "포장재 종이 재활용 보고")])   # 브리프와 멀다
    _add(session, layer="규제", source_id="annex5", title="별표 5", industry="화장품", chunks=[("가목", "창고 등록 서류"), ("나목", "영업 신고 양식")])
    for i in range(8):
        _add(session, layer="규제", source_id=f"other{i}", title=f"기타 {i}", industry="화장품",
             chunks=[(f"제{i}조", "의약품 오인 광고 금지 치료 표현" if i < 3 else f"수입 통관 서류 {i}")])
    brief = {"product_name": "크림", "description": "아토피 치료 화장품 광고 의약품 오인"}
    monkeypatch.setattr(knowledge, "LAW_ALL_MAX", 3)
    monkeypatch.setattr(knowledge, "LAW_K", 2)
    k = knowledge.for_copy(session, None, 7, brief, embedder=fake_embed)
    srcs = [r["source_id"] for r in k["law"]]
    assert k["law_mode"] == "core+search" and k["law_core"] == 3
    assert srcs[:3] == ["law13", "annex5", "annex5"], "핵심 규제가 유사도와 무관하게 맨 앞에 전부"
    assert len(srcs) == 5 and all(x.startswith("other") for x in srcs[3:]), "검색 상위 2개는 핵심 밖에서"
    assert knowledge.refs(k)["law_core"] == 3


def test_control_brand_without_group_gets_no_knowledge_at_all(session):
    """S3 대조군(2026-11-05 결정): 그룹 없는 타 브랜드는 규제를 포함해 어떤 지식도 받지 않는다. 임베딩 호출도 없다."""
    from app import knowledge
    _add(session, layer="규제", source_id="law13", title="제13조", industry="화장품", chunks=[("제13조", "의약품 오인 광고 금지")])
    _add(session, layer="제품", source_id="p", title="크림", chunks=[("크림", "보습 크림")], brand_id=1, product_name="크림")
    def boom(_): raise AssertionError("대조군은 검색하지 않는다")
    k = knowledge.for_copy(session, 1, None, {"product_name": "크림", "description": "아토피 치료 화장품"}, embedder=boom)
    assert k["law"] == [] and k["guide"] == [] and k["law_mode"] == "off" and "화장품" in k["industries"]
    g = knowledge.for_copy(session, 1, 7, {"product_name": "크림", "description": "아토피 치료 화장품"}, embedder=fake_embed)
    assert g["law"] and g["law_mode"] == "all", "같은 브랜드라도 그룹이 있으면 규제가 들어간다"


def _trend_doc(session):
    """이번 주 트렌드 문서(요약 1 + 키워드 3)."""
    from app import knowledge
    return knowledge.add_document(session, layer="트렌드", source_id="trend_2026-W45", title="화장품 검색 트렌드 2026-11-06 기준 주",
                                  chunks=[("요약", "[화장품 검색 트렌드 요약] 네이버 검색 상승 상위 3개: 수분크림(+27%), 핸드크림(+58%), 보습(+22%)"),
                                          ("수분크림", "[화장품 검색 트렌드] '수분크림' (품목 키워드) — 네이버 검색 관심 +27%, 쇼핑 클릭 -19%"),
                                          ("핸드크림", "[화장품 검색 트렌드] '핸드크림' (품목 키워드) — 네이버 검색 관심 +58%, 쇼핑 클릭 +35%"),
                                          ("보습", "[화장품 검색 트렌드] '보습' (효능·상황 키워드) — 네이버 검색 관심 +22%, 쇼핑 클릭 +92%")],
                                  industry="화장품", embedder=fake_embed,
                                  visual={"rows": {"수분크림": {"group": "품목", "naver": 0.27, "shopping": -0.19},
                                                   "핸드크림": {"group": "품목", "naver": 0.58, "shopping": 0.35},
                                                   "보습": {"group": "효능·상황", "naver": 0.22, "shopping": 0.92}}})


def test_pick_trend_drops_brief_keywords_and_prefers_situation_and_shopping():
    from app import knowledge
    rows = [{"section": "요약", "score": 0.9}, {"section": "수분크림", "score": 0.8}, {"section": "핸드크림", "score": 0.5},
            {"section": "보습", "score": 0.4}, {"section": "바쿠치올", "score": 0.3}, {"section": "아이크림", "score": 0.6}]
    metas = {"수분크림": {"group": "품목", "shopping": -0.19}, "핸드크림": {"group": "품목", "shopping": 0.35},
             "보습": {"group": "효능·상황", "shopping": 0.92}, "바쿠치올": {"group": "성분", "shopping": 0.36}, "아이크림": {"group": "품목", "shopping": -0.09}}
    brief = {"product_name": "아토베리어365 하이드로 수분 크림", "description": "장벽 캡슐크림", "key_message": "속수분"}
    out = knowledge.pick_trend(rows, brief, metas, k=3)
    assert [r["section"] for r in out] == ["보습", "바쿠치올", "핸드크림"]      # 요약·브리프에 있는 '수분크림' 제외, 효능 > 성분 > 품목(쇼핑 상승 우선)
    assert out[0]["why"] == "효능·상황 · 쇼핑 클릭도 +92%" and out[2]["why"] == "품목 · 쇼핑 클릭도 +35%"
    assert knowledge.pick_trend(rows, brief, metas, k=1)[0]["section"] == "보습"
    assert knowledge.pick_trend([{"section": "수분크림", "score": 0.8}], brief, metas) == []     # 억지로 채우지 않는다


def test_for_copy_adds_trend_chunks_without_summary_and_records_refs(session, monkeypatch):
    from app import copywriter, knowledge
    _add(session, layer="브랜드", source_id="brand_x", title="브랜드 소개", chunks=[("소개", "민감 피부를 위한 더마 브랜드")], group_id=7, brand_id=1)
    _trend_doc(session)
    monkeypatch.setattr(knowledge, "COPY_TREND", True)
    brief = {"product_name": "수분크림", "description": "장벽 캡슐 크림", "key_message": "속건조"}   # 브리프에 없는 효능·상황(보습)이 올라와야 한다
    k = knowledge.for_copy(session, 1, 7, brief, embedder=fake_embed)
    assert k["trend_mode"] == "on" and 1 <= len(k["trend"]) <= knowledge.TREND_K
    assert all(r["layer"] == "트렌드" and r["section"] != "요약" for r in k["trend"])
    assert "수분크림" not in [r["section"] for r in k["trend"]] and k["trend"][0]["section"] == "보습" and k["trend"][0]["why"].startswith("효능·상황")   # 브리프에 있는 품목은 빠지고 효능·상황이 앞
    # 프롬프트에 문단이 붙고 규제·브랜드 문단 뒤에 온다
    v = copywriter.knowledge_vars(k, brief)
    assert "[이번 주 검색 트렌드 — 참고]" in v["trend"] and "'보습'" in v["trend"] and "'수분크림'" not in v["trend"]
    _, user = copywriter._prompt("에스트라", {"voice": {}, "required_phrases": [], "forbidden_phrases": []}, brief,
                                 {"platform": "Meta", "name": "Instagram 피드"}, copywriter.limits_for([]), k)
    assert user.index("[이번 주 검색 트렌드") > user.index("[브랜드 지식")
    # refs → from_refs 로 되살아난다
    refs = knowledge.refs(k)
    assert refs["trend_mode"] == "on" and [t["section"] for t in refs["trend"]] == [r["section"] for r in k["trend"]]
    back = knowledge.from_refs(session, refs)
    assert [r["chunk_id"] for r in back["trend"]] == [r["chunk_id"] for r in k["trend"]] and back["trend_mode"] == "on" and back["trend"][0]["why"] == k["trend"][0]["why"]


def test_for_copy_trend_is_off_for_control_brand_brief_switch_and_env(session, monkeypatch):
    from app import knowledge
    _trend_doc(session)
    monkeypatch.setattr(knowledge, "COPY_TREND", True)
    brief = {"product_name": "수분크림", "description": "보습"}
    assert knowledge.for_copy(session, 99, None, brief, embedder=fake_embed)["trend_mode"] == "off"          # 대조군: 지식 전부 없음
    k = knowledge.for_copy(session, 1, 7, {**brief, "use_trend": False}, embedder=fake_embed)
    assert k["trend_mode"] == "off" and k["trend"] == []                                                        # 브리프 스위치
    monkeypatch.setattr(knowledge, "COPY_TREND", False)
    assert knowledge.for_copy(session, 1, 7, brief, embedder=fake_embed)["trend_mode"] == "off"                # 환경변수
