"""로고 자리 고정(브랜드 설정)과 제목 가독성 보강(문구 블록 뒤 판·그림자)."""
import pytest
from PIL import Image, ImageDraw

from tests.test_image_fit import FONT, _logo, _scene


def _busy_bright() -> Image.Image:
    """밝고 복잡한 배경: 평균색은 중간이지만 흰 조각이 많아 평균 대비만 보면 글자가 묻힌다."""
    im = Image.new("RGB", (1024, 1536), (120, 120, 120))
    d = ImageDraw.Draw(im)
    for y in range(0, 1536, 24):
        for x in range(0, 1024, 24):
            if (x // 24 + y // 24) % 2 == 0:
                d.rectangle((x, y, x + 23, y + 23), fill=(250, 250, 250))
    return im


@pytest.mark.parametrize("pos", ["좌상단", "우상단", "좌하단", "우하단"])
def test_fixed_logo_position_is_respected_on_vertical(pos):
    from app import compose
    _, rep = compose.compose(_scene(), 1440, 1800, "직구처럼 넓게, 리빙은 쿠팡에서", FONT, _logo(),
                             None, 10, "#000000", "#FFFFFF", logo_position=pos)
    assert rep["logo_placement"]["corner"] == pos and rep["logo_placement"]["mode"] == "브랜드 고정"
    logo = next(e for e in rep["elements"] if e["type"] == "logo")
    text = next(e for e in rep["elements"] if e["type"] == "text")
    assert logo["box"][3] <= text["box"][1] or logo["box"][1] >= text["box"][3], "로고와 문구가 겹치지 않는다"


def test_fixed_bottom_left_on_horizontal_stacks_text_above_logo():
    from app import compose
    _, rep = compose.compose(_scene(), 1200, 600, "제목", FONT, _logo(), None, 10, "#000000", "#FFFFFF", logo_position="좌하단")
    assert rep["logo_placement"]["corner"] == "좌하단"
    logo = next(e for e in rep["elements"] if e["type"] == "logo")
    text = next(e for e in rep["elements"] if e["type"] == "text")
    assert text["box"][3] <= logo["box"][1]


def test_auto_mode_when_position_missing_or_auto():
    from app import compose
    for pos in (None, "자동"):
        _, rep = compose.compose(_scene(), 1440, 1800, "제목", FONT, _logo(), None, 10, "#000000", "#FFFFFF", logo_position=pos)
        assert rep["logo_placement"]["mode"] == "자동"


def test_same_corner_across_placements_when_fixed():
    """같은 캠페인의 세로·정사각·가로 지면이 모두 같은 모서리를 쓴다(이전에는 지면마다 제각각)."""
    from app import compose
    corners = set()
    for w, h in ((1440, 1800), (1200, 1200), (1200, 600), (1440, 2560)):
        _, rep = compose.compose(_scene(), w, h, "제목", FONT, _logo(), None, 10, "#000000", "#FFFFFF", logo_position="우상단")
        corners.add(rep["logo_placement"]["corner"])
    assert corners == {"우상단"}


def test_logo_is_pasted_as_original_without_plate():
    """복잡한 배경 위에서도 로고 뒤에 반투명 판을 깔지 않고, 로고 픽셀은 원본(크기만 조정) 그대로다."""
    from PIL import Image
    from app import compose
    assert not hasattr(compose, "LOGO_PLATE_BUSYNESS") and not hasattr(compose, "_logo_plate")
    logo = _logo()
    img, rep = compose.compose(_busy_bright(), 1440, 1800, "제목", FONT, logo, None, 10, "#000000", "#FFFFFF",
                               logo_position="좌상단")
    assert not any("판" in w for w in rep["warnings"])
    x0, y0, x1, y1 = next(e for e in rep["elements"] if e["type"] == "logo")["box"]
    lg = logo.resize((x1 - x0, y1 - y0), Image.LANCZOS)
    region = img.crop((x0, y0, x1, y1)).convert("RGB")
    solid = [(region.getpixel((x, y)), lg.getpixel((x, y))[:3]) for y in range(0, y1 - y0, 3) for x in range(0, x1 - x0, 3)
             if lg.getpixel((x, y))[3] == 255]
    assert solid and all(a == b for a, b in solid)


def test_text_backing_added_on_bright_busy_background():
    from app import compose
    _, rep = compose.compose(_busy_bright(), 1440, 1800, "밝은 배경 위 제목", FONT, None, None, None, "#FFFFFF", "#000000")
    assert "text_backing" in rep, rep.get("warnings")
    tb = rep["text_backing"]
    text = next(e for e in rep["elements"] if e["type"] == "text")
    assert tb["box"][0] <= text["box"][0] and tb["box"][2] >= text["box"][2] and tb["box"][1] <= text["box"][1]


def test_text_backing_not_added_on_flat_background():
    from app import compose
    flat = Image.new("RGB", (1024, 1536), (20, 20, 20))
    _, rep = compose.compose(flat, 1440, 1800, "제목", FONT, None, None, None, "#FFFFFF", "#000000")
    assert "text_backing" not in rep


def test_min_local_contrast_sees_bright_patch_average_hides():
    from app import compose
    im = Image.new("RGB", (600, 300), (0, 0, 0))
    ImageDraw.Draw(im).rectangle((0, 0, 100, 300), fill=(255, 255, 255))     # 왼쪽 1/6 만 흰색
    white = (255, 255, 255)
    assert compose.contrast(white, im.resize((1, 1), Image.BOX).getpixel((0, 0))) > 4.5     # 평균 대비는 충분해 보인다
    assert compose.min_local_contrast(im, white) < 1.5                                       # 칸별로 보면 흰 칸이 걸린다


def test_draw_text_soft_keeps_size_and_draws():
    from PIL import ImageFont
    from app import compose
    im = Image.new("RGB", (400, 200), (30, 30, 30))
    out = compose.draw_text_soft(im, [(20, 60, "가독성")], ImageFont.truetype(FONT, 48), (255, 255, 255))
    assert out.size == im.size and out.mode == "RGB"
    assert out.getpixel((200, 100)) != (30, 30, 30) or any(out.getpixel((x, 80)) != (30, 30, 30) for x in range(20, 200))


def test_brand_schema_validates_logo_position():
    from app.schemas import BrandIn
    assert BrandIn(name="b", logo_position="우하단").logo_position == "우하단"
    assert BrandIn(name="b").logo_position is None
    with pytest.raises(ValueError):
        BrandIn(name="b", logo_position="가운데")


def test_editing_render_text_matches_compose_shadow_style():
    """편집 렌더도 같은 그림자 스타일을 쓴다(편집한 지면만 글자가 달라 보이지 않게)."""
    from app import editing
    bg = Image.new("RGB", (400, 200), (30, 30, 30))
    els = [{"type": "text", "z_index": 3, "props": {"text": "제목", "x": 20, "y": 40, "w": 300, "font_size": 40,
                                                     "color": "#FFFFFF", "align": "왼쪽", "font": {}}}]
    out, m = editing.render(bg, 400, 200, els, lambda r: FONT, lambda a: None)
    assert m["text"]["lines"] == ["제목"] and out.size == (400, 200)


@pytest.mark.parametrize("pos", ["좌상단", "우하단"])
def test_cta_corner_is_button_shape_not_logo_position(pos):
    """로고와 CTA 를 함께 합성하면 CTA 의 corner 는 버튼 모양(둥근/각진)이어야 한다.
    예전에는 로고 모서리 변수가 인자 corner 를 덮어써 '좌상단' 같은 로고 위치가 기록됐고, 편집 미리보기가 400 으로 거절됐다."""
    from app import compose, editing
    for shape in ("둥근", "각진"):
        _, rep = compose.compose(_scene(), 1440, 1800, "제목", FONT, _logo(), None, 10, "#000000", "#FFFFFF",
                                 cta="혜택 확인하기", accent_hex="#B8860B", corner=shape, logo_position=pos)
        assert rep["logo_placement"]["corner"] == pos
        cta = next(e for e in rep["elements"] if e["type"] == "cta")
        assert cta["corner"] == shape
        # 편집기 요소로 옮겨도 검증을 통과한다
        els = editing.default_elements(rep, {"source": "provided", "name": "Pretendard-Bold"}, 1)
        cta_el = next(e for e in els if e["type"] == "cta")
        assert editing.validate_props("cta", cta_el["props"], (1440, 1800))["corner"] == shape


def test_legacy_cta_corner_with_logo_position_is_normalized():
    """이미 저장된 예전 데이터(corner 에 로고 위치)는 둥근으로 읽어 편집 미리보기가 거절되지 않는다."""
    from app import editing
    props = {"text": "혜택 확인하기", "x": 86, "y": 1597, "w": 430, "h": 117, "font_size": 58, "bg_color": "#B8860B",
             "text_color": "#000000", "font": {"source": "provided", "name": "Pretendard-Bold"}, "corner": "좌상단"}
    assert editing.validate_props("cta", props, (1440, 1800))["corner"] == "둥근"
    assert editing.cta_corner("각진") == "각진" and editing.cta_corner(None) == "둥근"


def test_long_body_is_never_ellipsized():
    """본문이 길어도 말줄임표로 줄이지 않는다: 글자 크기를 줄여 상한 안에 넣고, 그래도 넘치면 모든 줄을 그대로 둔다."""
    from app import compose
    body = "출퇴근길 15분 강의로 30일 동안 영어 회화 감각을 차근차근 익히고 실전 표현까지 자연스럽게 말해 보세요. " * 3
    for w, h in ((1440, 1800), (1200, 1200), (1200, 600), (1440, 2560)):
        _, rep = compose.compose(_scene(), w, h, "출퇴근길 영어 루틴", FONT, None, None, None, "#FFFFFF", "#000000",
                                 body=body, cta="지금 시작하기", body_font_path=FONT)
        el = next((e for e in rep["elements"] if e["type"] == "body"), None)
        if el is None:                       # 자리가 정말 없으면 본문을 통째로 뺀다(잘라서 넣지 않음)
            assert any("본문을 이미지에서 뺐습니다" in x for x in rep["warnings"]), rep["warnings"]
            continue
        assert not any("…" in ln for ln in el["lines"]) and el["cut"] is False
        assert "".join(el["lines"]).replace(" ", "") == body.replace(" ", "").strip(), "본문 글자가 하나도 빠지지 않는다"
