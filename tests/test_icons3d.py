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


def test_fit_alpha_flags_clipped_when_alpha_touches_edge():
    """알파가 원본 가장자리에 닿으면 오브젝트가 잘린 것 → clipped. 안쪽에 있으면 False."""
    from app import icons
    assert icons.fit_alpha(_blob(), 256, 256)[1]["clipped"] is False
    assert icons.fit_alpha(_blob(box=(300, -200, 700, 600)), 256, 256)[1]["clipped"] is True, "위가 잘림"
    assert icons.fit_alpha(_blob(box=(600, 200, 1100, 900)), 256, 256)[1]["clipped"] is True, "오른쪽이 잘림"


def test_fit_alpha_empty_image_fails():
    import pytest
    from app import icons
    with pytest.raises(ValueError):
        icons.fit_alpha(Image.new("RGBA", (64, 64), (0, 0, 0, 0)), 256, 256)


def test_style_vars_material_preset_or_auto():
    from app import icons
    auto = icons.style_vars({"corner": "둥근"}, [], {"description": "무광 레진"})
    assert auto["material"].startswith("아래 브랜드 톤")
    assert auto["tone_scope"] == "재질·광택·조명·형태", "자동이면 톤 설명의 재질·광택도 따른다"
    fixed = icons.style_vars({"corner": "둥근", "material": "클레이"}, [], {"description": "유리와 빛의 반사"})
    assert fixed["material"].startswith("클레이 재질로 통일한다") and fixed["tone_scope"] == "조명·형태"
    from app import prompts
    text = prompts.render("icons/_3d_style", **fixed)
    assert "재질: 클레이 재질로 통일한다." in text
    assert "조명·형태 묘사만 따르고" in text and "재질·광택·조명·형태" not in text, "프리셋이면 톤 설명의 재질·광택은 따르지 않는다"
    assert icons.material_label("자동").startswith("자동 (") and icons.material_label("금속") == "금속"


def test_review_flags_icons_with_other_material():
    from types import SimpleNamespace
    from app import review
    base = {"render": "3d", "style_ref": "s", "prompt_ref": "p", "model": "m", "quality": "low", "corner": "둥근", "colors": []}
    icons_ = [SimpleNamespace(name=f"기능{i}", style={**base, "material": "클레이"}, violations=[], width=256, height=256,
                              png_path="", source_path=None) for i in range(10)]
    brand = {"levels": {"icon_style": "권장"}, "icon_style": {"corner": "둥근", "material": "클레이"}, "colors": [],
             "forbidden_phrases": [], "required_phrases": []}
    items = {i["item"]: i for i in review.review_icons(icons_, brand)}
    assert items["아이콘 스타일"]["status"] == "통과"
    icons_[3].style = {**base, "material": "금속"}
    items = {i["item"]: i for i in review.review_icons(icons_, brand)}
    assert items["아이콘 스타일"]["status"] != "통과" and "기능3" in items["아이콘 스타일"]["message"]
