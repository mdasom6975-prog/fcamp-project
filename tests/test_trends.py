"""화장품 검색 트렌드 수집(app/trends.py): 상승률 계산, 상위 선정, 조각 문장, 네이버 가짜 응답으로 수집, 구글 실패 허용, '트렌드' 층 등록·보관."""
import json
from datetime import date

import httpx
import pytest
from sqlalchemy import select

from app import knowledge, models, trends
from tests.test_knowledge import fake_embed, session  # noqa: F401  (픽스처)

CFG = {"version": 1, "naver_shopping_category": "50000002",
       "groups": {"품목": ["수분크림", "선크림", "토너"], "성분": ["시카", "레티놀", "히알루론산", "세라마이드"]}}
# 키워드별 주간 비율(12주): 수분크림은 최근 4주가 뛰고, 선크림은 내려가고, 토너는 자료 부족
SERIES = {"수분크림": [10] * 8 + [16, 15, 14, 15], "선크림": [40] * 8 + [20, 20, 20, 20], "토너": [5] * 3,
          "시카": [20] * 8 + [24] * 4, "레티놀": [0] * 12, "히알루론산": [30] * 12, "세라마이드": [10] * 8 + [12] * 4}


def _naver(request: httpx.Request) -> httpx.Response:
    assert request.headers["X-Naver-Client-Id"] == "cid"
    body = json.loads(request.content)
    path = request.url.path
    if path.endswith("/datalab/search"):
        res = [{"title": g["groupName"], "keywords": g["keywords"], "data": [{"period": f"w{i}", "ratio": v} for i, v in enumerate(SERIES[g["groupName"]])]}
               for g in body["keywordGroups"]]
        return httpx.Response(200, json={"results": res})
    if path.endswith("/category/keywords"):
        res = [{"title": k["name"], "data": [{"period": f"w{i}", "ratio": v * 0.5} for i, v in enumerate(SERIES[k["name"]])]} for k in body["keyword"]]
        return httpx.Response(200, json={"results": res})
    if path.endswith("/keyword/age"):
        return httpx.Response(200, json={"results": [{"data": [{"period": "m", "group": "20", "ratio": 41}, {"period": "m", "group": "30", "ratio": 35}, {"period": "m", "group": "40", "ratio": 24}]}]})
    if path.endswith("/keyword/gender"):
        r = 78 if body["gender"] == "f" else 22
        return httpx.Response(200, json={"results": [{"data": [{"period": "m", "group": body["gender"], "ratio": r}]}]})
    return httpx.Response(404)


@pytest.fixture
def naver(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "cid")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "sec")
    return httpx.Client(transport=httpx.MockTransport(_naver))


def test_rise_recent_over_prior():
    assert trends.rise([10] * 8 + [15] * 4) == pytest.approx(0.5)
    assert trends.rise([40] * 8 + [20] * 4) == pytest.approx(-0.5)
    assert trends.rise([5, 5, 5]) is None and trends.rise([0] * 12) is None       # 자료 부족 · 이전 평균 0


def test_rank_orders_by_naver_rise_and_keeps_group():
    rows = trends.rank(SERIES, {k: [v * 0.5 for v in s] for k, s in SERIES.items()}, None, dict(trends.all_keywords(CFG)), top_n=3)
    assert [r["keyword"] for r in rows] == ["수분크림", "시카", "세라마이드"]      # 동률(+20%)은 최근 비율이 큰 쪽이 앞
    assert rows[0]["group"] == "품목" and rows[0]["naver_search"] == pytest.approx(0.5) and rows[0]["naver_shopping"] == pytest.approx(0.5)
    assert rows[0]["google"] is None       # 구글 자료 없음(None) 은 조각에서 빠진다
    assert len(trends.rank(SERIES, {}, None, dict(trends.all_keywords(CFG)))) == 3      # 보합(히알루론산)·하락(선크림)·자료 부족(토너)·0(레티놀)은 뺀다


def test_chunk_text_states_numbers_only():
    row = {"keyword": "수분크림", "group": "품목", "naver_search": 0.42, "naver_shopping": 0.3, "google": 0.18}
    t = trends.chunk_text("2026-11-06 기준 주", row)
    assert t.startswith("[화장품 검색 트렌드 2026-11-06 기준 주] '수분크림' (품목 키워드)")
    assert "+42%" in t and "쇼핑(화장품/미용) 클릭 관심 +30%" in t and "구글 검색 관심 +18%" in t and "구매 직전에 찾는 키워드" in t
    t2 = trends.chunk_text("w", {**row, "google": None, "naver_shopping": -0.1})
    assert "구글" not in t2 and "정보를 찾는 단계의 키워드" in t2 and "구매 직전" not in t2
    assert "단계" not in trends.chunk_text("w", {**row, "naver_shopping": None}), "쇼핑 자료가 없으면 해석을 붙이지 않는다"


def test_collect_with_fake_naver_and_google_failure(naver, monkeypatch):
    def boom(keywords, weeks=12):
        raise RuntimeError("429")
    monkeypatch.setattr(trends, "google_series", boom)
    r = trends.collect(naver, CFG, today=date(2026, 11, 6))
    assert r["period"] == ["2026-08-13", "2026-11-05"] and r["source_id"] == "trend_2026-W45" and r["keywords"] == 7
    assert r["google_error"] == "RuntimeError" and [x["keyword"] for x in r["rows"]][:2] == ["수분크림", "시카"]
    assert all(x["google"] is None for x in r["rows"])
    assert r["rows"][0]["profile"]["age"]["20대"] == 41.0 and r["rows"][0]["profile"]["gender"]["여성"] == 78.0
    # 구글이 되면 상승률이 붙는다
    monkeypatch.setattr(trends, "google_series", lambda keywords, weeks=12: {"수분크림": [50] * 8 + [60] * 4})
    r2 = trends.collect(naver, CFG, today=date(2026, 11, 6), profiles=False)
    assert r2["google_error"] is None and r2["rows"][0]["google"] == pytest.approx(0.2) and "profile" not in r2["rows"][0]


def test_register_makes_trend_layer_doc_and_archives_previous(session, naver, monkeypatch):
    monkeypatch.setattr(trends, "google_series", lambda *a, **k: {})
    r1 = trends.collect(naver, CFG, today=date(2026, 10, 30), profiles=False)
    d1, n1 = trends.register(session, r1, embedder=fake_embed)
    r2 = trends.collect(naver, CFG, today=date(2026, 11, 6), profiles=False)
    d2, n2 = trends.register(session, r2, embedder=fake_embed)
    docs = {d.id: d for d in session.scalars(select(models.KnowledgeDoc).where(models.KnowledgeDoc.layer == "트렌드"))}
    assert docs[d1].status == "보관" and docs[d2].status == "사용" and docs[d2].source_id == "trend_2026-W45"
    assert docs[d2].industry == "화장품" and docs[d2].visual["top"][0] == "수분크림" and docs[d2].visual["google"] is True
    chunks = list(session.scalars(select(models.KnowledgeChunk).where(models.KnowledgeChunk.doc_id == d2).order_by(models.KnowledgeChunk.seq)))
    assert n2 == len(chunks) == 1 + 3 and chunks[0].section == "요약" and "상승 상위 3개" in chunks[0].text
    assert chunks[1].section == "수분크림" and chunks[1].vec is not None
    assert "트렌드" in knowledge.LAYERS


def test_keywords_file_has_groups_and_unique_entries():
    cfg = trends.load_keywords()
    kws = [k for k, _ in trends.all_keywords(cfg)]
    assert set(cfg["groups"]) == {"품목", "성분", "효능·상황", "무드·질감", "루틴·상황", "주의"} and len(kws) == len(set(kws)) >= 50 and cfg["naver_shopping_category"] == "50000002"


def test_naver_mode_picks_api_hub_by_secret_length_or_env(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "x"); monkeypatch.setenv("NAVER_CLIENT_SECRET", "s" * 40); monkeypatch.delenv("NAVER_API", raising=False)
    assert trends.naver_mode() == "ncp" and "X-NCP-APIGW-API-KEY-ID" in trends.naver_headers()
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "s" * 10)
    assert trends.naver_mode() == "developers" and "X-Naver-Client-Id" in trends.naver_headers()
    monkeypatch.setenv("NAVER_API", "ncp")
    assert trends.naver_mode() == "ncp"
    assert trends.NAVER_PATHS["shopping"][1] == "/shopping/v1/category/keywords" and trends.NAVER_BASES["ncp"].startswith("https://naverapihub.")


def test_collect_skips_shopping_when_api_hub_has_no_subscription(monkeypatch):
    """쇼핑인사이트가 404/401 이면 검색 추이만으로 간다(연령·성별 없음)."""
    monkeypatch.setenv("NAVER_CLIENT_ID", "cid"); monkeypatch.setenv("NAVER_CLIENT_SECRET", "sec")
    def handler(request):
        if request.url.path.endswith("/search"):
            return _naver(request)
        return httpx.Response(404, json={"error": {"errorCode": "300"}})
    monkeypatch.setattr(trends, "google_series", lambda *a, **k: {})
    r = trends.collect(httpx.Client(transport=httpx.MockTransport(handler)), CFG, today=date(2026, 11, 6))
    assert r["shopping_error"] == "HTTP 404" and r["rows"][0]["naver_shopping"] is None and "profile" not in r["rows"][0]
