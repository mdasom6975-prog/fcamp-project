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


# ---- 2단계: 생성 후 표현 사전 검사 ----
def test_scan_blocks_superlative_and_ignores_spaces():
    hits = legal.scan("우리 동네 최 고 의 케이크, 원금 보장!")
    ph = {h["phrase"]: h for h in hits}
    assert ph["최고"]["severity"] == "block" and "표시·광고의 공정화" in ph["최고"]["law"]
    assert "원금 보장" not in ph          # 금융 업종이 아니면 업종 한정 표현은 보지 않는다


def test_industry_phrases_apply_only_to_detected_industry():
    assert any(h["phrase"] == "치료" for h in legal.scan("피부 트러블 치료 크림", "수분 크림"))
    assert not any(h["phrase"] == "치료" for h in legal.scan("충치 치료 안내", "텀블러"))      # 식품·화장품 업종이 아니면 제외
    assert any(h["phrase"] == "원금 보장" for h in legal.scan("원금 보장 적금", "적금 상품"))


def test_phrase_in_user_input_is_downgraded_to_warning():
    hits = legal.scan("지역 1등 케이크", "지역 1등 케이크 가게")
    h = next(x for x in hits if x["phrase"] == "1등")
    assert h["severity"] == "warn" and h["grounded"] is True
    h2 = next(x for x in legal.scan("지역 1등 케이크", "케이크 가게") if x["phrase"] == "1등")
    assert h2["severity"] == "block" and h2["grounded"] is False


def test_false_positive_words_are_not_flagged():
    for ok in ("쫄깃하게 씹는 맛이 좋은 빵", "시발점이 되는 이야기"):
        assert not legal.scan(ok), ok


def test_check_adds_legal_violation_with_law_and_severity():
    brand = {"levels": {}, "forbidden_phrases": [], "required_phrases": [], "source_text": "케이크"}
    v = copywriter.check({"headline": "국내 최고 케이크", "body": "무조건 만족", "cta": "보기"},
                         {"headline": None, "body": None, "cta": None}, brand)
    lg = [x for x in v if x["type"] == "legal"]
    assert {x["phrase"] for x in lg} >= {"최고", "무조건"}
    assert all(x["severity"] == "필수" and x["law"] and x["detail"] for x in lg)


def test_phrase_file_entries_are_well_formed():
    import json
    data = json.loads(legal.PHRASES_FILE.read_text(encoding="utf-8"))
    assert data["status"] and data["entries"]
    for e in data["entries"]:
        assert e["severity"] in ("block", "warn") and e["phrase"] and e["reason"] and e["law"]
        assert set(e.get("industries", [])) <= set(legal.INDUSTRY_KEYWORDS)


def _review(tmp_path, headline, source="케이크"):
    from types import SimpleNamespace

    from PIL import Image

    from app import review
    path = tmp_path / "img.png"
    Image.new("RGB", (500, 500), "white").save(path)
    pv = SimpleNamespace(production_width=500, production_height=500, ratio=None, conditions=[], safe_area=None,
                         text_limits=[], required_assets=[], placement=SimpleNamespace(content_kind="완성형", name="피드"))
    brand = {"levels": {}, "forbidden_phrases": [], "required_phrases": [], "colors": [], "fonts": [], "logo_asset_id": None,
             "source_text": source}
    items = review.review_preset(pv, SimpleNamespace(file_path=str(path)), {"headline": headline, "body": "", "cta": ""},
                                 None, None, None, None, brand, None, True)
    return [i for i in items if i["category"] == "법적"]


def test_review_blocks_on_legal_block_phrase(tmp_path):
    items = _review(tmp_path, "세계 최초 케이크")
    assert any(i["blocking"] and i["status"] == "수정 필요" and "최초" in i["item"] and "표시·광고의 공정화" in i["message"] for i in items)


def test_review_warns_when_phrase_came_from_user_input(tmp_path):
    items = _review(tmp_path, "지역 1등 케이크", source="지역 1등 케이크 가게")
    assert items and all(not i["blocking"] and i["status"] == "경고" for i in items)


def test_review_passes_clean_copy(tmp_path):
    items = _review(tmp_path, "오늘 구운 딸기 케이크")
    assert [i["status"] for i in items] == ["통과"]
