"""생성 대기 화면의 브랜드 퀴즈. 기다리는 동안 브랜드 규칙과 광고 표현 규칙을 익히게 한다.
API·모델 호출 없이 브랜드 규칙 버전(허용 색상·로고 자리)과 실제 적용되는 필수·금지 표현(phrases.effective),
표현 사전(prompts/legal/phrases.json, 업종 공통 항목만)으로 문항을 만든다. 문항마다 보기를 섞어 돌려준다."""
import random

from . import icons, legal, phrases

# 금지·필수 표현 문항의 오답 보기. 표현 사전에 걸리지 않는 평범한 광고 문구
SAFE_PHRASES = ["지금 만나 보세요", "새롭게 시작해요", "자세히 알아보기", "함께 준비해요", "오늘부터 가볍게",
                "한 번 더 생각해요", "매일 조금씩", "가까이에서 만나요"]
# 색 문항의 오답 보기. 브랜드 색과 헷갈릴 만큼 가까운 색(ΔE ≤ DECOY_MIN_DE)은 뺀다
DECOY_COLORS = ["#2F80ED", "#27AE60", "#F2C94C", "#9B51E0", "#EB5757", "#F2994A", "#56CCF2", "#6FCF97",
                "#333333", "#BDBDBD", "#8D6E63", "#E91E63"]
LOGO_SPOTS = ["좌상단", "우상단", "좌하단", "우하단"]
DECOY_MIN_DE = 30.0      # analysis.NEAR_DE(20, '같은 계열')보다 넉넉히 떨어진 색만 오답으로
LEGAL_PICK = 4            # 표현 규칙 문항 수
SKIP_REASONS = ("욕설",)  # 대기 화면에 띄우기 부적절한 항목


def _item(kind: str, question: str, options: list[dict], answer: int, explain: str, rng: random.Random) -> dict:
    """보기를 섞고 정답 위치를 맞춘다. options: [{label, color?}]"""
    order = list(range(len(options)))
    rng.shuffle(order)
    return {"kind": kind, "question": question, "options": [options[i] for i in order],
            "answer": order.index(answer), "explain": explain}


def _color_items(brand_name: str, colors: list[dict], rng: random.Random) -> list[dict]:
    from .analysis import delta_e
    ident = [c for role in icons.PALETTE_ROLES for c in colors if c.get("role") == role and c.get("hex")]
    out = []
    for c in ident:
        others = [x["hex"].upper() for x in colors if x.get("hex") and x is not c]
        decoys = [h for h in DECOY_COLORS if all(delta_e(h, x["hex"]) > DECOY_MIN_DE for x in colors if x.get("hex"))]
        rng.shuffle(decoys)
        wrong = list(dict.fromkeys([h for h in others if h != c["hex"].upper()][:1] + decoys))[:3]
        opts = [{"label": c["hex"].upper(), "color": c["hex"].upper()}] + [{"label": h, "color": h} for h in wrong]
        out.append(_item("color", f"{brand_name}의 {c['role']}은 어느 색일까요?", opts, 0,
                         f"{c['role']}은 {c['hex'].upper()} 입니다. 제목·CTA 버튼 같은 합성 요소와 아이콘, 배경의 포인트 색에 쓰입니다.", rng))
    return out


def _phrase_items(brand_name: str, eff: dict, rng: random.Random) -> list[dict]:
    out = []
    banned = set(eff["forbidden_phrases"]) | set(eff["required_phrases"])
    safe = [p for p in SAFE_PHRASES if p not in banned and not legal.scan(p)]
    src = {"캠페인": "이 캠페인 입력", "브랜드": "브랜드 규칙"}
    for key, q, why in (("forbidden_phrases", f"{brand_name} 광고에 쓰면 안 되는 표현은?", "금지 표현"),
                        ("required_phrases", f"{brand_name} 광고 문구에 꼭 들어가야 하는 표현은?", "필수 표현")):
        lst = eff[key]
        if not lst or len(safe) < 3:
            continue
        pick = rng.choice(lst)
        wrong = rng.sample(safe, 3)
        where = src.get(eff[key.replace("_phrases", "_source")], "브랜드 규칙")
        out.append(_item("phrase", q, [{"label": pick}] + [{"label": w} for w in wrong], 0,
                         f"'{pick}' 은(는) {where}의 {why}입니다. 검수에서 {'들어 있으면' if key == 'forbidden_phrases' else '빠지면'} 수정이 필요합니다.", rng))
    return out


def _legal_items(rng: random.Random) -> list[dict]:
    pool = [e for e in legal._entries() if not e.get("industries") and not any(s in e["reason"] for s in SKIP_REASONS)]
    out = []
    for e in rng.sample(pool, min(LEGAL_PICK, len(pool))):
        block = e["severity"] == "block"
        opts = [{"label": "그대로 써도 된다"}, {"label": "근거 없이 쓰면 안 된다" if block else "근거를 확인하고 써야 한다"}]
        tip = f" 바꿔 쓰기: {e['suggest']}." if e.get("suggest") else ""
        out.append(_item("legal", f"'{e['phrase']}' — 광고 문구에 그대로 써도 될까요?", opts, 1,
                         f"{e['reason']}. 근거: {e['law']}.{tip}", rng))
    return out


def _logo_item(brand_name: str, position: str | None, rng: random.Random) -> list[dict]:
    pos = position or "좌상단"
    if pos not in LOGO_SPOTS:          # '자동'은 지면마다 달라 문제로 내지 않는다
        return []
    return [_item("logo", f"{brand_name} 광고를 합성할 때 로고는 어느 모서리에 놓일까요?",
                  [{"label": pos}] + [{"label": p} for p in LOGO_SPOTS if p != pos], 0,
                  f"브랜드 규칙의 로고 자리는 {pos} 입니다. 캠페인의 모든 지면이 같은 자리를 써서 브랜드가 한눈에 들어옵니다.", rng)]


def build(brand_name: str, bv, brief: dict | None, seed: int | None = None) -> list[dict]:
    """대기 화면 퀴즈 문항 목록(섞인 순서). 브랜드 문항이 없어도 표현 규칙 문항은 늘 나온다."""
    rng = random.Random(seed)
    eff = phrases.effective(bv, brief)
    items = (_color_items(brand_name, bv.colors or [], rng) + _phrase_items(brand_name, eff, rng)
             + _logo_item(brand_name, bv.logo_position, rng) + _legal_items(rng))
    rng.shuffle(items)
    return items
