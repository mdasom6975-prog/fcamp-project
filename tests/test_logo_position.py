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


def test_logo_plate_threshold_lowered():
    from app import compose
    assert compose.LOGO_PLATE_BUSYNESS <= 0.5


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
