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

    def fake_ask(system: str, user: str, kind: str = "icon_names", schema: dict | None = None) -> dict:
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


# ---- 3D 아이콘 (icons, ICON_RENDER=3d): 제안은 _ask 가로채기, 렌더 문장은 순수 함수 ----
PALETTE = [c for c in BRAND["colors"] if c["role"] in ("주조색", "보조색", "강조색")]


def test_icon3d_propose_prompt(capture_ask):
    from app import icons
    icons.propose3d("쿠챠", BRIEF, BRAND["forbidden_phrases"])
    _assert_snapshot("icons3d__propose", _dump(capture_ask))


def test_icon3d_propose_prompt_minimal_brief(capture_ask):
    from app import icons
    icons.propose3d("쿠챠", BRIEF_MIN, [])
    _assert_snapshot("icons3d__propose_minimal", _dump(capture_ask))


def test_icon3d_more_prompt(capture_ask):
    from app import icons
    icons.propose3d_more("쿠챠", BRIEF, BRAND["forbidden_phrases"], ["보관함", "배송 조회"], 3)
    _assert_snapshot("icons3d__more", _dump(capture_ask))


def test_icon3d_render_prompt():
    from app import icons
    vars = icons.style_vars(STYLE, PALETTE, BRAND["image_tone"])
    _assert_snapshot("icons3d__render", icons.render_prompt("보관함", "뚜껑이 살짝 열린 둥근 상자", vars) + "\n")


def test_icon3d_render_prompt_minimal():
    """브랜드 색 역할·톤 설명이 없을 때의 분기(아이콘 색 하나, '지정 없음')."""
    from app import icons
    vars = icons.style_vars(STYLE, [], {})
    _assert_snapshot("icons3d__render_minimal", icons.render_prompt("보관함", "", vars) + "\n")


def test_pin_grid_prompt():
    """핀 뱃지 격자: 로고 첨부 + 단어·이름 + 아이콘 주제 5개."""
    from app import icons
    sv = icons.pin_style_vars(BRAND["colors"], BRAND["image_tone"])
    topics = [("보관함", "뚜껑이 살짝 열린 둥근 상자"), ("선물", "리본을 묶은 작은 상자"), ("알림", "둥근 종"),
              ("수집", "캡슐 세 개가 담긴 진열장"), ("공개", "반쯤 열린 캡슐")]
    cells = icons.pin_cells("쿠챠", "호기심", topics, has_logo=True)
    _assert_snapshot("pins__grid", icons.pin_grid_prompt("쿠챠", cells, sv, ["최저가"]) + "\n")


def test_pin_one_prompt_minimal():
    """핀 1개 다시 그리기 + 로고·브랜드 색·톤이 없을 때의 분기(모노그램, '지정 없음')."""
    from app import icons
    cell = icons.pin_cells("쿠챠", "호기심", [("a", "")] * 5, has_logo=False)[0]
    _assert_snapshot("pins__one_minimal", icons.pin_one_prompt("쿠챠", cell, icons.pin_style_vars([], {}), []) + "\n")


# ---- 배경 이미지 (imagegen) ----
@pytest.mark.parametrize("template", ["가로형", "정사각", "세로형"])
def test_background_prompt(template):
    from app import imagegen
    _assert_snapshot(f"image__{template}", imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, template) + "\n")


def test_background_prompt_minimal():
    from app import imagegen
    _assert_snapshot("image__minimal", imagegen.build_prompt("쿠챠", {}, BRIEF_MIN, "정사각") + "\n")


@pytest.mark.parametrize("style", ["실사 인물", "실사 제품·공간", "플랫 일러스트", "3D 렌더", "그래픽 패턴"])
def test_background_prompt_styles(style):
    """배경 형태(image/style/*.md)별 문장. 템플릿은 정사각으로 고정한다."""
    from app import imagegen
    _assert_snapshot(f"image__style_{imagegen.STYLES[style]}",
                     imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, "정사각", style) + "\n")


def test_background_product_prompt():
    """제품 누끼 참조 문장 (image/product_ref.md + 정사각 배경 프롬프트), 비율군 변형과 함께 쓸 때."""
    from app import imagegen
    base = imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, "정사각", "실사 제품·공간", product=True)
    _assert_snapshot("image__product_정사각", base + "\n")
    base = imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, "가로형", "실사 제품·공간", product=True, variant=True)
    _assert_snapshot("image__product_variant_가로형", imagegen.build_variant_prompt(base) + "\n")


def test_background_variant_prompt():
    """참조 이미지와 함께 보내는 '같은 장면' 변형 프롬프트 (image/same_scene.md + 가로형 배경 프롬프트)."""
    from app import imagegen
    base = imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, "가로형", "실사 제품·공간")
    _assert_snapshot("image__variant_가로형", imagegen.build_variant_prompt(base) + "\n")


def test_background_brand_colors_prompt():
    """[브랜드 색] 문장 (image/_brand_colors.md): 브랜드 배경색이 있을 때 / 없을 때(밝은 중성색) / 강조색이 없을 때(주조색)."""
    from app import imagegen
    _assert_snapshot("image__brand_colors_세로형",
                     imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, "세로형", colors=BRAND["colors"]) + "\n")
    no_bg = [c for c in BRAND["colors"] if c["role"] != "배경색"]
    no_accent = [c for c in BRAND["colors"] if c["role"] != "강조색"]
    _assert_snapshot("image__brand_colors_lines",
                     imagegen.brand_color_line(no_bg).strip() + "\n" + imagegen.brand_color_line(no_accent).strip() + "\n")
    assert imagegen.brand_color_line([{"role": "배경색", "hex": "#FFFFFF"}]) == ""
    assert (imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, "세로형", colors=[])
            == imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, "세로형"))


@pytest.mark.parametrize("layout,template", [("상단 문구", "세로형"), ("좌우 반전", "가로형")])
def test_background_layout_prompt(layout, template):
    """레이아웃 토큰이 구도 문장(image/composition)의 피사체·문구 방향과 비율, 제품 구역을 바꾼다. 문구는 그리지 않는 composite 로 확인한다."""
    from app import imagegen
    base = imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, template, "실사 제품·공간", product=True, composite=True,
                                 layout=layout)
    _assert_snapshot(f"image__layout_{layout.replace(' ', '')}_{template}", base + "\n")


# ---- 분석 화면 비전 채점 (analysis): 이미지는 보내지 않고 문장만 고정한다 ----
def test_judge_prompt():
    from app import analysis, prompts
    from types import SimpleNamespace
    bv = SimpleNamespace(image_tone=BRAND["image_tone"])
    vars = analysis.judge_vars(SimpleNamespace(name="쿠챠"), bv, BRIEF, "실사 제품·공간")
    _assert_snapshot("analysis__judge", f"[SYSTEM]\n{prompts.render('analysis/judge_system')}\n\n[USER]\n{prompts.render('analysis/judge_user', **vars)}\n")


def test_background_product_slot_prompt():
    """제품을 그리지 않고 구역을 비워 두는 프롬프트 (image/product_slot.md), 비율군 변형과 함께 쓸 때."""
    from app import imagegen
    base = imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, "세로형", "실사 제품·공간", product=True, composite=True)
    _assert_snapshot("image__product_slot_세로형", base + "\n")
    base = imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, "가로형", "실사 제품·공간", product=True, variant=True, composite=True)
    _assert_snapshot("image__product_slot_variant_가로형", imagegen.build_variant_prompt(base) + "\n")


def test_background_product_slot_prompt_sized_to_cutout():
    """누끼 크기를 알면 고정 구역 대신 누끼가 실제로 놓일 자리(compose.product_box + 사방 3%)만 비우고 모양을 알려 준다."""
    from PIL import Image
    from app import compose, imagegen
    tall = Image.new("RGBA", (300, 900), (10, 120, 200, 255))      # 1:3 길쭉한 병
    placed = compose.product_box(1024, 1536, tall, "세로형")
    text = imagegen.build_prompt("쿠챠", BRAND["image_tone"], BRIEF, "세로형", "실사 제품·공간", product=True, composite=True,
                                 placed=placed)
    _assert_snapshot("image__product_slot_placed_세로형", text + "\n")


@pytest.mark.parametrize("style", ["실사 인물", "실사 제품·공간", "플랫 일러스트", "3D 렌더", "그래픽 패턴"])
def test_no_people_styles_forbid_people_everywhere(style):
    """사람 없는 형태는 어디에서도 사람이 있다는 전제를 두지 않고, 금지를 끝에서 한 번 더 말한다(시안 12: 실사 제품·공간에 인물)."""
    from app import imagegen
    tone = {"description": "거리에서 백팩을 멘 청년을 담은 스트리트 사진 느낌의 실사 인물"}
    text = imagegen.build_prompt("모노백", tone, BRIEF, "세로형", style, product=True, composite=True)
    if style in imagegen.NO_PEOPLE_STYLES:
        assert "사람이 등장하는 장면이면" not in text and "사람이나 주 피사체" not in text
        assert "타깃(그리지 않음, 분위기 참고)" in text and "톤 문장에 인물이 나와도" in text
        assert text.rstrip().endswith("한 조각도 없다.")
    else:
        assert "사람이 등장하는 장면이면" in text and "타깃: " in text and "한 조각도 없다" not in text


def test_no_people_tone_drops_person_sentences_and_composite_scene_is_empty_place():
    """시안 12 2차: 톤 문장의 '백팩을 멘 청년' 같은 사람 묘사는 사람 없는 형태에서 빼고, 투명 누끼 합성이면 [장면]은 '상황'이 아니라
    상품이 내려놓일 빈 장소다. 인물 형태는 톤·장면을 그대로 둔다."""
    from app import imagegen
    tone = {"description": "거리·캠퍼스에서 백팩을 멘 청년을 담은 스트리트 사진 느낌의 실사 인물. 라임 포인트, 대비 강한 모노톤. 금지: 이미지 안의 글자, 가짜 로고."}
    assert imagegen.tone_text("실사 제품·공간", tone["description"]) == "라임 포인트, 대비 강한 모노톤. 금지: 이미지 안의 글자, 가짜 로고"
    assert imagegen.tone_text("실사 인물", tone["description"]) == tone["description"]
    assert imagegen.tone_text("3D 렌더", "밝은 인물 사진.") == "지정 없음"
    assert imagegen.tone_text("3D 렌더", "아이콘처럼 단순한 형태.") == "아이콘처럼 단순한 형태", "단어 일부(아이콘)는 사람으로 보지 않는다"
    brief = {"product_name": "모노 노트북 백팩 20L", "key_message": "출근길도 가볍게", "target": "통학·출퇴근하는 20대"}
    text = imagegen.build_prompt("모노백", tone, brief, "세로형", "실사 제품·공간", product=True, composite=True)
    assert "백팩을 멘" not in text and "청년" not in text and "가방·옷" not in text
    assert "상품이 내려놓일 장소 한 곳을 그린다" in text and "상황을 한 장면으로" not in text
    assert "'주 피사체'는 이 비워 둔 상품 자리를 뜻한다" in text
    people = imagegen.build_prompt("모노백", tone, brief, "세로형", "실사 인물", product=True, composite=True)
    assert "백팩을 멘 청년" in people and "상황을 한 장면으로" in people
    plain = imagegen.build_prompt("모노백", tone, brief, "세로형", "실사 제품·공간")
    assert "상황을 한 장면으로" in plain and "청년" not in plain, "합성이 아니면 기본 장면, 톤 거르기는 그대로"
    refs = imagegen.prompt_refs("세로형", "실사 제품·공간", composite=True)
    assert any(r.startswith("image/_scene_place@") for r in refs)
