"""화장품 검색 트렌드를 네이버 데이터랩(+구글 트렌드)에서 모아 '트렌드' 지식 층으로 등록한다 (주 1회).
실행:
    backend/.venv/bin/python tools/fetch_trends.py [--dry-run] [--no-google] [--no-profile] [--no-news] [--keywords prompts/trend/keywords.json]
- 뷰티 업계 매체 RSS(코스인코리아·코스모닝)의 소비자 쪽 기사도 함께 '트렌드' 층 문서로 등록한다(--no-news 로 끔). 원문은 저장하지 않는다
- --dry-run: 등록 없이 상위 키워드와 조각 문장만 출력(네이버 호출은 한다). 보고서는 항상 eval/reports/trend_<날짜>.json
- 네이버 호출 수: 키워드 60개 → 검색 12 + 쇼핑 12 + 상위 15개 연령·성별 45 = 69회 (일 1,000회 한도)
- 필요: backend/.env 의 NAVER_CLIENT_ID · NAVER_CLIENT_SECRET. 구글은 비공식(pytrends)이라 실패하면 건너뛴다"""
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    args = sys.argv[1:]
    dry, use_google, profiles = "--dry-run" in args, "--no-google" not in args, "--no-profile" not in args
    kw_path = Path(args[args.index("--keywords") + 1]) if "--keywords" in args else None
    from app import trends
    cfg = trends.load_keywords(kw_path) if kw_path else trends.load_keywords()
    use_news = "--no-news" not in args
    with httpx.Client() as client:
        result = trends.collect(client, cfg, use_google=use_google, profiles=profiles)
        news = trends.collect_news(client, cfg) if use_news else None
    if news is not None:
        result["news"] = {**news, "items": [{**it, "date": str(it["date"])} for it in news["items"]]}
    out = ROOT / "eval" / "reports" / f"trend_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"기간 {result['period'][0]} ~ {result['period'][1]} · 키워드 {result['keywords']}개 · 네이버 {result['naver_api']}{' (쇼핑인사이트 없음: ' + result['shopping_error'] + ')' if result.get('shopping_error') else ''} · 구글 {'실패: ' + result['google_error'] if result['google_error'] else '포함' if use_google else '끔'}")
    for r in result["rows"]:
        print(f"  {r['keyword']:12} {r['group']:6} 네이버 {r['naver_search'] * 100:+5.0f}%  쇼핑 {('-' if r['naver_shopping'] is None else f'{r['naver_shopping'] * 100:+.0f}%'):>6}  구글 {('-' if r.get('google') is None else f'{r['google'] * 100:+.0f}%'):>6}")
    print(f"보고서: {out.relative_to(ROOT)}")
    if news is not None:
        print(f"업계 기사: 읽은 {news['fetched']}건 → 고른 {len(news['items'])}건{' · 실패 ' + str(news['errors']) if news['errors'] else ''}")
        for it in news["items"]:
            print(f"  [{it['source']} {it['date']}] {it['title']}  ← {', '.join(it['matched'])}")
    if dry:
        print("\n조각 예:", trends.chunk_text(result["label"], result["rows"][0]) if result["rows"] else "(없음)")
        return 0
    from app import db
    from app.main import init_app
    init_app()
    with db.SessionLocal() as session:
        doc_id, n = trends.register(session, result)
        print(f"[등록] {result['source_id']} → 문서 {doc_id}, {n}조각 (이전 주 트렌드 문서는 보관)")
        if news is not None:
            nid, nn = trends.register_news(session, news)
            print(f"[등록] {news['source_id']} → 문서 {nid}, {nn}조각" if nid else "[등록] 업계 기사: 고른 기사가 없어 건너뜀")
    return 0


if __name__ == "__main__":
    sys.exit(main())
