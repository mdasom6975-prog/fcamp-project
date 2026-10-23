"""법적·제도적 금지 규칙. 규칙 문장은 prompts/legal/ 에 있고, 여기서는 품목 글에서 규제 업종을 알아내 그 업종 규칙을 고른다.
공통 규칙(prompts/legal/_copy_common · _image_common)은 카피 System·배경 프롬프트가 항상 include 한다.
키워드 감지는 '이 업종일 수 있다'는 보수적 판정이라 놓칠 수 있으며, 법률 자문이 아니다(prompts/legal/*.md 의 status 참고)."""
from . import prompts

# 업종 파일 이름(prompts/legal/industry/<이름>.md) → 감지 키워드. 부분 문자열로 찾는다.
INDUSTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "건강기능식품": ("건강기능식품", "건기식", "영양제", "비타민", "오메가3", "유산균", "프로바이오틱스", "홍삼", "루테인", "콜라겐"),
    "식품": ("식품", "음식", "반찬", "도시락", "간식", "과자", "빵", "케이크", "쿠키", "디저트", "커피", "음료", "녹차", "홍차", "보리차", "주스", "김치",
             "고기", "치킨", "피자", "떡", "식당", "맛집", "카페", "베이커리", "밀키트", "다이어트", "분유", "이유식", "과일", "즙", "젤리"),
    "화장품": ("화장품", "스킨케어", "크림", "세럼", "에센스", "토너", "선크림", "자외선", "마스크팩", "립스틱", "립밤", "틴트", "파운데이션", "샴푸", "바디", "미백", "주름"),
    "의료": ("병원", "의원", "한의원", "치과", "피부과", "성형", "시술", "약국", "진료", "클리닉", "치아교정", "한약", "치료"),
    "금융": ("대출", "신용카드", "체크카드", "보험", "예금", "적금", "투자", "펀드", "주식투자", "증권", "비트코인", "가상화폐", "금리", "연금", "금융"),
    "주류": ("주류", "술집", "소주", "맥주", "와인", "막걸리", "위스키", "사케", "하이볼", "칵테일", "주점", "이자카야", "펍"),
}


def detect(text: str) -> list[str]:
    """품목 글에서 규제 업종 후보를 찾는다(여러 개 가능). 건강기능식품이 잡히면 일반 식품 규칙은 덧붙이지 않는다."""
    t = (text or "") + " "
    hit = [k for k, words in INDUSTRY_KEYWORDS.items() if any(w in t for w in words)]
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

