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


def test_prompt_slot_follows_cutout_box():
    """배경 프롬프트가 비우는 자리 = 실제로 붙일 상자 + 사방 SLOT_MARGIN. 누끼 정보가 없으면 예전 고정 구역."""
    from app import imagegen
    prod = _cutout(400, 1600, (0, 0, 399, 1599))
    p = compose.product_box(1024, 1536, prod, "세로형")
    v = imagegen.product_slot_vars("세로형", p)
    x0, y0, x1, y1 = p["box"]
    m = imagegen.SLOT_MARGIN
    assert v["slot"] == (f"가로 {round((x0 / 1024 - m) * 100)}~{round((x1 / 1024 + m) * 100)}%, "
                         f"세로 {round((y0 / 1536 - m) * 100)}~{round((y1 / 1536 + m) * 100)}%")
    assert "세로로 긴 물건(가로:세로 1:4.0)" in v["shape"] and f"세로 {round(y1 / 1536 * 100)}% 높이" in v["shape"]
    assert imagegen.product_slot_vars("세로형") == {"slot": "가로 14~86%, 세로 6~66%", "shape": ""}
    wide = imagegen.product_slot_vars("가로형", compose.product_box(1536, 1024, _cutout(900, 300, (0, 0, 899, 299)), "가로형"))
    assert "가로로 넓은 물건(가로:세로 3.0:1)" in wide["shape"]


def test_product_scale_shrinks_slot_on_the_same_floor():
    """제품 크기: 구역 가로·세로에 배율을 곱하고, 세로형·정사각은 받침(아랫변) 가운데, 가로형은 구역 가운데를 기준으로 줄인다."""
    for template in ("세로형", "정사각", "가로형"):
        x0, y0, x1, y1 = compose.PRODUCT_SLOT[template]
        assert compose.product_slot(template, "크게") == compose.product_slot(template)
        for size, k in compose.PRODUCT_SCALES.items():
            a0, b0, a1, b1 = compose.product_slot(template, size)
            assert abs((a1 - a0) - (x1 - x0) * k) < 1e-9 and abs((b1 - b0) - (y1 - y0) * k) < 1e-9
            assert abs((a0 + a1) - (x0 + x1)) < 1e-9, "가로 가운데 유지"
            if template == "가로형":
                assert abs((b0 + b1) - (y0 + y1)) < 1e-9
            else:
                assert abs(b1 - y1) < 1e-9, "받침 높이 유지"
    prod = _cutout(400, 600, (0, 0, 399, 599))
    big, small = (compose.product_box(1024, 1536, prod, "세로형", s) for s in ("크게", "작게"))
    assert big["box"][3] == small["box"][3] and (small["box"][3] - small["box"][1]) < (big["box"][3] - big["box"][1]) * 0.6


# ---- 지면마다 누끼 다시 붙이기(compose.crop_scene·fit_product_box): 같은 비율군이라도 지면 비율에 따라 잘리는 양이 다르다 ----
def test_fit_product_box_keeps_inside_or_shrinks_on_floor():
    from app import compose
    placed = {"box": [800, 80, 1260, 900]}          # 1536×1024 가로형 배경에서 세로 80%를 차지하는 누끼
    # 1200×628 로 자를 때(배율 0.78125, 위 12px 부터): 아래가 넘쳐 비율을 지켜 줄이고 바닥은 캔버스 안쪽에
    box, k = compose.fit_product_box(placed, 1200 / 1536, 0, 12, 1200, 628)
    assert k < 1 and 0 <= box[1] and box[3] <= 628 - 628 * compose.PRODUCT_FIT_PAD + 1
    w0, h0 = 460, 820
    assert abs((box[2] - box[0]) / (box[3] - box[1]) - w0 / h0) < 0.01          # 비율 유지
    assert abs((box[0] + box[2]) / 2 - (800 + 1260) / 2 * 1200 / 1536) <= 1      # 가로 중심 유지
    # 다 들어가는 지면은 그대로(배율 1, 옮기지 않음)
    box, k = compose.fit_product_box(placed, 1080 / 1024, 300, 0, 1080, 1080)
    assert k == 1.0 and box == [round(800 * 1080 / 1024 - 300), round(80 * 1080 / 1024), round(1260 * 1080 / 1024 - 300), round(900 * 1080 / 1024)]
    # 옆으로만 넘치면 크기는 그대로 두고 안쪽으로 옮긴다
    box, k = compose.fit_product_box(placed, 1080 / 1024, 20, 0, 1080, 1080)
    assert k == 1.0 and box[2] <= 1080 and box[2] - box[0] == round(460 * 1080 / 1024)


def test_crop_scene_repastes_cutout_so_it_is_not_cut():
    from PIL import Image, ImageDraw
    from app import compose
    clean = Image.new("RGB", (1536, 1024), (200, 210, 220))
    product = Image.new("RGBA", (400, 800), (0, 0, 0, 0))
    ImageDraw.Draw(product).rectangle([20, 20, 380, 780], fill=(230, 90, 70, 255))
    bg, placed = compose.paste_product(clean, product, "가로형")
    old, _, _ = compose.crop_scene(bg, 1200, 628)
    new, crop, fit = compose.crop_scene(bg, 1200, 628, clean=clean, product=product, placed=placed)
    red = lambda im: (im.convert("RGB").getchannel("R").point(lambda v: 255 if v > 225 else 0).getbbox())
    assert red(old)[3] == 628                                   # 붙인 채로 자르면 아래가 잘린다
    x0, y0, x1, y1 = red(new)
    assert y0 > 0 and y1 < 628 and fit["scale"] < 1 and "줄였습니다" in fit["note"]
    cb = crop["content_box_canvas"]
    assert cb[0] <= fit["box"][0] and cb[3] >= fit["box"][3]    # 문구가 피하는 내용 상자에 누끼 자리가 들어간다
