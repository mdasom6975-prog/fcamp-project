"""화장품 검색 트렌드 수집 → '트렌드' 지식 층. 네이버 데이터랩(통합검색어 트렌드·쇼핑인사이트)이 주, 구글 트렌드(pytrends, 비공식)는 보조.
- 데이터랩은 인기 검색어 목록을 주지 않는다: prompts/trend/keywords.json 의 시드 키워드(5개씩 묶어 호출)의 상대 추이만 온다
- 상승률 = 최근 RECENT_WEEKS 평균 / 이전 PRIOR_WEEKS 평균 - 1. 네이버 검색 상승률로 상위 TOP_N 을 고르고, 쇼핑 관심의 연령·성별 우세를 붙인다
- 구글은 429 차단이 잦아 실패하면 건너뛴다(조각에 '구글 -' 로 남김)
- 한 주 = 문서 하나(source_id trend_<ISO 주>), 조각 = 요약 1 + 키워드별 1. 등록하면 이전 주 트렌드 문서는 '보관'
필요: backend/.env 의 NAVER_CLIENT_ID · NAVER_CLIENT_SECRET (개발자센터 > 데이터랩 검색어 트렌드·쇼핑인사이트)."""
import json
import os
from datetime import date, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .db import BASE_DIR

load_dotenv(BASE_DIR / ".env")

KEYWORDS_FILE = BASE_DIR.parent / "prompts" / "trend" / "keywords.json"
# 네이버 데이터랩은 두 곳에서 열린다. 개발자센터(developers.naver.com: X-Naver-Client-Id, 시크릿 10자) 와
# 네이버 클라우드 플랫폼 API Hub(ncp: X-NCP-APIGW-API-KEY-ID, 키 40자). NAVER_API=developers|ncp, 비우면 시크릿 길이로 고른다
# 개발자센터는 신규 신청을 받지 않고(기존 키는 2027-06-30 까지), 신규 키는 NAVER API HUB(ncp)에서 Search Trend·Shopping Insight 를 따로 구독한다
NAVER_BASES = {"developers": "https://openapi.naver.com/v1/datalab", "ncp": "https://naverapihub.apigw.ntruss.com"}
NAVER_PATHS = {   # 경로 이름 → (개발자센터, API HUB)
    "search": ("/search", "/search-trend/v1/search"),
    "shopping": ("/shopping/category/keywords", "/shopping/v1/category/keywords"),
    "age": ("/shopping/category/keyword/age", "/shopping/v1/category/keyword/age"),
    "gender": ("/shopping/category/keyword/gender", "/shopping/v1/category/keyword/gender"),
}
RECENT_WEEKS, PRIOR_WEEKS = 4, 8
TOP_N = 15
BATCH = 5                      # 데이터랩 keywordGroups 한도
AGES = {"10": "10대", "20": "20대", "30": "30대", "40": "40대", "50": "50대", "60": "60대 이상"}


def load_keywords(path: Path = KEYWORDS_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def all_keywords(cfg: dict) -> list[tuple[str, str]]:
    """[(키워드, 묶음 이름)] 순서 보존."""
    return [(k, g) for g, ks in cfg["groups"].items() for k in ks]


def _batches(items: list, n: int = BATCH):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def period(today: date | None = None) -> tuple[str, str]:
    """최근 RECENT+PRIOR 주를 덮는 시작·끝 날짜(끝은 어제: 데이터랩은 당일 자료가 없다)."""
    end = (today or date.today()) - timedelta(days=1)
    start = end - timedelta(weeks=RECENT_WEEKS + PRIOR_WEEKS)
    return start.isoformat(), end.isoformat()


def rise(series: list[float], recent: int = RECENT_WEEKS, prior: int = PRIOR_WEEKS) -> float | None:
    """주간 비율 목록에서 상승률. 자료가 부족하거나 이전 평균이 0 이면 None."""
    if len(series) < recent + 1:
        return None
    r = series[-recent:]
    p = series[-(recent + prior):-recent] or series[:-recent]
    if not p:
        return None
    pm, rm = sum(p) / len(p), sum(r) / len(r)
    if pm <= 0:
        return None
    return rm / pm - 1


# ── 네이버 데이터랩 ──────────────────────────────────────────────────────
def naver_mode() -> str:
    m = os.getenv("NAVER_API", "").strip().lower()
    if m in NAVER_BASES:
        return m
    return "ncp" if len(os.getenv("NAVER_CLIENT_SECRET", "").strip()) > 20 else "developers"


def naver_headers() -> dict:
    cid, sec = os.getenv("NAVER_CLIENT_ID", "").strip(), os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if not cid or not sec:
        raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 이 backend/.env 에 없습니다")
    if naver_mode() == "ncp":
        return {"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": sec, "Content-Type": "application/json"}
    return {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec, "Content-Type": "application/json"}


def _post(client: httpx.Client, name: str, body: dict) -> dict:
    mode = naver_mode()
    path = NAVER_PATHS[name][1 if mode == "ncp" else 0]
    r = client.post(f"{NAVER_BASES[mode]}{path}", headers=naver_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def naver_search_series(client: httpx.Client, keywords: list[str], start: str, end: str) -> dict[str, list[float]]:
    """통합검색어 트렌드: 키워드별 주간 상대 검색량(요청 안 최대 100)."""
    out: dict[str, list[float]] = {}
    for batch in _batches(keywords):
        body = {"startDate": start, "endDate": end, "timeUnit": "week",
                "keywordGroups": [{"groupName": k, "keywords": [k]} for k in batch]}
        for res in _post(client, "search", body).get("results", []):
            out[res["title"]] = [float(d["ratio"]) for d in res.get("data", [])]
    return out


def naver_shopping_series(client: httpx.Client, keywords: list[str], start: str, end: str, category: str) -> dict[str, list[float]]:
    """쇼핑인사이트 키워드 트렌드(화장품/미용 카테고리 안 클릭 관심)."""
    out: dict[str, list[float]] = {}
    for batch in _batches(keywords):
        body = {"startDate": start, "endDate": end, "timeUnit": "week", "category": category,
                "keyword": [{"name": k, "param": [k]} for k in batch]}
        for res in _post(client, "shopping", body).get("results", []):
            out[res["title"]] = [float(d["ratio"]) for d in res.get("data", [])]
    return out


def naver_shopping_profile(client: httpx.Client, keyword: str, start: str, end: str, category: str) -> dict:
    """쇼핑 관심의 연령·성별 분포(최근 기간 합). 반환 {age: {'20대': 41.0, …}, gender: {'여성': 78.0, '남성': 22.0}} (비율 %)."""
    base = {"startDate": start, "endDate": end, "timeUnit": "month", "category": category, "keyword": keyword}
    prof: dict = {"age": {}, "gender": {}}
    age = _post(client, "age", {**base, "ages": list(AGES)})
    tot: dict[str, float] = {}
    for res in age.get("results", []):
        for d in res.get("data", []):
            tot[d["group"]] = tot.get(d["group"], 0.0) + float(d["ratio"])
    s = sum(tot.values())
    if s > 0:
        prof["age"] = {AGES.get(g, g): round(v / s * 100, 1) for g, v in tot.items()}
    gen = _post(client, "gender", {**base, "gender": "f"})
    gen_m = _post(client, "gender", {**base, "gender": "m"})
    f = sum(float(d["ratio"]) for res in gen.get("results", []) for d in res.get("data", []))
    m = sum(float(d["ratio"]) for res in gen_m.get("results", []) for d in res.get("data", []))
    if f + m > 0:
        prof["gender"] = {"여성": round(f / (f + m) * 100, 1), "남성": round(m / (f + m) * 100, 1)}
    return prof


# ── 구글 트렌드(비공식) ──────────────────────────────────────────────────
def google_series(keywords: list[str], weeks: int = RECENT_WEEKS + PRIOR_WEEKS) -> dict[str, list[float]]:
    """pytrends 로 한국 주간 관심도. 차단(429)·오류는 호출자가 잡는다."""
    from pytrends.request import TrendReq
    tr = TrendReq(hl="ko-KR", tz=540, timeout=(10, 25))   # retries>0 은 urllib3 2 에서 깨진다(method_whitelist)
    out: dict[str, list[float]] = {}
    for batch in _batches(keywords):
        tr.build_payload(batch, timeframe=f"today {max(1, round(weeks / 4.3))}-m", geo="KR")
        df = tr.interest_over_time()
        for k in batch:
            if k in df.columns:
                out[k] = [float(v) for v in df[k].tolist()]
    return out


# ── 집계 → 조각 ──────────────────────────────────────────────────────────
def rank(search: dict[str, list[float]], shopping: dict[str, list[float]], google: dict[str, list[float]] | None,
         groups: dict[str, str], top_n: int = TOP_N) -> list[dict]:
    """키워드별 상승률을 모아 네이버 검색 상승률이 양수인 것만 그 순으로 상위 top_n."""
    rows = []
    for k, grp in groups.items():
        s = rise(search.get(k, []))
        if s is None or s <= 0:      # 뜨는 키워드만: 자료 부족·보합·하락은 뺀다
            continue
        rows.append({"keyword": k, "group": grp, "naver_search": s, "naver_shopping": rise(shopping.get(k, [])),
                     "google": rise((google or {}).get(k, [])) if google is not None else None,
                     "recent_ratio": sum(search[k][-RECENT_WEEKS:]) / RECENT_WEEKS})
    rows.sort(key=lambda r: (r["naver_search"], r["recent_ratio"]), reverse=True)
    return rows[:top_n]


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:+.0f}%"


def chunk_text(week_label: str, row: dict) -> str:
    """키워드 조각 한 문단. 캡션·기사 원문이 아니라 수치 사실만."""
    parts = [f"[화장품 검색 트렌드 {week_label}] '{row['keyword']}' ({row['group']} 키워드) — 네이버 검색 관심이 최근 {RECENT_WEEKS}주 평균 기준 이전 {PRIOR_WEEKS}주보다 {_pct(row['naver_search'])}"]
    if row.get("naver_shopping") is not None:
        parts.append(f", 네이버 쇼핑(화장품/미용) 클릭 관심 {_pct(row['naver_shopping'])}")
    if row.get("google") is not None:
        parts.append(f", 구글 검색 관심 {_pct(row['google'])}")
    prof = row.get("profile") or {}
    if prof.get("age"):
        top_age = max(prof["age"].items(), key=lambda x: x[1])
        parts.append(f". 쇼핑 관심은 {top_age[0]}({top_age[1]:.0f}%)")
        if prof.get("gender"):
            tg = max(prof["gender"].items(), key=lambda x: x[1])
            parts.append(f"·{tg[0]}({tg[1]:.0f}%) 우세")
    return "".join(parts) + "."


def summary_text(week_label: str, rows: list[dict]) -> str:
    names = ", ".join(f"{r['keyword']}({_pct(r['naver_search'])})" for r in rows)
    return f"[화장품 검색 트렌드 {week_label} 요약] 네이버 검색 상승 상위 {len(rows)}개: {names}. 출처: 네이버 데이터랩 통합검색어·쇼핑인사이트" + \
           (", 구글 트렌드" if any(r.get("google") is not None for r in rows) else "") + "."


def week_label(today: date | None = None) -> tuple[str, str]:
    """(source_id 용 ISO 주 'trend_2026-W45', 사람용 '2026-11-06 기준 주')"""
    d = today or date.today()
    y, w, _ = d.isocalendar()
    return f"trend_{y}-W{w:02d}", f"{d.isoformat()} 기준 주"


def collect(client: httpx.Client, cfg: dict, today: date | None = None, use_google: bool = True, profiles: bool = True) -> dict:
    """전부 모아 {rows, google_error, period, source_id, label}. 네트워크는 client(네이버)와 pytrends(구글)."""
    kws = all_keywords(cfg)
    names = [k for k, _ in kws]
    groups = dict(kws)
    start, end = period(today)
    search = naver_search_series(client, names, start, end)
    shopping, shop_err = {}, None
    try:
        shopping = naver_shopping_series(client, names, start, end, cfg["naver_shopping_category"])
    except httpx.HTTPStatusError as e:      # NCP API Hub 에는 쇼핑인사이트가 없을 수 있다(404/403) → 검색 추이만으로 간다
        shop_err = f"HTTP {e.response.status_code}"
        profiles = False
    google, gerr = None, None
    if use_google:
        try:
            google = google_series(names)
        except Exception as e:      # 비공식 API: 차단·형식 변경은 건너뛴다
            gerr = f"{type(e).__name__}"
    rows = rank(search, shopping, google, groups)
    if profiles:
        for r in rows:
            try:
                r["profile"] = naver_shopping_profile(client, r["keyword"], start, end, cfg["naver_shopping_category"])
            except Exception as e:
                r["profile_error"] = type(e).__name__
    sid, label = week_label(today)
    return {"rows": rows, "google_error": gerr, "shopping_error": shop_err, "naver_api": naver_mode(), "period": [start, end],
            "source_id": sid, "label": label, "keywords": len(names)}


def register(db, result: dict, embedder=None) -> tuple[int, int]:
    """결과를 '트렌드' 층 문서 하나로 등록하고 이전 주 트렌드 문서를 보관한다. 반환 (문서 id, 조각 수)."""
    from sqlalchemy import select

    from . import knowledge
    from .models import KnowledgeDoc
    rows, label = result["rows"], result["label"]
    chunks = [("요약", summary_text(label, rows))] + [(r["keyword"], chunk_text(label, r)) for r in rows]
    for old in db.scalars(select(KnowledgeDoc).where(KnowledgeDoc.layer == "트렌드", KnowledgeDoc.status == "사용")):
        old.status = "보관"
    doc = knowledge.add_document(db, layer="트렌드", source_id=result["source_id"], title=f"화장품 검색 트렌드 {label}", chunks=chunks,
                                 source_url="https://datalab.naver.com/", fetched_at=result["period"][1], industry="화장품",
                                 visual={"period": result["period"], "google": result["google_error"] is None, "top": [r["keyword"] for r in rows]},
                                 embedder=embedder or knowledge.embed)
    db.commit()
    return doc.id, len(chunks)
