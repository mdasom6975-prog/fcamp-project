"""3D 아이콘 후처리(icons.fit_alpha) 검사. API 는 호출하지 않는다.

투명 PNG 를 알파 경계로 잘라 live area(20/24)에 중앙 정렬하는 규칙과, 배경이 투명하지 않게 나온 경우의 감지를 고정한다."""
from PIL import Image, ImageDraw


def _blob(bg=(0, 0, 0, 0), box=(300, 200, 700, 900)) -> Image.Image:
    im = Image.new("RGBA", (1024, 1024), bg)
    ImageDraw.Draw(im).ellipse(box, fill=(76, 105, 254, 255))
    return im


def test_fit_alpha_centers_in_live_area():
    from app import icons
    out, info = icons.fit_alpha(_blob(), 256, 256)
    assert out.size == (256, 256) and out.mode == "RGBA"
    x0, y0, x1, y1 = info["bbox"]
    assert abs(min(x0, y0, 24 - x1, 24 - y1) - 2.0) < 0.1, "긴 변이 live area(20) 에 닿아 여백이 2 여야 한다"
    assert abs((x0 + x1) / 2 - 12) < 0.1 and abs((y0 + y1) / 2 - 12) < 0.1, "중앙 정렬"
    assert info["transparent"] is True
    assert out.getpixel((0, 0))[3] == 0 and out.getpixel((128, 128))[3] == 255


def test_fit_alpha_non_square_canvas_uses_short_side():
    from app import icons
    out, info = icons.fit_alpha(_blob(box=(100, 400, 900, 600)), 400, 200)
    assert out.size == (400, 200)
    assert abs(info["bbox"][1] - 9.5) < 0.2 and abs(info["bbox"][3] - 14.5) < 0.2, "넓은 도형도 짧은 변(200) 기준 20/24 안에 들어간다"


def test_fit_alpha_detects_opaque_background():
    from app import icons
    _, info = icons.fit_alpha(_blob(bg=(255, 255, 255, 255)), 256, 256)
    assert info["transparent"] is False


def test_fit_alpha_empty_image_fails():
    import pytest
    from app import icons
    with pytest.raises(ValueError):
        icons.fit_alpha(Image.new("RGBA", (64, 64), (0, 0, 0, 0)), 256, 256)
