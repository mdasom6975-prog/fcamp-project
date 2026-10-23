"""카피 생성 모델 비교: 같은 브리프·같은 프롬프트를 여러 Claude 모델에 보내 통과율·법적 표현·글자 수·비용·시간을 나란히 본다.
사용:  backend/.venv/bin/python tools/compare_chat_models.py claude-sonnet-5-5 claude-haiku-5-5 [--runs 1]
실제 API 를 부르므로 과금된다(브리프 6개 × 모델 수 × 횟수). 호출 기록 DB 는 건드리지 않는다.
결과는 모델 선택의 참고값이다: 통과율이 같더라도 문구가 마음에 드는지는 출력된 제목 샘플을 사람이 직접 읽어 판단한다."""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import copywriter, llm, pricing, smallbiz  # noqa: E402

PLACEMENT = {"platform": "Instagram", "name": "Instagram 피드"}
TEXT_LIMITS = [{"field": "기본 문구", "max_chars": 125, "count_method": "글자 수", "level": "권장"}]
BRIEFS = [
    {"product_name": "동네 수제 딸기 케이크", "description": "매일 아침 직접 굽는 케이크. 오늘만 1만원", "key_message": "오늘만 1만원"},
    {"product_name": "프리미엄 오메가3 영양제", "description": "혈행 개선에 좋은 오메가3, 하루 한 알", "key_message": "혈행 개선에 도움"},
    {"product_name": "신상 텀블러", "description": "보냉 12시간, 300ml, 파스텔 핑크", "key_message": "가을 음료를 담아 보세요"},
    {"product_name": "피부과 여드름 흉터 시술 이벤트", "description": "여드름 흉터 시술 첫 방문 할인, 100% 효과 보장", "key_message": "흉터 걱정 끝"},
    {"product_name": "청년 적금", "description": "연 5% 금리 적금, 원금 보장", "key_message": "목돈 마련을 시작하세요"},
    {"product_name": "카페 라떼 1+1", "description": "비 오는 날 라떼 한 잔 사면 한 잔 더", "key_message": "비 오는 날 1+1"},
]


def run(model: str, brief: dict) -> dict:
    brand = {"voice": {"polite": True}, "required_phrases": [], "forbidden_phrases": smallbiz.COMMON_FORBIDDEN, "levels": {},
             "source_text": " ".join(brief.values())}
    limits = copywriter.limits_for(TEXT_LIMITS)
    system, user = copywriter._prompt("가게", brand, brief, PLACEMENT, limits)
    t0 = time.time()
    try:
        data, r = llm._claude_json(system, user, llm.copy_schema([]), model=model)
    except Exception as e:      # 모델 이름 오류·거절 등은 그 건의 실패로 센다
        return {"ok": False, "error": str(e)[:120], "ms": int((time.time() - t0) * 1000)}
    ms = int((time.time() - t0) * 1000)
    variants = [{k: str(v.get(k, "")).strip() for k in ("headline", "body", "cta")} for v in (data.get("variants") or [])]
    viol = [copywriter.check(v, limits, brand) for v in variants]
    u = getattr(r, "usage", None)
    usd, _ = pricing.cost_of({"ok": True, "api": "chat", "provider": "anthropic", "model": model,
                              "tokens_in": getattr(u, "input_tokens", 0), "tokens_out": getattr(u, "output_tokens", 0)})
    return {"ok": True, "n": len(variants), "clean": sum(1 for v in viol if not v), "legal_block": sum(1 for vs in viol for x in vs if x["type"] == "legal" and x["severity"] == "필수"),
            "brand_forbidden": sum(1 for vs in viol for x in vs if x["type"] == "forbidden"), "length": sum(1 for vs in viol for x in vs if x["type"] in ("length", "min_length")),
            "usd": usd or 0, "ms": ms, "sample": variants[0]["headline"] if variants else ""}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--runs", type=int, default=1)
    a = ap.parse_args()
    for model in a.models:
        rows = [(b["product_name"], run(model, b)) for b in BRIEFS for _ in range(a.runs)]
        ok = [r for _, r in rows if r["ok"]]
        print(f"\n== {model}  (요금표 입력 ${(pricing._lookup(pricing.table()['chat'], model)[0] or {}).get('in')}/출력 ${(pricing._lookup(pricing.table()['chat'], model)[0] or {}).get('out')} /100만 토큰)")
        for name, r in rows:
            print(f"  {name[:16]:<16} " + (f"변형 {r['n']} · 깨끗 {r['clean']} · 법적차단 {r['legal_block']} · 금지어 {r['brand_forbidden']} · 글자수 {r['length']} · ${r['usd']:.4f} · {r['ms']}ms · 「{r['sample']}」"
                                         if r["ok"] else f"실패: {r['error']}"))
        if ok:
            tot = sum(r["n"] for r in ok)
            print(f"  합계: 변형 {tot}개 중 깨끗 {sum(r['clean'] for r in ok)} · 법적차단 {sum(r['legal_block'] for r in ok)} · 금지어 {sum(r['brand_forbidden'] for r in ok)} · "
                  f"글자수 위반 {sum(r['length'] for r in ok)} · 비용 ${sum(r['usd'] for r in ok):.4f} · 평균 {sum(r['ms'] for r in ok) // len(ok)}ms · 실패 {len(rows) - len(ok)}건")


if __name__ == "__main__":
    main()
