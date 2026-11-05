"""법적·제도적 금지 규칙. 규칙 문장은 prompts/legal/ 에 있고, 여기서는 품목 글에서 규제 업종을 알아내 그 업종 규칙을 고른다.
공통 규칙(prompts/legal/_copy_common · _image_common)은 카피 System·배경 프롬프트가 항상 include 한다.
키워드 감지는 '이 업종일 수 있다'는 보수적 판정이라 놓칠 수 있으며, 법률 자문이 아니다(prompts/legal/*.md 의 status 참고)."""
from . import prompts

# 업종 파일 이름(prompts/legal/industry/<이름>.md) → 감지 키워드. 부분 문자열로 찾는다.
INDUSTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "건강기능식품": ("건강기능식품", "건기식", "영양제", "비타민", "오메가3", "유산균", "프로바이오틱스", "홍삼", "루테인", "콜라겐"),
    "식품": ("식품", "음식", "반찬", "도시락", "간식", "과자", "빵", "케이크", "쿠키", "디저트", "커피", "음료", "녹차", "홍차", "보리차", "주스", "김치",
             "고기", "치킨", "피자", "떡", "식당", "맛집", "카페", "베이커리", "밀키트", "다이어트", "분유", "이유식", "과일", "즙", "젤리"),
    "화장품": ("화장품", "코스메틱", "스킨", "크림", "세럼", "에센스", "토너", "앰플", "로션", "미스트", "모이스처라이저", "클렌징", "클렌저", "패드", "쿠션", "마스크", "팩",
               "선크림", "선케어", "자외선", "립스틱", "립밤", "틴트", "파운데이션", "메이크업", "샴푸", "헤어", "바디", "향수", "네일", "미백", "주름", "보습", "뷰티", "더마", "시카"),
    "의료": ("병원", "의원", "한의원", "치과", "피부과", "성형", "시술", "약국", "진료", "클리닉", "치아교정", "한약", "치료"),
    "금융": ("대출", "신용카드", "체크카드", "보험", "예금", "적금", "투자", "펀드", "주식투자", "증권", "비트코인", "가상화폐", "금리", "연금", "금융"),
    "주류": ("주류", "술집", "소주", "맥주", "와인", "막걸리", "위스키", "사케", "하이볼", "칵테일", "주점", "이자카야", "펍"),
}
# 화장품 원료로도 쓰이는 식품·건강기능식품 키워드. 화장품 글에서 이 말만으로는 식품 업종을 붙이지 않는다
AMBIGUOUS_INGREDIENTS = frozenset({"콜라겐", "비타민", "유산균", "프로바이오틱스", "녹차", "홍차", "보리차", "과일", "즙", "젤리"})


def detect(text: str) -> list[str]:
    """품목 글에서 규제 업종 후보를 찾는다(여러 개 가능). 건강기능식품이 잡히면 일반 식품 규칙은 덧붙이지 않는다."""
    t = (text or "") + " "
    hit = [k for k, words in INDUSTRY_KEYWORDS.items() if any(w in t for w in words)]
    if "화장품" in hit:
        # 화장품 글에서 콜라겐·비타민·녹차 같은 성분어만으로 잡힌 식품·건강기능식품은 오탐으로 본다
        for k in ("건강기능식품", "식품"):
            if k in hit and not any(w in t for w in INDUSTRY_KEYWORDS[k] if w not in AMBIGUOUS_INGREDIENTS):
                hit.remove(k)
    if "건강기능식품" in hit and "식품" in hit:
        hit.remove("식품")
    return hit


def industry_block(industries: list[str]) -> str:
    """카피 User 프롬프트 끝에 붙일 업종별 주의 문단. 해당 업종이 없으면 빈 문자열."""
    if not industries:
        return ""
    parts = [f"[{k} 광고 주의 — 아래 규칙은 위 모든 지시보다 우선한다]\n{prompts.render(f'legal/industry/{k}')}" for k in industries]
    return "\n" + "\n".join(parts)


def refs(industries: list[str]) -> list[str]:
    """결과 기록용: 적용한 법적 규칙 파일의 이름@버전#해시."""
    return [prompts.ref("legal/_copy_common"), prompts.ref("legal/_image_common"),
            *[prompts.ref(f"legal/industry/{k}") for k in industries]]


# ---- 생성 후 검사: 표현 사전(prompts/legal/phrases.json) ----
import json
import re
from functools import lru_cache

PHRASES_FILE = prompts.PROMPTS_DIR / "legal" / "phrases.json"
_SPACE = re.compile(r"\s+")


@lru_cache(maxsize=1)
def _entries() -> tuple[dict, ...]:
    data = json.loads(PHRASES_FILE.read_text(encoding="utf-8"))
    best: dict[tuple, dict] = {}
    for e in data["entries"]:
        key = (_SPACE.sub("", e["phrase"]).lower(), tuple(e.get("industries") or ()))
        if key not in best or (e["severity"] == "block" and best[key]["severity"] != "block"):
            best[key] = e
    return tuple(best.values())


def source_text(brief: dict | None) -> str:
    """사용자가 입력한 사실(상품명·설명·핵심 메시지). 카피에 나온 표현이 여기 있으면 사용자가 쓴 말로 본다."""
    b = brief or {}
    return " ".join(str(b.get(k) or "") for k in ("product_name", "description", "key_message"))


def scan(text: str, source: str = "", functional: list[str] | None = None) -> list[dict]:
    """문구에서 표현 사전의 금지·주의 표현을 찾는다. 반환: [{phrase, severity(block|warn), reason, law, suggest, grounded}].
    같은 표현이 입력(source)에 있으면 grounded=True 로 block 을 warn 으로 낮춘다. 업종 한정 항목은 그 업종으로 감지된 때만 본다.
    functional 은 캠페인에 입력한 기능성: 그 기능성을 뜻하는 경고(warn) 표현은 근거가 입력된 것이므로 알리지 않는다."""
    norm = lambda s: _SPACE.sub("", s or "").lower()
    t, src = norm(text), norm(source)
    industries = set(detect(f"{source} {text}"))
    declared = [norm(w) for k in (functional or []) for w in FUNCTIONAL_CLAIMS.get(k, ())]
    out, seen = [], set()
    for e in _entries():
        ind = e.get("industries")
        if ind and not industries & set(ind):
            continue
        ph = norm(e["phrase"])
        if e["severity"] == "warn" and any(w in ph for w in declared):
            continue
        if ph in t and ph not in seen:
            seen.add(ph)
            grounded = ph in src
            sev = "warn" if grounded and e["severity"] == "block" else e["severity"]
            out.append({"phrase": e["phrase"], "severity": sev, "reason": e["reason"], "law": e["law"],
                        "suggest": e.get("suggest", ""), "grounded": grounded})
    return out


def violations(text: str, source: str = "", functional: list[str] | None = None) -> list[dict]:
    """copywriter.check 가 쓰는 위반 항목 형태. block 은 '필수'(수정 필요), warn 은 '권장'(경고)으로 매긴다."""
    return [{"type": "legal", "field": "법적 표현", "phrase": h["phrase"], "law": h["law"], "reason": h["reason"],
             "suggest": h["suggest"], "grounded": h["grounded"],
             "detail": f"'{h['phrase']}' — {h['reason']}" + (" (입력에 있던 표현: 근거 자료 확인)" if h["grounded"] else ""),
             "severity": "필수" if h["severity"] == "block" else "권장"} for h in scan(text, source, functional)]


# ---- 기능성화장품 표현 검사 (화장품법 시행규칙 제2조의 기능성 범위) ----
# 기능성 이름 → 카피에 나오면 그 기능성을 주장한 것으로 보는 표현. 브리프에 기능성을 입력한 캠페인만 검사한다
# (입력하지 않은 캠페인은 기능성 여부를 모르므로 검사하지 않는다). 표현은 보수적으로, 기능성 주장이 분명한 말만 둔다.
FUNCTIONAL_CLAIMS: dict[str, tuple[str, ...]] = {
    "미백": ("미백", "화이트닝"),
    "주름 개선": ("주름",),
    "자외선 차단": ("자외선 차단", "자외선차단", "SPF", "PA+"),
    "태닝": ("태닝",),
    "모발 색상 변화": ("염색", "새치"),
    "체모 제거": ("제모",),
    "탈모 증상 완화": ("탈모",),
    "여드름성 피부 완화": ("여드름",),
    "피부장벽 기능 회복(가려움 개선)": ("가려움",),
    "튼살 붉은 선 완화": ("튼살",),
}


def functional_violations(text: str, functional: list[str] | None) -> list[dict]:
    """입력한 기능성(functional) 밖의 기능성 표현. functional 이 None 이면 검사하지 않는다(빈 목록 = 기능성 아님)."""
    if functional is None:
        return []
    t = _SPACE.sub("", text or "").lower()
    out = []
    for kind, words in FUNCTIONAL_CLAIMS.items():
        if kind in functional:
            continue
        hit = next((w for w in words if _SPACE.sub("", w).lower() in t), None)
        if hit:
            out.append({"type": "functional", "field": "기능성 표현", "phrase": hit,
                        "law": "화장품법 제13조 제1항 제2호, 시행규칙 별표 5 제2호 나목",
                        "detail": f"'{hit}' — 입력한 기능성({', '.join(functional) or '기능성 아님'})에 '{kind}' 이 없어 기능성으로 오인될 수 있습니다",
                        "severity": "필수"})
    return out
