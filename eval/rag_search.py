"""RAG 검색 품질 평가 (설계 16 · 6절, 1-a 수용 기준). 생성 없이 검색 결과만 채점한다.

실행: backend/.venv/bin/python eval/rag_search.py [--cases eval/rag_cases.csv] [--show R07]
- 먼저 tools/register_knowledge.py 로 지식을 등록해 둔다(같은 DB: DATABASE_URL, 없으면 backend/data/app.db)
- 각 건의 브리프로 층별 검색(knowledge.LAYER_K)을 하고, expect 의 출처 id 가 결과에 있는지 본다
  · expect 는 '|' 로 나열, 'a/b' 는 둘 중 하나면 정답
- 지표: 정답 포함률(Recall) — 기대한 출처 중 나온 비율, 다른 브랜드 섞임 — 브랜드·제품 조각이 다른 브랜드 것이면 1건
- 결과: 화면 표 + eval/reports/rag_<날짜_시각>.json (질의마다 나온 조각과 점수)
- 과금: 질의 임베딩 건수만큼(20건이면 수백 토큰)
"""
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def brand_name(slug: str) -> str:
    return json.loads((ROOT / "brands" / slug / "brand.json").read_text(encoding="utf-8"))["brand"]["name"]


def grade(expect: str, results: dict[str, list[dict]]) -> tuple[int, int, list[str]]:
    """(맞힌 수, 기대 수, 놓친 것). 'a/b' 는 둘 중 하나가 나오면 맞힘."""
    got = {r["source_id"] for rows in results.values() for r in rows}
    want = [w for w in expect.split("|") if w]
    missed = [w for w in want if not set(w.split("/")) & got]
    return len(want) - len(missed), len(want), missed


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
    rows, hit_total, want_total, leaks = [], 0, 0, 0
    with db.SessionLocal() as s:
        brands = {b.name: b for b in s.scalars(select(Brand)).all()}
        for c in cases:
            b = brands.get(brand_name(c["brand"]))
            if not b:
                print(f"{c['id']}: 브랜드 {c['brand']} 가 DB 에 없습니다 — register_knowledge.py 를 먼저 실행하세요")
                return 2
            q = knowledge.query_text(c)
            res = knowledge.search(s, q, brand_id=b.id, group_id=b.group_id, product_name=c["product_name"] or None)
            hit, want, missed = grade(c["expect"], res)
            leak = sum(1 for layer in ("브랜드", "제품") for r in res.get(layer, [])
                       if not r["source_id"].endswith(c["brand"]) and f"_{c['brand']}_" not in r["source_id"])
            hit_total += hit
            want_total += want
            leaks += leak
            rows.append({"id": c["id"], "brand": c["brand"], "query": q, "expect": c["expect"], "hit": hit, "want": want,
                         "missed": missed, "leak": leak, "note": c["note"],
                         "results": {k: [{x: r[x] for x in ("source_id", "section", "score", "exact_product")} for r in v]
                                     for k, v in res.items()}})
            mark = "OK " if not missed and not leak else "-- "
            print(f"{mark}{c['id']} {c['brand']:9} {hit}/{want}" + (f"  놓침 {missed}" if missed else "") + (f"  섞임 {leak}" if leak else ""))
            if show == c["id"]:
                for layer, rs in res.items():
                    for r in rs:
                        print(f"     [{layer}] {r['score']:.3f} {r['source_id']} · {r['section']}")
    recall = hit_total / want_total if want_total else 0
    print(f"\n정답 포함률 {hit_total}/{want_total} = {recall:.0%}   다른 브랜드 섞임 {leaks}건   (층별 k = {knowledge.LAYER_K})")
    out = ROOT / "eval" / "reports" / f"rag_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"recall": recall, "hit": hit_total, "want": want_total, "leaks": leaks,
                               "layer_k": knowledge.LAYER_K, "embed_model": knowledge.EMBED_MODEL, "cases": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"보고서: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
