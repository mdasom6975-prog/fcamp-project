"""RAG 검색 품질 평가 (설계 16 · 6절, 1-a 수용 기준). 생성 없이 검색 결과만 채점한다.

실행: backend/.venv/bin/python eval/rag_search.py [--cases eval/rag_cases.csv] [--show R07]
- 먼저 tools/register_knowledge.py 로 지식을 등록해 둔다(같은 DB: backend/.env 의 DATABASE_URL)
- 각 건의 브리프로 층별 검색(knowledge.LAYER_K)을 하고, expect 의 출처 id 가 결과에 있는지 본다
  · expect 는 '|' 로 나열, 'a/b' 는 둘 중 하나면 정답, '출처#글자' 는 절 이름까지 맞아야 정답(예: 별표의 특정 목)
- 지표: 정답 포함률(Recall) — 기대한 출처 중 나온 비율, 다른 브랜드 섞임 — 브랜드·제품 조각이 다른 브랜드 것이면 1건
- 결과: 화면 표 + eval/reports/rag_<날짜_시각>.json (질의마다 나온 조각과 점수)
- 과금: 질의 임베딩 건수만큼(20건이면 수백 토큰)
"""
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def brand_name(slug: str) -> str:
    return json.loads((ROOT / "brands" / slug / "brand.json").read_text(encoding="utf-8"))["brand"]["name"]


def _found(alt: str, rows: list[dict]) -> bool:
    """'출처' 는 그 출처의 아무 조각, '출처#글자' 는 절 이름에 그 글자가 든 조각(예: annex5#가목)."""
    src, _, sec = alt.partition("#")
    return any(r["source_id"] == src and sec in r["section"] for r in rows)


def grade(expect: str, results: dict[str, list[dict]]) -> tuple[int, int, list[str]]:
    """(맞힌 수, 기대 수, 놓친 것). 'a/b' 는 둘 중 하나가 나오면 맞힘."""
    rows = [r for rs in results.values() for r in rs]
    want = [w for w in expect.split("|") if w]
    missed = [w for w in want if not any(_found(a, rows) for a in w.split("/"))]
    return len(want) - len(missed), len(want), missed


CACHE = ROOT / "eval" / "cache" / "query_embeddings.json"


def cached_embed(q: str) -> list[float]:
    """질의 임베딩 캐시. OpenAI 임베딩은 호출마다 소수 셋째 자리에서 흔들려(2단계에서 확인) 같은 질의도 실행마다 결과가
    조금씩 달라진다. (모델, 질의 텍스트) 가 같으면 저장된 벡터를 쓴다. 파일은 저장소에 둔다(다른 기계에서도 같은 결과)."""
    import hashlib
    from app import knowledge
    key = hashlib.sha1(f"{knowledge.EMBED_MODEL}\n{q}".encode()).hexdigest()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    if key not in cache:
        cache[key] = {"model": knowledge.EMBED_MODEL, "query": q, "vector": knowledge.embed([q])[0]}
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache[key]["vector"]


def main() -> int:
    args = sys.argv[1:]
    cases_path = Path(args[args.index("--cases") + 1]) if "--cases" in args else ROOT / "eval" / "rag_cases.csv"
    show = args[args.index("--show") + 1] if "--show" in args else None
    from sqlalchemy import select

    from app import db, knowledge
    from app.main import init_app
    from app.models import Brand
    init_app()
    cases = list(csv.DictReader(cases_path.open(encoding="utf-8")))
    rows, hit_total, want_total, leaks, times = [], 0, 0, 0, []
    with db.SessionLocal() as s:
        brands = {b.name: b for b in s.scalars(select(Brand)).all()}
        for c in cases:
            b = brands.get(brand_name(c["brand"]))
            if not b:
                print(f"{c['id']}: 브랜드 {c['brand']} 가 DB 에 없습니다 — register_knowledge.py 를 먼저 실행하세요")
                return 2
            q = knowledge.query_text(c)
            qv = cached_embed(q)                       # 같은 질의 텍스트는 같은 벡터(재현성, eval/cache). 검색 시간만 잰다(네트워크 제외)
            t0 = time.perf_counter()
            res = knowledge.search(s, q, brand_id=b.id, group_id=b.group_id, product_name=c["product_name"] or None,
                                   embedder=lambda _t, _v=qv: [_v])
            ms = (time.perf_counter() - t0) * 1000
            times.append(ms)
            hit, want, missed = grade(c["expect"], res)
            leak = sum(1 for layer in ("브랜드", "제품") for r in res.get(layer, [])
                       if not r["source_id"].endswith(c["brand"]) and f"_{c['brand']}_" not in r["source_id"])
            hit_total += hit
            want_total += want
            leaks += leak
            rows.append({"id": c["id"], "brand": c["brand"], "query": q, "expect": c["expect"], "hit": hit, "want": want,
                         "missed": missed, "leak": leak, "note": c["note"], "ms": round(ms),
                         "results": {k: [{x: r[x] for x in ("source_id", "section", "score", "exact_product")} for r in v]
                                     for k, v in res.items()}})
            mark = "OK " if not missed and not leak else "-- "
            print(f"{mark}{c['id']} {c['brand']:9} {hit}/{want}" + (f"  놓침 {missed}" if missed else "") + (f"  섞임 {leak}" if leak else ""))
            if show == c["id"]:
                for layer, rs in res.items():
                    for r in rs:
                        print(f"     [{layer}] {r['score']:.3f} {r['source_id']} · {r['section']}")
    recall = hit_total / want_total if want_total else 0
    ts = sorted(times)
    p50, p95 = (ts[len(ts) // 2], ts[int(len(ts) * 0.95)]) if ts else (0, 0)
    print(f"\n정답 포함률 {hit_total}/{want_total} = {recall:.0%}   다른 브랜드 섞임 {leaks}건   (층별 k = {knowledge.LAYER_K})")
    print(f"검색 시간(DB+정렬, 임베딩 제외) 중앙값 {p50:.0f}ms · p95 {p95:.0f}ms · 경로 {knowledge.SEARCH_MODE}")
    out = ROOT / "eval" / "reports" / f"rag_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"recall": recall, "hit": hit_total, "want": want_total, "leaks": leaks,
                               "ms_p50": round(p50), "ms_p95": round(p95), "search_mode": knowledge.SEARCH_MODE,
                               "layer_k": knowledge.LAYER_K, "embed_model": knowledge.EMBED_MODEL, "cases": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"보고서: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
