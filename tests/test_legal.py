"""법적·제도적 금지 규칙: 업종 감지, 프롬프트 주입, 규칙 파일 메타."""
import pytest

from app import copywriter, imagegen, legal, prompts

BRAND = {"voice": {}, "required_phrases": [], "forbidden_phrases": []}
PLACEMENT = {"platform": "Instagram", "name": "Instagram 피드"}
LIMITS = {"headline": None, "body": None, "cta": None, "skipped": []}


@pytest.mark.parametrize("text,expected", [
    ("동네 수제 딸기 케이크", ["식품"]),
    ("프리미엄 오메가3 영양제", ["건강기능식품"]),
    ("피부과 여드름 시술 이벤트", ["의료"]),
    ("신용카드 연회비 면제 대출", ["금융"]),
    ("수분 크림 세럼", ["화장품"]),
    ("텀블러", []),
    ("주식회사 로고 카드뉴스 제작", []),      # 주식회사·카드뉴스·시술의 '술' 같은 부분 일치 오탐 방지
    ("동네 술집 생맥주", ["주류"]),
])
def test_detect_industries(text, expected):
    assert legal.detect(text) == expected


def test_functional_food_does_not_also_add_general_food():
    assert legal.detect("건강기능식품 홍삼 음료") == ["건강기능식품"]


def test_copy_system_always_has_common_rules_and_user_has_industry_block():
    system, user = copywriter._prompt("가게", BRAND, {"product_name": "딸기 케이크", "description": "수제"}, PLACEMENT, LIMITS)
    assert "표시·광고의 공정화에 관한 법률" in system and "거짓 금지" in system and "욕설" in system
    assert "[식품 광고 주의" in user and "질병의 예방·치료" in user
    _, user2 = copywriter._prompt("가게", BRAND, {"product_name": "텀블러"}, PLACEMENT, LIMITS)
    assert "광고 주의" not in user2


def test_image_prompt_has_common_legal_rules():
    p = imagegen.build_prompt("x", {}, {"product_name": "텀블러"}, "정사각", "실사 제품·공간")
    assert "[법적 준수]" in p and "전후 비교" in p


def test_every_legal_file_records_law_and_status():
    names = [n for n in prompts.list_names() if n.startswith("legal/")]
    assert {n.split("/")[-1] for n in names if "industry" in n} == set(legal.INDUSTRY_KEYWORDS)
    for n in names:
        meta = prompts.load(n).meta
        assert meta.get("law") and meta.get("status"), f"{n}: law/status 메타가 필요합니다"

