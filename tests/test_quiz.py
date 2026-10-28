"""대기 화면 브랜드 퀴즈(quiz.build): 모델 호출 없이 브랜드 규칙·표현 사전으로 문항을 만든다."""
from types import SimpleNamespace

from app import quiz

BV = SimpleNamespace(
    colors=[{"role": "주조색", "hex": "#FC1C49"}, {"role": "보조색", "hex": "#C9032A"}, {"role": "강조색", "hex": "#FF7450"},
            {"role": "배경색", "hex": "#FFFFFF"}],
    logo_position="좌상단", required_phrases=["얼리버드"], forbidden_phrases=["업계"])


def test_build_makes_answerable_items():
    items = quiz.build("패스트캠퍼스", BV, {}, seed=3)
    kinds = [it["kind"] for it in items]
    assert kinds.count("color") == 3 and kinds.count("phrase") == 2 and kinds.count("logo") == 1
    assert kinds.count("legal") == quiz.LEGAL_PICK
    for it in items:
        assert 0 <= it["answer"] < len(it["options"]) and it["question"] and it["explain"]
        labels = [o["label"] for o in it["options"]]
        assert len(labels) == len(set(labels)), "보기가 겹치면 안 된다"
    color = next(it for it in items if it["question"].endswith("강조색은 어느 색일까요?"))
    assert color["options"][color["answer"]]["color"] == "#FF7450"
    forb = next(it for it in items if "쓰면 안 되는" in it["question"])
    assert forb["options"][forb["answer"]]["label"] == "업계"


def test_decoy_colors_are_not_close_to_brand_colors():
    from app.analysis import delta_e
    for seed in range(5):
        for it in quiz.build("패스트캠퍼스", BV, {}, seed=seed):
            if it["kind"] != "color":
                continue
            brand = {c["hex"] for c in BV.colors}
            for o in it["options"]:
                if o["color"] not in brand:
                    assert all(delta_e(o["color"], h) > quiz.DECOY_MIN_DE for h in brand)


def test_legal_items_skip_profanity_and_industry_entries():
    for seed in range(20):
        for it in quiz.build("브랜드", SimpleNamespace(colors=[], logo_position="자동", required_phrases=[], forbidden_phrases=[]), {}, seed=seed):
            assert it["kind"] == "legal", "브랜드 규칙이 비면 표현 규칙 문항만"
            assert "욕설" not in it["explain"]


def test_campaign_phrases_take_priority():
    items = quiz.build("브랜드", BV, {"forbidden_phrases": ["최저가 보장"]}, seed=1)
    forb = next(it for it in items if "쓰면 안 되는" in it["question"])
    assert forb["options"][forb["answer"]]["label"] == "최저가 보장" and "캠페인 입력" in forb["explain"]
