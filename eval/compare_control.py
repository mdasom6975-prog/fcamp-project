"""S3 대조군 비교 (00 정의서 v2.4): 같은 브리프를 아모레퍼시픽 브랜드(지식 있음)와 타 브랜드(지식 없음, 대조군)로 만들어 카피를 생성하고,
근거 조각 수 · 제품 사실 사용 · 코드 검사 위반 수 · 근거 없는 수치 주장을 표로 비교한다.

실행 (서버가 떠 있어야 한다, 카피 생성 = 브리프당 Claude Sonnet 1회 + Haiku 1회 과금):
    backend/.venv/bin/python eval/compare_control.py [--base http://127.0.0.1:8000] [--control-brand 대조군 테스트] [--presets 1] [--only 마스크]
- 브리프는 아래 BRIEFS(제품은 지식에 있는 것, 두 건은 일부러 위반을 유도). 대조군은 같은 브리프 텍스트를 그대로 쓴다
- 결과: eval/reports/s3_<날짜_시각>.json + 표(markdown)를 stdout 에. 캠페인은 "[S3] " 접두로 남겨 분석 탭에서 볼 수 있다
- 제품 사실: 지식의 제품 조각에서 뽑은 사실 토큰(용량·라인·성분·pH)이 카피 세 안의 본문에 하나라도 쓰였는지. 근거 없는 수치: 브리프·제품 자료에 없는 숫자+단위(시간·%·배) 주장
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent

# (브랜드, 상품명, 설명, 핵심 메시지, 타깃, 의도)
BRIEFS = [
    ("에스트라", "아토베리어365 하이드로 수딩크림", "민감피부의 속수분을 채워주는 장벽수분 캡슐크림", "속수분을 채우는 장벽 크림", "건조하고 민감한 30대", "정상"),
    ("에스트라", "아토베리어365 하이드로 수딩크림", "민감피부의 속수분을 채워주는 장벽수분 캡슐크림", "아토피를 치료하는 장벽 크림", "아토피가 고민인 20~30대", "의약품 오인 유도"),
    ("에스트라", "더마UV365 레드진정 톤업 선크림", "민감 피부의 붉은기를 가려 주는 톤업 선크림", "붉은기 없이 환한 하루", "민감 피부 20~30대", "정상(기능성 범위 주의)"),
    ("라네즈", "워터 슬리핑 마스크", "밤사이 수분을 채워 주는 슬리핑 마스크", "자고 일어나면 촉촉한 피부", "20~30대 건조 피부", "정상"),
    ("설화수", "자음생크림 리치", "인삼 과학으로 탄력을 케어하는 안티에이징 크림", "48시간 만에 콜라겐이 회복되는 크림", "40~50대 여성", "효능 단정 유도"),
]
FACT_PATTERNS = [r"\d+\s?ml", r"\d+\s?매", r"ph\s?4\.5", r"세라마이드", r"히알루론산", r"판테놀", r"캡슐", r"장벽", r"진세노", r"인삼", r"사포닌", r"sleeptox", r"시카|cica", r"톤업", r"spf", r"pa\+"]
NUMERIC_CLAIM = re.compile(r"(\d+\s?(시간|분|일|주|개월|%|배|배속|만에))")


def facts_of(text: str) -> set[str]:
    t = text.lower()
    return {p for p in FACT_PATTERNS if re.search(p, t)}


def numeric_claims(text: str) -> set[str]:
    return {m[0].replace(" ", "") for m in NUMERIC_CLAIM.findall(text)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--control-brand", default="대조군 테스트")
    ap.add_argument("--presets", default="1", help="캠페인에 넣을 규격 id (쉼표)")
    ap.add_argument("--only", default="", help="상품명에 이 말이 든 브리프만 (일부 재실행)")
    a = ap.parse_args()
    presets = [int(x) for x in a.presets.split(",") if x]
    c = httpx.Client(base_url=a.base, timeout=300)
    brands = {b["name"]: b for b in c.get("/api/brands").json()}
    if a.control_brand not in brands:
        print(f"대조군 브랜드 '{a.control_brand}' 가 없습니다. 타 브랜드 생성 입구에서 먼저 만드세요", file=sys.stderr)
        return 2
    rows = []
    for brand_name, product, desc, key, target, intent in BRIEFS:
        if a.only and a.only not in product:
            continue
        for side, bname in (("아모레(지식)", brand_name), ("대조군", a.control_brand)):
            b = brands[bname]
            brief = {"product_name": product, "description": desc, "key_message": key, "target": target, "functional": None}
            d = c.post("/api/designs", json={"brand_id": b["id"], "kind": "광고 이미지", "brief": {**brief, "product_name": product}, "preset_version_ids": presets}).json()
            if "id" not in d:
                print("캠페인 생성 실패:", bname, product, d, file=sys.stderr); continue
            did = d["id"]
            r = c.post(f"/api/designs/{did}/copy").json()
            if r.get("failures"):
                print("생성 실패:", bname, product, r["failures"], file=sys.stderr)
            ev = c.get(f"/api/designs/{did}/evidence").json()
            variants = r["groups"][0]["variants"] if r.get("groups") else []
            texts = " ".join(f"{v['headline']} {v['body']} {v.get('long_body') or ''}" for v in variants)
            must = sum(1 for v in variants for x in v["violations"] if x.get("severity") == "필수")
            warn = sum(1 for v in variants for x in v["violations"] if x.get("severity") != "필수")
            # 제품 사실: 지식 제품 조각(아모레 쪽 근거)에서 뽑은 사실 — 대조군도 같은 제품의 사실을 기준으로 잰다
            prod_rows = [g for g in (ev.get("guide") or []) if g["layer"] == "제품"]
            source_facts = facts_of(" ".join(g["text"] for g in prod_rows)) if prod_rows else set()
            used_facts = facts_of(texts)
            brief_claims = numeric_claims(f"{product} {desc} {key} {target}") | numeric_claims(" ".join(g["text"] for g in prod_rows))
            unsupported = numeric_claims(texts) - brief_claims
            rows.append({"brief": product, "intent": intent, "side": side, "brand": bname, "design_id": did,
                         "law": len(ev.get("law") or []), "law_core": sum(1 for x in (ev.get("law") or []) if x.get("core")),
                         "guide": len(ev.get("guide") or []), "product_chunk": bool(prod_rows), "law_mode": ev.get("law_mode"),
                         "source_facts": sorted(source_facts), "used_facts": sorted(used_facts), "facts_hit": len(used_facts & source_facts) if source_facts else None,
                         "violations_must": must, "violations_warn": warn, "unsupported_numbers": sorted(unsupported),
                         "headlines": [v["headline"] for v in variants]})
            print(f"{side:8} {product[:14]:14} 규제 {rows[-1]['law']:2} 근거 {rows[-1]['guide']} 위반(필수/권장) {must}/{warn} 사실 {rows[-1]['facts_hit']} 수치 {sorted(unsupported)}")
    out = ROOT / "eval" / "reports" / f"s3_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n| 브리프 | 의도 | 쪽 | 규제 조각 | 근거 조각 | 제품 자료 | 제품 사실 사용 | 위반 필수/권장 | 근거 없는 수치 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['brief']} | {r['intent']} | {r['side']} | {r['law']} (핵심 {r['law_core']}) | {r['guide']} | {'있음' if r['product_chunk'] else '없음'} | "
              f"{'—' if r['facts_hit'] is None else str(r['facts_hit']) + '/' + str(len(r['source_facts']))} | "
              f"{r['violations_must']}/{r['violations_warn']} | {', '.join(r['unsupported_numbers']) or '없음'} |")
    print(f"\n보고서: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
