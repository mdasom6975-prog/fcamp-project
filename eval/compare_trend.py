"""트렌드 층 있음/없음 비교 (12 로드맵 §6): 같은 브리프로 use_trend=true / false 캠페인을 만들어 카피를 생성하고,
카피 본문에 이번 주 트렌드 키워드가 몇 개 들어갔는지(근거 패널의 트렌드 조각 기준)와 코드 검사 위반 수를 표로 비교한다.

실행 (서버가 떠 있어야 한다, 브리프당 카피 2회 과금):
    backend/.venv/bin/python eval/compare_trend.py [--base http://127.0.0.1:8000] [--brand 에스트라] [--presets 1]
- 브리프는 BRIEFS(지식에 있는 제품). 반환: eval/reports/trend_ab_<날짜_시각>.json + markdown 표
- '트렌드 키워드 반영': 트렌드 있음 쪽이 본 트렌드 조각의 키워드(section)가 세 안의 제목·본문에 하나라도 쓰였는가. 없음 쪽도 같은 키워드로 센다
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BRIEFS = [
    ("아토베리어365 하이드로 수딩크림", "민감피부의 속수분을 채워주는 장벽수분 캡슐크림", "속수분을 채우는 장벽 크림", "건조하고 민감한 30대"),
    ("아토베리어365 바디로션", "민감 피부를 위한 장벽 보습 바디로션", "겨울 건조함을 덜어 주는 바디 보습", "건조한 피부의 20~40대"),
    ("아토베리어365 립 배리어 밤", "입술 장벽을 지켜 주는 보습 립밤", "환절기 갈라지는 입술 케어", "입술이 쉽게 트는 20~30대"),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--brand", default="에스트라")
    ap.add_argument("--presets", default="1")
    a = ap.parse_args()
    presets = [int(x) for x in a.presets.split(",") if x]
    c = httpx.Client(base_url=a.base, timeout=300)
    brand = next((b for b in c.get("/api/brands").json() if b["name"] == a.brand), None)
    if not brand:
        print(f"브랜드 '{a.brand}' 가 없습니다", file=sys.stderr); return 2
    rows = []
    for product, desc, key, target in BRIEFS:
        pair = {}
        for use_trend in (True, False):
            brief = {"product_name": product, "description": desc, "key_message": key, "target": target, "functional": None, "use_trend": use_trend}
            d = c.post("/api/designs", json={"brand_id": brand["id"], "kind": "광고 이미지", "brief": brief, "preset_version_ids": presets}).json()
            if "id" not in d:
                print("캠페인 생성 실패:", product, d, file=sys.stderr); continue
            r = c.post(f"/api/designs/{d['id']}/copy").json()
            ev = c.get(f"/api/designs/{d['id']}/evidence").json()
            variants = r["groups"][0]["variants"] if r.get("groups") else []
            text = _norm(" ".join(f"{v['headline']} {v['body']} {v.get('long_body') or ''}" for v in variants))
            pair[use_trend] = {"design_id": d["id"], "trend_sections": [t["section"] for t in ev.get("trend", [])], "trend_mode": ev.get("trend_mode"),
                               "text": text, "headlines": [v["headline"] for v in variants],
                               "evidence": [x.get("evidence", "") for x in ev.get("variants", [])],
                               "must": sum(1 for v in variants for x in v["violations"] if x.get("severity") == "필수"),
                               "warn": sum(1 for v in variants for x in v["violations"] if x.get("severity") != "필수")}
        if len(pair) < 2:
            continue
        kws = pair[True]["trend_sections"]
        for use_trend in (True, False):
            p = pair[use_trend]
            p["hits"] = [k for k in kws if _norm(k) in p["text"]]
            p["in_evidence"] = [k for k in kws if any(_norm(k) in _norm(e) for e in p["evidence"])]
        rows.append({"brief": product, "trend_keywords": kws, "with": {k: v for k, v in pair[True].items() if k != "text"},
                     "without": {k: v for k, v in pair[False].items() if k != "text"}})
        print(f"{product[:16]:16} 트렌드 {kws} → 있음 반영 {pair[True]['hits']} (evidence {pair[True]['in_evidence']}) 위반 {pair[True]['must']}/{pair[True]['warn']}"
              f" · 없음 반영 {pair[False]['hits']} 위반 {pair[False]['must']}/{pair[False]['warn']}")
    out = ROOT / "eval" / "reports" / f"trend_ab_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n| 브리프 | 트렌드 조각(키워드) | 있음: 반영 / 위반 필수·권장 | 없음: 반영 / 위반 필수·권장 |")
    print("|---|---|---|---|")
    for r in rows:
        w, wo = r["with"], r["without"]
        print(f"| {r['brief']} | {', '.join(r['trend_keywords'])} | {len(w['hits'])}/{len(r['trend_keywords'])} ({', '.join(w['hits']) or '-'}) / {w['must']}·{w['warn']} "
              f"| {len(wo['hits'])}/{len(r['trend_keywords'])} ({', '.join(wo['hits']) or '-'}) / {wo['must']}·{wo['warn']} |")
    print(f"\n보고서: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
