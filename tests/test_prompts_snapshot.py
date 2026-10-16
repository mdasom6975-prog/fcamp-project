"""프롬프트 고정(스냅샷) 테스트.

카피·아이콘·배경 이미지 프롬프트를 고정 입력으로 렌더링한 결과가 tests/fixtures/prompts/*.txt 와 글자 하나까지 같은지 확인한다.
프롬프트를 md 파일로 옮기거나 로더를 바꿀 때 "문장은 그대로"임을 보장하는 안전망이다.
(2일차 원칙: 한 번에 하나만 바꾼다 — 구조를 옮길 때 문장까지 같이 바꾸지 않는다)

의도적으로 문장을 바꿨다면: UPDATE_PROMPT_SNAPSHOTS=1 backend/.venv/bin/python -m pytest tests/test_prompts_snapshot.py -q
API 는 호출하지 않는다.
"""
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "prompts"
UPDATE = os.getenv("UPDATE_PROMPT_SNAPSHOTS") == "1"

BRAND = json.loads((ROOT / "brands" / "kucha" / "brand.json").read_text(encoding="utf-8"))["brand"]
PRESETS = {p["placement"]: p for p in json.loads((ROOT / "specs" / "presets.json").read_text(encoding="utf-8"))["presets"]}
BRIEF = {
    "product_name": "쿠챠 랜덤 캡슐",
    "description": "실물 캐릭터 굿즈를 무작위로 받아 보는 온라인 랜덤 커머스",
    "key_message": "오늘 어떤 캐릭터를 만나게 될까",
    "target": "20~30대 캐릭터 굿즈 수집가",
}
BRIEF_MIN = {"product_name": "쿠챠 랜덤 캡슐"}   # 선택 항목이 비었을 때의 '없음' 분기
STYLE = {"stroke_width": 2, "corner": "둥근", "fill": "윤곽", "color": "#4C69FE"}
# 글자 수 제한 유형이 다른 지면 셋: 글자 수 / 전각 2자 계산(필수 필드 포함) / px 길이(생성 제외)
PLACEMENTS = ["Instagram 피드", "PMax 가로 1.91:1", "비즈보드 배너 1029×258"]


def _placement(name: str) -> dict:
    p = PRESETS[name]
    return {"platform": p["platform"], "name": p["placement"]}


def _assert_snapshot(name: str, text: str) -> None:
    path = FIXTURES / f"{name}.txt"
    if UPDATE or not path.exists():
        FIXTURES.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if not UPDATE:
            pytest.fail(f"스냅샷이 없어 새로 만들었습니다: {path.relative_to(ROOT)} — 내용을 확인하고 커밋하세요")
        return
    expected = path.read_text(encoding="utf-8")
    assert text == expected, f"프롬프트가 스냅샷({path.relative_to(ROOT)})과 다릅니다. 의도한 변경이면 UPDATE_PROMPT_SNAPSHOTS=1"


# ---- 카피 (copywriter) ----
@pytest.mark.parametrize("placement", PLACEMENTS)
def test_copy_prompt(placement):
    from app import copywriter
    limits = copywriter.limits_for(PRESETS[placement]["text_limits"])
    system, user = copywriter._prompt("쿠챠", BRAND, BRIEF, _placement(placement), limits)
    _assert_snapshot(f"copy__{placement}", f"[SYSTEM]\n{system}\n\n[USER]\n{user}\n")


def test_copy_prompt_minimal_brief_and_voice():
    """브리프 선택 항목·어조 설정이 비었을 때의 분기('없음', '지정 없음')."""
    from app import copywriter
    brand = dict(BRAND, voice={"description": "", "headline_max_chars": 20, "polite": False},
                 required_phrases=[], forbidden_phrases=[])
    limits = copywriter.limits_for([])
    system, user = copywriter._prompt("쿠챠", brand, BRIEF_MIN, _placement("Instagram 피드"), limits)
    _assert_snapshot("copy__minimal", f"[SYSTEM]\n{system}\n\n[USER]\n{user}\n")


# ---- 아이콘 (icons): _ask 를 가로채 system/user 만 기록한다 ----
@pytest.fixture
def capture_ask(monkeypatch):
    from app import icons
    calls: list[tuple[str, str]] = []

    def fake_ask(system: str, user: str) -> dict:
        calls.append((system, user))
        return {"icons": [{"name": "보관함", "concept": "상자", "elements": [{"tag": "rect", "attrs": {"x": "4", "y": "4", "width": "16", "height": "16"}}]}]}

    monkeypatch.setattr(icons, "_ask", fake_ask)
    return calls


def _dump(calls: list[tuple[str, str]]) -> str:
    return "".join(f"[SYSTEM]\n{s}\n\n[USER]\n{u}\n" for s, u in calls)


def test_icon_propose_prompt(capture_ask):
    from app import icons
    icons.propose_and_draw("쿠챠", BRIEF, STYLE, BRAND["forbidden_phrases"])
    _assert_snapshot("icons__propose", _dump(capture_ask))


def test_icon_propose_prompt_minimal_brief(capture_ask):
    from app import icons
    icons.propose_and_draw("쿠챠", BRIEF_MIN, STYLE, [])
    _assert_snapshot("icons__propose_minimal", _dump(capture_ask))


def test_icon_redraw_prompt(capture_ask):
    from app import icons
    icons.redraw_one("쿠챠", BRIEF, "보관함", "뚜껑이 열린 상자", STYLE)
    _assert_snapshot("icons__redraw", _dump(capture_ask))


def test_icon_more_prompt(capture_ask):
    from app import icons
    icons.propose_more("쿠챠", BRIEF, STYLE, BRAND["forbidden_phrases"], ["보관함", "배송 조회"], 3)
    _assert_snapshot("icons__more", _dump(capture_ask))


# ---- 배경 이미지 (imagegen) ----
@pytest.mark.parametrize("template", ["가로형", "정사각", "세로형"])
def test_background_prompt(template):
    from app import imagegen
    _assert_snapshot(f"image__{template}", imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, template) + "\n")


def test_background_prompt_minimal():
    from app import imagegen
    _assert_snapshot("image__minimal", imagegen.build_prompt("쿠챠", {}, BRIEF_MIN, "정사각") + "\n")
