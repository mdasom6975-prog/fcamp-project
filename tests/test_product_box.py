"""제품 누끼 배치 상자(compose.product_box): 합성(paste_product)과 같은 값을 미리 계산할 수 있어야 한다."""
from PIL import Image, ImageDraw

from app import compose


def _cutout(w: int, h: int, box: tuple[int, int, int, int]) -> Image.Image:
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle(box, fill=(10, 120, 200, 255))
    return im


def test_product_box_matches_paste_product():
    bg = Image.new("RGB", (1024, 1536), (240, 240, 240))
    prod = _cutout(300, 500, (50, 20, 250, 480))
    for template in ("세로형", "정사각", "가로형"):
        _, placed = compose.paste_product(bg, prod, template)
        assert compose.product_box(1024, 1536, prod, template) == placed
    assert placed["src"] == [201, 461], "투명 여백을 잘라낸 크기"


def test_tall_cutout_fills_slot_height_only():
    prod = _cutout(400, 1600, (0, 0, 399, 1599))           # 1:4 길쭉한 병
    p = compose.product_box(1024, 1536, prod, "세로형")
    (sx0, sy0, sx1, sy1), (x0, y0, x1, y1) = p["slot"], p["box"]
    assert y1 - y0 == sy1 - sy0 and (x1 - x0) < (sx1 - sx0) / 2, "높이에 맞추고 가로는 구역의 절반도 안 쓴다"


def test_small_cutout_is_capped_by_max_upscale():
    prod = _cutout(120, 160, (0, 0, 119, 159))             # 저해상도 누끼
    p = compose.product_box(1024, 1536, prod, "세로형")
    x0, y0, x1, y1 = p["box"]
    assert p["scale"] == compose.PRODUCT_MAX_UPSCALE and (x1 - x0, y1 - y0) == (180, 240)
    assert y1 == p["slot"][3], "작아도 받침(구역 아랫변)에 발을 붙인다"
