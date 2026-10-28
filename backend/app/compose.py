"""배경 이미지 위에 로고와 문구를 합성한다 (이미지 생성과 분리).

- 로고는 원본 비율을 그대로 쓰고 변형하지 않는다.
- 문구는 모델이 그리지 않고 여기서 폰트로 직접 그린다 (한글 오탈자 방지).
- 지면 비율에 따라 가로형 / 정사각 / 세로형 템플릿을 쓴다.
"""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

from . import storage

HORIZONTAL, SQUARE, VERTICAL = "가로형", "정사각", "세로형"
STRIP_RATIO = 2.6      # 띠 배너(320×100 등): 가로형의 하위 배치 — 왼쪽 이미지, 가운데 문구, 오른쪽 CTA
BODY_MAX_LINES = {VERTICAL: 2, SQUARE: 2, HORIZONTAL: 2}
CTA_MAX_CHARS = 8


def template_for(w: int, h: int) -> str:
    r = w / h
    return HORIZONTAL if r >= 1.25 else VERTICAL if r <= 0.8 else SQUARE


def is_strip(w: int, h: int) -> bool:
    return w / h >= STRIP_RATIO


def pick_text_color(region, text_color_hex: str | None) -> tuple[tuple[int, int, int], bool]:
    """브랜드 텍스트색이 배경과 대비 4.5:1 이상이면 그 색, 아니면 흰/검 중 더 대비가 큰 쪽. 반환: (색, 대체 여부)."""
    if text_color_hex:
        cand = _hex(text_color_hex)
        if contrast(cand, region) >= 4.5:
            return cand, False
    white_better = contrast((255, 255, 255), region) >= contrast((0, 0, 0), region)
    return ((255, 255, 255) if white_better else (0, 0, 0)), bool(text_color_hex)


def cta_colors(accent_hex: str | None, fallback_text: tuple[int, int, int]) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """CTA 버튼 배경(브랜드 주조색, 없으면 문구색의 반전)과 글자색(흰/검 중 대비가 큰 쪽)."""
    bg = _hex(accent_hex) if accent_hex else tuple(255 - c for c in fallback_text)
    fg = (255, 255, 255) if contrast((255, 255, 255), bg) >= contrast((0, 0, 0), bg) else (0, 0, 0)
    return bg, fg


def draw_cta(canvas: Image.Image, box: list[int], text: str, font_path: str, size: int, bg, fg, corner: str) -> None:
    """둥근 사각형 버튼 + 가운데 글자. corner 는 브랜드 아이콘 스타일(둥근/각진)."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    radius = h // 2 if corner != "각진" else max(2, round(h * 0.15))
    ImageDraw.Draw(canvas).rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=bg)
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(font_path, size)
    tw = draw.textlength(text, font=font)
    bb = font.getbbox(text)
    th = bb[3] - bb[1]
    draw.text((x0 + (x1 - x0 - tw) / 2, y0 + (h - th) / 2 - bb[1]), text, font=font, fill=fg)


def content_box(img: Image.Image) -> tuple[int, int, int, int] | None:
    """이미지에서 '내용이 있는' 영역(피사체·소품)의 상자를 어림한다. 경계선(질감)이 충분히 많은 행·열을 찾는다.
    단색 면(카피 스페이스)과 벽·바닥 경계 같은 한 줄짜리 선은 빠진다(선은 행 하나에만 많고 열마다는 1~2픽셀뿐이라
    5% 문턱에 못 미친다). 아무것도 못 찾으면 None. (10/19: 배경색과의 색 차이 기준은 두 색 벽·바닥을 전부 내용으로
    잡아 뺐다)"""
    W, H = img.size
    side = 96
    sw, sh = (side, max(8, round(side * H / W))) if W >= H else (max(8, round(side * W / H)), side)
    small = img.convert("RGB").resize((sw, sh), Image.BILINEAR)
    edge = small.convert("L").filter(ImageFilter.FIND_EDGES).load()
    rows, cols = [0] * sh, [0] * sw
    for y in range(1, sh - 1):
        for x in range(1, sw - 1):
            if edge[x, y] > 30:
                rows[y] += 1
                cols[x] += 1
    ys = [y for y, n in enumerate(rows) if n >= max(3, sw * 0.05)]
    xs = [x for x, n in enumerate(cols) if n >= max(3, sh * 0.05)]
    if not ys or not xs:
        return None
    fx, fy = W / sw, H / sh
    return (round(xs[0] * fx), round(ys[0] * fy), round((xs[-1] + 1) * fx), round((ys[-1] + 1) * fy))


MAX_TIGHTEN = 1.3          # 여백을 줄이려 확대하는 상한(원본 1024 기준 화질 유지)
SUBJECT_SHARE = {VERTICAL: 0.55, SQUARE: 0.6, HORIZONTAL: 0.6}   # 피사체가 차지해야 할 몫(세로형·정사각은 높이, 가로형은 폭)


def crop_window(img: Image.Image, w: int, h: int, focus: bool = True,
                tighten: bool = True) -> tuple[int, int, int, int, dict]:
    """cover 리사이즈 후 잘라 낼 창의 위치. focus 면 content_box 를 담도록 창을 옮긴다(담을 수 없으면 세로는 윗변,
    가로는 중심에 맞춘다 — 얼굴은 피사체의 위쪽에 있다). tighten 이면 피사체가 작아 빈 면이 넓을 때 창을 줄여(확대, 최대
    MAX_TIGHTEN 배) 피사체가 SUBJECT_SHARE 만큼 차지하게 하고, 문구 자리(세로형·정사각은 아래, 가로형은 왼쪽)는 남긴다.
    반환: (nw, nh, left, top, 기록용 dict). dict 의 zoom·cx·cy 는 editing.render 와 같은 뜻이다."""
    scale0 = max(w / img.width, h / img.height)
    nw0, nh0 = max(1, round(img.width * scale0)), max(1, round(img.height * scale0))
    left, top = (nw0 - w) // 2, (nh0 - h) // 2
    zoom = 1.0
    info: dict = {"mode": "중앙"}
    box = content_box(img) if focus else None
    if box:
        x0, y0, x1, y1 = (round(v * scale0) for v in box)
        bw, bh = max(1, x1 - x0), max(1, y1 - y0)
        tpl = template_for(w, h)
        if tighten:
            if tpl == HORIZONTAL:
                zoom = min(MAX_TIGHTEN, w / (bw / SUBJECT_SHARE[tpl]), h / bh if bh > 0 else MAX_TIGHTEN)
            else:
                zoom = min(MAX_TIGHTEN, h / (bh / SUBJECT_SHARE[tpl]), w / bw if bw > 0 else MAX_TIGHTEN)
            zoom = max(1.0, round(zoom, 3))
            if zoom < 1.03:
                zoom = 1.0
        ww, wh = w / zoom, h / zoom           # 확대 전 좌표계에서의 창 크기
        if tpl == HORIZONTAL and zoom > 1:    # 피사체를 오른쪽에, 왼쪽은 문구 자리
            left = x1 + 0.05 * ww - ww
            top = (y0 + y1) / 2 - wh / 2
        else:                                 # 가로 중심, 세로는 위에 약간의 머리 공간 (아래가 문구 자리)
            left = (x0 + x1) / 2 - ww / 2
            top = (y0 - 0.08 * wh) if zoom > 1 else ((y0 + y1) / 2 - wh / 2 if bh <= wh else y0)
        left, top = min(max(left, 0), nw0 - ww), min(max(top, 0), nh0 - wh)
        info = {"mode": "내용 상자" if zoom == 1 else "내용 상자+확대", "content_box": [x0, y0, x1, y1],
                "cut": {"top": top > y0, "bottom": y1 > top + wh, "left": left > x0, "right": x1 > left + ww}}
    scale = scale0 * zoom
    nw, nh = max(w, round(img.width * scale)), max(h, round(img.height * scale))
    left, top = min(max(round(left * zoom), 0), nw - w), min(max(round(top * zoom), 0), nh - h)
    info.update({"zoom": zoom, "cx": round((left + w / 2) / nw, 4), "cy": round((top + h / 2) / nh, 4)})
    if box:   # 캔버스 좌표계의 내용 상자 (문구가 피사체를 덮지 않게 compose 가 쓴다)
        bx0, by0, bx1, by1 = info["content_box"]
        info["content_box_canvas"] = [round(bx0 * zoom) - left, round(by0 * zoom) - top,
                                      round(bx1 * zoom) - left, round(by1 * zoom) - top]
    return nw, nh, left, top, info


def cover_crop(img: Image.Image, w: int, h: int, focus: bool = True) -> Image.Image:
    """늘이지 않고 비율을 맞춰 잘라 채운다. focus(기본) 면 내용 상자 기준, 아니면 중앙 기준."""
    img = img.convert("RGB")
    nw, nh, x, y, _ = crop_window(img, w, h, focus)
    img = img.resize((nw, nh), Image.LANCZOS)
    return img.crop((x, y, x + w, y + h))


def fit_blur(img: Image.Image, w: int, h: int) -> Image.Image:
    """잘라내지 않고 전체를 (w, h) 안에 맞춰 넣고, 남는 자리는 같은 이미지를 확대해 흐린 것으로 채운다.
    1장 방식에서 손실이 큰 비율군의 변형 생성이 실패했을 때의 폴백 — 피사체는 하나도 잘리지 않는다."""
    from PIL import ImageFilter
    img = img.convert("RGB")
    back = cover_crop(img, w, h, focus=False).filter(ImageFilter.GaussianBlur(radius=max(w, h) / 40))
    s = min(w / img.width, h / img.height)
    fw, fh = max(1, round(img.width * s)), max(1, round(img.height * s))
    fg = img.resize((fw, fh), Image.LANCZOS)
    back.paste(fg, ((w - fw) // 2, (h - fh) // 2))
    return back


# 제품 누끼를 붙일 구역 (x0, y0, x1, y1) — 캔버스 대비 비율. 세로형·정사각은 위쪽 구역에 놓인 모양(아래는 문구 자리),
# 가로형은 오른쪽 구역. 배경 프롬프트(image/product_slot.md)가 같은 숫자로 그 구역을 비워 두게 한다.
PRODUCT_SLOT = {VERTICAL: (0.14, 0.06, 0.86, 0.66), SQUARE: (0.16, 0.05, 0.84, 0.70), HORIZONTAL: (0.40, 0.08, 0.94, 0.88)}
PRODUCT_MAX_UPSCALE = 1.5    # 누끼가 구역보다 작을 때 늘리는 상한(그 이상은 흐려지므로 구역 안에서 작게 둔다)
# 제품 크기(캠페인 입력 product_scale): 구역의 가로·세로에 곱하는 배율. 세로형·정사각은 구역 아랫변 가운데(받침), 가로형은 구역 가운데를
# 기준으로 줄인다. 면적은 캔버스의 약 43% / 24% / 12% — 12% 아래로는 모바일 썸네일에서 제품을 알아보기 어렵다.
PRODUCT_SCALES = {"크게": 1.0, "보통": 0.75, "작게": 0.53}
DEFAULT_PRODUCT_SCALE = "크게"


def product_slot(template: str, size: str = DEFAULT_PRODUCT_SCALE) -> tuple[float, float, float, float]:
    """제품 크기(PRODUCT_SCALES)를 적용한 제품 구역 (x0, y0, x1, y1), 캔버스 대비 비율. 크게는 PRODUCT_SLOT 그대로."""
    x0, y0, x1, y1 = PRODUCT_SLOT[template]
    k = PRODUCT_SCALES.get(size, 1.0)
    w, h = (x1 - x0) * k, (y1 - y0) * k
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    top = cy - h / 2 if template == HORIZONTAL else y1 - h
    return cx - w / 2, top, cx + w / 2, top + h


def has_cutout(im: Image.Image) -> bool:
    """투명 배경이 실제로 있는 이미지인가(알파 채널이 있고 3% 이상이 투명). 불투명 사진은 오려 붙일 수 없다."""
    if im.mode not in ("RGBA", "LA") and "transparency" not in im.info:
        return False
    a = im.convert("RGBA").getchannel("A")
    hist = a.histogram()
    return sum(hist[:250]) / max(1, a.width * a.height) >= 0.03


def trim_cutout(product: Image.Image) -> Image.Image:
    """누끼의 투명 여백을 잘라낸 RGBA. 배치 크기는 이 잘라낸 크기(실제 제품 모양) 기준이다."""
    prod = product.convert("RGBA")
    bbox = prod.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return prod.crop(bbox) if bbox else prod


def product_box(W: int, H: int, product: Image.Image, template: str, size: str = DEFAULT_PRODUCT_SCALE) -> dict:
    """(W, H) 캔버스에서 제품 누끼가 실제로 놓일 상자. 제품 구역(product_slot: PRODUCT_SLOT 에 제품 크기 size 적용)에 비율을 지켜 맞추되 PRODUCT_MAX_UPSCALE 까지만
    키우고, 세로형·정사각은 구역 아랫변에 발을 붙이고 가로형은 구역 가운데에 둔다. paste_product 와 배경 프롬프트가 같은 값을 쓴다.
    반환: {slot, box, scale, src, canvas}. slot·box 는 픽셀 (x0, y0, x1, y1), src 는 여백을 잘라낸 누끼의 픽셀 크기 [w, h],
    canvas 는 계산에 쓴 캔버스 크기 [W, H]."""
    prod = trim_cutout(product)
    sx0, sy0, sx1, sy1 = (round(f * v) for f, v in zip(product_slot(template, size), (W, H, W, H)))
    s = min((sx1 - sx0) / prod.width, (sy1 - sy0) / prod.height, PRODUCT_MAX_UPSCALE)
    pw, ph = max(1, round(prod.width * s)), max(1, round(prod.height * s))
    x = sx0 + (sx1 - sx0 - pw) // 2
    y = sy1 - ph if template != HORIZONTAL else sy0 + (sy1 - sy0 - ph) // 2
    return {"slot": [sx0, sy0, sx1, sy1], "box": [x, y, x + pw, y + ph], "scale": round(s, 3), "src": [prod.width, prod.height],
            "canvas": [W, H]}


def paste_product(bg: Image.Image, product: Image.Image, template: str,
                  size: str = DEFAULT_PRODUCT_SCALE) -> tuple[Image.Image, dict]:
    """생성된 빈 배경 위에 제품 누끼 원본을 그대로(AI 재생성 없이) 붙인다. 위치·크기는 product_box,
    발밑에 접지 그림자를 깐다. 반환: (합성 이미지 RGB, 기록용 {slot, box, scale, src, canvas})."""
    bg = bg.convert("RGB")
    W, H = bg.size
    placed = product_box(W, H, product, template, size)
    x, y, x1, y1 = placed["box"]
    pw, ph = x1 - x, y1 - y
    prod = trim_cutout(product).resize((pw, ph), Image.LANCZOS)
    canvas = bg.convert("RGBA")
    # 접지 그림자: 발밑의 납작한 타원을 흐려 깐다(제품이 허공에 뜨지 않게)
    sh_w, sh_h = round(pw * 0.78), max(6, round(ph * 0.05))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    cx, base = x + pw // 2, y + ph - round(sh_h * 0.35)
    ImageDraw.Draw(shadow).ellipse([cx - sh_w // 2, base - sh_h // 2, cx + sh_w // 2, base + sh_h // 2], fill=(0, 0, 0, 140))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(max(3, sh_h * 0.45))))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste(prod, (x, y))
    canvas = Image.alpha_composite(canvas, layer)
    return canvas.convert("RGB"), placed


def load_logo(path: str, mime: str) -> Image.Image:
    if mime == "image/svg+xml":
        import resvg_py
        svg = storage.local(path).read_text(encoding="utf-8", errors="ignore")
        png = resvg_py.svg_to_bytes(svg_string=svg, width=1200)
        return Image.open(io.BytesIO(bytes(png))).convert("RGBA")
    return Image.open(storage.local(path)).convert("RGBA")


def _hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lum(rgb) -> float:
    def c(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * c(r) + 0.7152 * c(g) + 0.0722 * c(b)


def contrast(a, b) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        t = f"{cur} {word}".strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
            continue
        if cur:
            lines.append(cur)
            cur = ""
        for ch in word:  # 한 단어가 너무 길면 글자 단위로 줄바꿈
            t = cur + ch
            if draw.textlength(t, font=font) <= max_w:
                cur = t
            else:
                lines.append(cur)
                cur = ch
    if cur:
        lines.append(cur)
    return lines or [""]


def apply_scrim(canvas: Image.Image, tpl: str, color_hex: str, opacity: float, top: bool = False) -> Image.Image:
    """가독성을 위한 그라디언트 막. 가로형은 왼쪽이, 그 외는 아래쪽이 진하다(top=True 면 문구를 위에 둘 때 위쪽이 진하다)."""
    w, h = canvas.size
    grad = Image.new("L", (w, h), 0)
    ramp = Image.linear_gradient("L")            # 위(0) → 아래(255)
    if tpl == HORIZONTAL:
        gw = max(1, round(w * 0.75))
        grad.paste(ramp.transpose(Image.ROTATE_270).resize((gw, h), Image.BILINEAR), (0, 0))  # 왼쪽 255 → 오른쪽 0
    elif top:
        grad.paste(ramp.transpose(Image.FLIP_TOP_BOTTOM).resize((w, max(1, round(h * 0.6))), Image.BILINEAR), (0, 0))
    else:
        y0 = round(h * 0.4)
        grad.paste(ramp.resize((w, max(1, h - y0)), Image.BILINEAR), (0, y0))
    grad = grad.point(lambda v: int(v * max(0.0, min(1.0, opacity))))
    out = canvas.copy()
    out.paste(Image.new("RGB", (w, h), _hex(color_hex)), (0, 0), grad)
    return out


def _margins(w: int, h: int, safe: dict | None) -> tuple[int, int, int, int, str]:
    base = round(min(w, h) * 0.06)
    l = t = r = b = base
    src = "기본 여백(짧은 변의 6%)"
    if safe:
        def px(pct_key, px_key, total):
            if safe.get(px_key) is not None:
                return round(safe[px_key])
            if safe.get(pct_key) is not None:
                return round(total * safe[pct_key] / 100)
            return 0
        sl, st = px("left_pct", "left_px", w), px("top_pct", "top_px", h)
        sr, sb = px("right_pct", "right_px", w), px("bottom_pct", "bottom_px", h)
        if any((sl, st, sr, sb)):
            l, t, r, b = max(l, sl), max(t, st), max(r, sr), max(b, sb)
            src = "지면 안전영역 반영"
    return l, t, r, b, src


def _busyness(region: Image.Image) -> float:
    """영역이 얼마나 복잡한지(질감·대비) + 피부색 비율. 값이 클수록 로고를 올리기 나쁜 자리다 (사람·디테일 회피용 휴리스틱)."""
    small = region.convert("RGB").resize((48, 48), Image.BILINEAR)
    gray = small.convert("L")
    std = ImageStat.Stat(gray).stddev[0] / 64
    edge = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES).crop((1, 1, 47, 47))).mean[0] / 32   # 테두리 1px 은 필터 아티팩트라 제외
    px = list(small.getdata())
    # 피부색: 붉은 기가 돌고(R>G>B) R-G 는 15~90, G-B 는 70 미만. 노란 벽(G-B 가 큼)이나 주황 단색은 제외한다 (10/19)
    skin = sum(1 for r, g, b in px if r > 95 and g > 40 and b > 20 and r > g > b
               and 15 < r - g < 90 and g - b < 70 and max(r, g, b) - min(r, g, b) > 15) / len(px)
    return std + edge + skin * 1.5


def pick_logo_corner(canvas: Image.Image, boxes: dict[str, list[int]], pad: int) -> tuple[str, float, dict]:
    """후보 위치 중 가장 비어 있는 곳을 고른다. 기본(좌상단)이 충분히 괜찮으면 그대로 둔다."""
    W, H = canvas.size
    scores = {}
    for name, (x0, y0, x1, y1) in boxes.items():
        r = canvas.crop((max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)))
        scores[name] = _busyness(r)
    default = next(iter(boxes))
    best = min(scores, key=scores.get)
    if scores[default] <= scores[best] * 1.15 + 0.1:
        best = default
    return best, scores[best], scores


TEXT_BACKING_BUSYNESS = 0.6      # 문구 블록 뒤 보강판을 까는 복잡도 기준
TEXT_BACKING_OPACITY = 0.5


def min_local_contrast(region: Image.Image, color: tuple[int, int, int], cols: int = 6, rows: int = 3) -> float:
    """영역을 칸으로 나눠 글자색과 가장 대비가 약한 칸의 대비를 돌려준다. 평균색 대비는 밝은 조각을 숨기므로 칸 단위로 본다."""
    small = region.convert("RGB").resize((cols, rows), Image.BOX)
    return min(contrast(color, small.getpixel((x, y))) for x in range(cols) for y in range(rows))


def text_backing(canvas: Image.Image, box: list[int], color_hex: str, opacity: float) -> Image.Image:
    """문구 블록 뒤에 가장자리가 흐린 판을 깐다(스크림색). 사진의 밝은 조각 위에서도 글자가 읽히게 한다."""
    x0, y0, x1, y1 = box
    r = max(4, round((y1 - y0) * 0.12))
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((x0, y0, x1, y1), radius=r, fill=round(255 * opacity))
    mask = mask.filter(ImageFilter.GaussianBlur(r))
    out = canvas.copy()
    out.paste(Image.new("RGB", canvas.size, _hex(color_hex)), (0, 0), mask)
    return out


def draw_text_soft(canvas: Image.Image, items: list[tuple[int, int, str]], font, color: tuple[int, int, int]) -> Image.Image:
    """글자 뒤에 부드러운 그림자(밝은 글자면 어둡게, 어두운 글자면 밝게)를 한 번 깔고 글자를 그린다. 반환: 새 캔버스."""
    if not items:
        return canvas
    size = getattr(font, "size", 20)
    shadow = (0, 0, 0) if _lum(color) > 0.3 else (255, 255, 255)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    off = max(1, round(size * 0.03))
    for x, y, text in items:
        d.text((x + off, y + off), text, font=font, fill=shadow + (150,))
    layer = layer.filter(ImageFilter.GaussianBlur(max(1.0, size * 0.06)))
    out = Image.alpha_composite(canvas.convert("RGBA"), layer)
    d = ImageDraw.Draw(out)
    for x, y, text in items:
        d.text((x, y), text, font=font, fill=color)
    return out.convert("RGB")


def compose_strip(bg: Image.Image, w: int, h: int, headline: str, font_path: str, logo: Image.Image | None,
                  safe: dict | None, text_color_hex: str | None, scrim_hex: str | None, body: str | None, cta: str | None,
                  body_font_path: str, accent_hex: str | None, corner: str) -> tuple[Image.Image, dict]:
    """띠 배너(가로세로 2.6 이상, 예: 320×100). 왼쪽 30% 는 피사체 중심으로 자른 이미지 조각, 오른쪽은 같은 이미지를 흐리고
    어둡게 깐 바탕 위에 로고 · 제목(+본문 1줄) · CTA 버튼을 한 줄로 놓는다. 세로가 낮아 문구를 쌓지 않는다."""
    bg = bg.convert("RGB")
    L, T, R, B, safe_src = _margins(w, h, safe)
    canvas = fit_blur(bg, w, h)
    left_w = round(w * 0.3)
    canvas.paste(cover_crop(bg, left_w, h), (0, 0))
    scrim = _hex(scrim_hex or "#000000")
    mask = Image.new("L", (w - left_w, h), round(255 * 0.55))
    canvas.paste(Image.new("RGB", (w - left_w, h), scrim), (left_w, 0), mask)
    report: dict = {"template": HORIZONTAL, "layout_variant": "띠형", "canvas": [w, h], "content_box": [L, T, w - R, h - B],
                    "margin_source": safe_src, "warnings": [], "elements": [],
                    "crop": {"mode": "띠형", "zoom": 1.0, "cx": 0.5, "cy": 0.5, "content_box_canvas": [0, 0, left_w, h]},
                    "scrim": {"mode": "띠형", "color": (scrim_hex or "#000000").upper(), "opacity": 0.55}}
    gap = round(h * 0.08)
    x = left_w + gap
    cy = h / 2
    draw = ImageDraw.Draw(canvas)
    right = w - R
    # CTA: 오른쪽 끝. 폭은 캔버스의 30% 이내(글자 크기를 줄여 맞춘다)
    cta_box = None
    cta_h = max(18, min(64, round(h * 0.38)))        # 높이의 38%, 다만 큰 띠 배너에서도 64px 을 넘지 않게
    cta_size = max(10, round(cta_h * 0.5))
    if cta:
        while True:
            cf = ImageFont.truetype(font_path, cta_size)
            cw = round(draw.textlength(cta, font=cf) + cta_h * 0.8)
            if cw <= w * 0.3 or cta_size <= 10:
                break
            cta_size -= 1
        cta_box = [right - cw, round(cy - cta_h / 2), right, round(cy + cta_h / 2)]
        right = cta_box[0] - gap
    # 로고: 문구 왼쪽, 높이의 36% 이내·폭 12% 이내. 넣고도 문구 폭이 캔버스의 40% 이상 남을 때만 넣는다(띠 배너는 폭이 생명)
    if logo is not None:
        lh = round(h * 0.36)
        lw = max(1, round(logo.width * lh / logo.height))
        if lw > w * 0.12:
            lw = round(w * 0.12); lh = max(1, round(logo.height * lw / logo.width))
        if right - (x + lw + gap) >= w * 0.28:
            box = [x, round(cy - lh / 2), x + lw, round(cy + lh / 2)]
            lg = logo.resize((lw, lh), Image.LANCZOS)
            canvas.paste(lg, (box[0], box[1]), lg)
            report["elements"].append({"type": "logo", "box": box, "locked": True, "note": "원본 비율 유지"})
            x = box[2] + gap
        else:
            report["warnings"].append("띠 배너 폭이 좁아 로고를 뺐습니다 (편집에서 넣을 수 있습니다)")
    avail_w = max(20, right - x)
    # 제목: 한 줄 우선, 안 되면 두 줄. 글자 크기는 높이의 26% 부터 줄인다
    size = max(12, min(56, round(h * 0.26)))        # 제목 시작 크기: 높이의 26%, 최대 56px
    min_size = max(10, round(size * 0.5))
    body_size = 0
    body_lines: list[str] = []
    while True:
        font = ImageFont.truetype(font_path, size)
        lines = _wrap(draw, headline, font, avail_w)
        block_h = round(size * 1.28) * len(lines)
        if body:
            body_size = max(10, round(size * 0.6))
            bf = ImageFont.truetype(body_font_path, body_size)
            wrapped = _wrap(draw, body, bf, avail_w)
            # 본문은 제목이 한 줄로 들어가고 본문도 한 줄에 다 들어갈 때만
            if len(lines) == 1 and len(wrapped) == 1 and draw.textlength(wrapped[0], font=bf) <= avail_w:
                body_lines = wrapped
                block_h += round(h * 0.04) + round(body_size * 1.28)
            else:
                body_lines = []
        fits = len(lines) <= 2 and block_h <= h - T - B and all(draw.textlength(ln, font=font) <= avail_w for ln in lines)
        if fits or size <= min_size:
            break
        size -= 1
    if body and not body_lines:
        report["warnings"].append("띠 배너 폭이 좁아 본문을 이미지에서 뺐습니다 (제목·CTA 만 합성)")
        body = None
    if not fits:
        report["warnings"].append("문구가 영역을 넘칠 수 있습니다 (최소 글자 크기에서도 맞지 않음)")
    text_h = round(size * 1.28) * len(lines)
    body_h = (round(h * 0.04) + round(body_size * 1.28)) if body else 0
    ty = round(cy - (text_h + body_h) / 2)
    region = canvas.crop((x, max(0, ty), max(x + 1, right), min(h, ty + text_h + body_h))).resize((1, 1), Image.BOX).getpixel((0, 0))
    color, fallback = pick_text_color(region, text_color_hex)
    if fallback:
        report["warnings"].append("브랜드 텍스트색의 대비가 부족해 흰색/검정으로 대체했습니다")
    for i, ln in enumerate(lines):
        draw.text((x, ty + i * round(size * 1.28)), ln, font=font, fill=color)
    text_box = [x, ty, right, ty + text_h]
    report["elements"].append({"type": "text", "role": "제목", "text": headline, "lines": lines, "box": text_box,
                               "font_size": size, "color": "#%02X%02X%02X" % color, "color_fallback": fallback, "fits": fits})
    if body:
        by = ty + text_h + round(h * 0.04)
        bf = ImageFont.truetype(body_font_path, body_size)
        draw.text((x, by), body_lines[0], font=bf, fill=color)
        report["elements"].append({"type": "body", "role": "본문", "text": body, "lines": body_lines,
                                   "box": [x, by, right, by + round(body_size * 1.28)], "font_size": body_size,
                                   "color": "#%02X%02X%02X" % color, "cut": True})
    if cta and cta_box:
        cta_bg, cta_fg = cta_colors(accent_hex, color)
        draw_cta(canvas, cta_box, cta, font_path, cta_size, cta_bg, cta_fg, corner)
        report["elements"].append({"type": "cta", "role": "CTA", "text": cta, "box": cta_box, "font_size": cta_size,
                                   "bg_color": "#%02X%02X%02X" % cta_bg, "text_color": "#%02X%02X%02X" % cta_fg, "corner": corner})
    report["in_image"] = ["headline"] + (["body"] if body else []) + (["cta"] if cta else [])
    return canvas, report


def compose(bg: Image.Image, w: int, h: int, headline: str, font_path: str, logo: Image.Image | None,
            safe: dict | None, logo_margin_pct: float | None, text_color_hex: str | None,
            scrim_hex: str | None, fill_blur: bool = False, body: str | None = None, cta: str | None = None,
            body_font_path: str | None = None, accent_hex: str | None = None, corner: str = "둥근",
            logo_position: str | None = None) -> tuple[Image.Image, dict]:
    """배경 위에 로고·제목(+선택: 본문·CTA 버튼)을 합성한다. 본문·CTA 는 지면 설정(in_image)이 켜진 경우에만 받는다.
    logo_position: 브랜드 설정의 로고 자리(좌상단·우상단·좌하단·우하단). None/'자동'이면 배경이 가장 단순한 모서리를 고른다.
    문구 블록은 제목 → 본문 → CTA 순으로 아래에서 위로 쌓고, 자리가 모자라면 본문을 먼저 뺀다(제목·CTA 우선).
    띠 배너(가로세로 2.6 이상)는 CTA 를 오른쪽에 두고 본문은 1줄만."""
    bg = bg.convert("RGB")
    body = (body or "").strip() or None
    cta = (cta or "").strip() or None
    body_font_path = body_font_path or font_path
    if is_strip(w, h):
        return compose_strip(bg, w, h, headline, font_path, logo, safe, text_color_hex, scrim_hex, body, cta,
                             body_font_path, accent_hex, corner)
    strip = False
    if fill_blur:        # 블러 채우기: 자르지 않고 전체를 넣는다(1장 방식 폴백). 내용 상자는 전체로 본다
        canvas = fit_blur(bg, w, h)
        s = min(w / bg.width, h / bg.height)
        fw, fh = round(bg.width * s), round(bg.height * s)
        ox, oy = (w - fw) // 2, (h - fh) // 2
        crop = {"mode": "블러 채우기", "zoom": 1.0, "cx": 0.5, "cy": 0.5, "content_box_canvas": [ox, oy, ox + fw, oy + fh]}
    else:
        nw, nh, x, y, crop = crop_window(bg, w, h)
        canvas = bg.resize((nw, nh), Image.LANCZOS).crop((x, y, x + w, y + h))
    tpl = template_for(w, h)
    L, T, R, B, safe_src = _margins(w, h, safe)
    cl, ct, cr, cb = L, T, w - R, h - B          # 콘텐츠 영역
    report: dict = {"template": tpl, "canvas": [w, h], "content_box": [cl, ct, cr, cb],
                    "margin_source": safe_src, "crop": crop, "warnings": [], "elements": []}
    if crop.get("cut", {}).get("top") or crop.get("cut", {}).get("bottom"):
        report["warnings"].append("배경의 피사체가 이 규격 비율에 다 담기지 않아 위/아래가 일부 잘렸습니다")

    # 1) 로고 (원본 비율 유지, 확대·변형 없음)
    logo_box = None
    if logo is not None:
        max_h = round(h * (0.14 if tpl != HORIZONTAL else 0.18))
        max_w = round((cr - cl) * 0.4)
        s = min(max_h / logo.height, max_w / logo.width)
        lw, lh = max(1, round(logo.width * s)), max(1, round(logo.height * s))
        logo_box = [cl, ct, cl + lw, ct + lh]
    pad = round((logo_box[3] - logo_box[1]) * (logo_margin_pct or 0) / 100) if logo_box else 0

    # 1-1) 로고 위치 자동 선택: 후보 중 이미지가 가장 단순한 곳(사람·디테일 회피). 후보는 문구 영역과 겹치지 않는 곳만 쓴다.
    #      세로형·정사각은 피사체가 위쪽에 오는 구도라 아래 모서리도 후보에 넣는다(선택되면 문구를 로고 위에 쌓는다).
    if logo_box:
        # 로고 모서리는 logo_corner 에 둔다. 인자 corner(CTA 버튼 모양 둥근/각진)와 섞이면 CTA 에 로고 위치가 기록된다
        lw, lh = logo_box[2] - logo_box[0], logo_box[3] - logo_box[1]
        all_cands = {"좌상단": [cl, ct, cl + lw, ct + lh], "우상단": [cr - lw, ct, cr, ct + lh],
                     "좌하단": [cl, cb - lh, cl + lw, cb], "우하단": [cr - lw, cb - lh, cr, cb]}
        fixed = logo_position if logo_position in all_cands else None
        if fixed:
            # 브랜드 설정으로 고정: 캠페인의 모든 지면이 같은 모서리를 쓴다(지면마다 제각각이면 브랜드 인지가 흐려진다)
            logo_corner = fixed
            _, score, scores = pick_logo_corner(canvas, {logo_corner: all_cands[logo_corner]}, pad)
            report["logo_placement"] = {"corner": logo_corner, "busyness": round(score, 2), "mode": "브랜드 고정"}
        else:
            cands = {k: all_cands[k] for k in ("좌상단", "우상단", "우하단")} if tpl == HORIZONTAL else dict(all_cands)
            # 가로형은 문구가 왼쪽 55%에 있어 좌하단은 후보에서 뺀다
            logo_corner, score, scores = pick_logo_corner(canvas, cands, pad)
            # 세로형·정사각에서 피사체가 아래 자리를 비워 두면(위 모서리가 피사체와 안 겹치고 단순하면) 위 모서리를 우선한다 —
            # 아래 띠는 문구 자리라 로고까지 두면 문구가 피사체 위로 밀려 올라간다
            subj_box = crop.get("content_box_canvas")
            if tpl != HORIZONTAL and subj_box and logo_corner in ("좌하단", "우하단"):
                sx0, sy0, sx1, sy1 = subj_box
                tops = {k: v for k, v in cands.items() if k in ("좌상단", "우상단")
                        and (v[3] + pad < sy0 or v[2] + pad < sx0 or v[0] - pad > sx1) and scores[k] < 0.35}
                if tops:
                    logo_corner = min(tops, key=scores.get)
                    score = scores[logo_corner]
            report["logo_placement"] = {"corner": logo_corner, "busyness": round(score, 2), "mode": "자동"}
        logo_box = all_cands[logo_corner]
    left_logo = bool(logo_box) and logo_box[0] <= cl + 1
    # 로고가 아래 모서리면 문구는 그 위에 쌓는다. 가로형은 문구가 왼쪽 열이라 왼쪽 아래 로고만 해당한다
    bottom_logo = bool(logo_box) and logo_box[1] > ct + 1 and (tpl != HORIZONTAL or left_logo)

    # 2) 문구 영역
    if tpl == HORIZONTAL:
        tx0, tx1 = cl, cl + round((cr - cl) * 0.55)
    else:
        tx0, tx1 = cl, cr
    gap = pad + round(h * 0.02)
    if bottom_logo:                         # 세로형·정사각에서 로고가 아래 모서리면 문구는 그 위에 쌓는다
        ty0, ty1 = ct, logo_box[1] - gap
    elif logo_box and (left_logo or tpl != HORIZONTAL):   # 문구는 로고 아래에서 시작한다
        ty0, ty1 = logo_box[3] + gap, cb
    else:                                   # 로고가 오른쪽이고 문구가 왼쪽 55%인 가로형은 겹치지 않아 위까지 쓴다
        ty0, ty1 = ct, cb
    # 문구는 피사체(내용 상자)를 덮지 않는다: 세로형·정사각은 피사체 아래에서 시작, 가로형은 피사체 왼쪽까지만.
    # 남는 자리가 너무 작으면(세로 25%·가로 30% 미만) 무리하지 않고 크기 맞춤·경고에 맡긴다.
    min_size = max(16, round(min(w, h) * 0.035))
    ty0_top = ty0            # 피사체 조정 전의 위쪽 끝(문구를 피사체 위에 둘 때 쓴다)
    subj = crop.get("content_box_canvas")
    if subj:
        sx0, sy0, sx1, sy1 = subj
        if tpl != HORIZONTAL and sy1 + gap > ty0 and sy0 < ty1:
            if ty1 - (sy1 + gap) >= round(min_size * 1.28):      # 최소 글자 크기 한 줄이라도 들어가면 피사체 아래로
                ty0 = sy1 + gap
            else:
                report["warnings"].append("문구 자리가 좁아 피사체와 겹칠 수 있습니다 (편집에서 위치를 조정하세요)")
        elif tpl == HORIZONTAL and sx0 - gap < tx1:
            if sx0 - gap - tx0 >= 0.3 * (cr - cl):
                tx1 = sx0 - gap
            else:
                report["warnings"].append("문구 자리가 좁아 피사체와 겹칠 수 있습니다 (편집에서 위치를 조정하세요)")
    # 띠 배너: CTA 는 문구 열 오른쪽에 세로 가운데, 문구 열은 그만큼 좁아진다
    cta_h = 0
    if cta:
        cta_h = round(h * (0.26 if strip else 0.11 if tpl == HORIZONTAL else 0.065))
        cta_h = max(cta_h, 18)
    cta_box: list[int] | None = None
    cta_font_size = max(10, round(cta_h * 0.5)) if cta else 0
    if cta and strip:
        probe = ImageDraw.Draw(canvas)
        cf = ImageFont.truetype(font_path, cta_font_size)
        cw = round(probe.textlength(cta, font=cf) + cta_h)
        cta_box = [tx1 - cw, round((ct + cb) / 2 - cta_h / 2), tx1, round((ct + cb) / 2 + cta_h / 2)]
        tx1 = cta_box[0] - gap
    avail_w, avail_h = tx1 - tx0, max(ty1 - ty0, 1)

    # 3) 스크림(가독성 보조)은 문구 자리(아래/위)가 정해진 뒤에 4-1)에서 깐다.

    # 4) 문구 블록 크기 맞춤: 제목(+본문 +CTA)을 한 덩어리로 보고 제목 크기를 줄여 가며 맞춘다. 본문은 말줄임표("…")로 줄이지 않는다:
    #    줄 수 상한 안에 들어가도록 글자 크기를 줄이고, 최소 크기에서도 안 들어가면 상한을 넘겨 모든 줄을 그대로 둔다.
    #    블록이 최소 글자 크기에서도 안 들어가면 본문을 먼저 뺀다(제목·CTA 우선). 그래도 넘치면 경고만 남긴다.
    draw = ImageDraw.Draw(canvas)
    body_max_lines = 1 if strip else BODY_MAX_LINES.get(tpl, 2)
    body_min_size = 11
    inner_gap = round(h * 0.012) if not strip else round(h * 0.04)
    size0 = max(24, round(min(w, h) * 0.09))     # 여백이 넓어 보이지 않게 제목을 크게 시작한다(맞지 않으면 줄인다)
    def body_fit(bsize: int) -> tuple[list[str], int]:
        """본문을 bsize 로 줄바꿈한 줄들(자르지 않음)과 실제로 쓸 글자 크기. 줄 수 상한 안에 들어갈 때까지 크기를 줄이고,
        최소 크기에서도 넘치면 최소 크기의 모든 줄을 돌려준다."""
        for bs in range(bsize, body_min_size - 1, -1):
            bl = _wrap(draw, body, ImageFont.truetype(body_font_path, bs), avail_w)
            if len(bl) <= body_max_lines:
                return bl, bs
        return bl, body_min_size
    def fit_block(ah: int):
        """주어진 높이에 제목(+본문 +CTA)을 맞춘다. 반환: 제목 크기, 제목 줄, 본문 줄, 본문 크기, 맞음, 본문 유지, 블록 높이"""
        sz = size0
        while True:
            f = ImageFont.truetype(font_path, sz)
            ls = _wrap(draw, headline, f, avail_w)
            bh = round(sz * 1.28) * len(ls)
            bl: list[str] = []
            bs = 0
            if body:
                bl, bs = body_fit(max(body_min_size, round(sz * 0.55)))
                bh += inner_gap + round(bs * 1.28) * len(bl)
            if cta and not strip:
                bh += inner_gap + cta_h
            ok = bh <= ah and all(draw.textlength(ln, font=f) <= avail_w for ln in ls)
            if ok or sz <= min_size:
                break
            sz -= 2
        kept = True
        if not ok and body:      # 자리가 모자라면 본문을 빼고 제목·CTA 만
            kept, bl, bs = False, [], 0
            bh = round(sz * 1.28) * len(ls) + ((inner_gap + cta_h) if cta and not strip else 0)
            ok = bh <= ah and all(draw.textlength(ln, font=f) <= avail_w for ln in ls)
        return sz, ls, bl, bs, ok, kept, bh
    size, lines, body_lines, body_size, fits, kept, block_h = fit_block(avail_h)
    # 4-1) 피사체 아래 자리가 모자라면(본문이 빠지거나 넘칠 때) 피사체 위쪽 자리를 써 본다. 더 잘 들어갈 때만 옮긴다.
    top_zone = False
    if (not fits or not kept) and tpl != HORIZONTAL and not strip and subj:
        above_h = (subj[1] - gap) - ty0_top
        if above_h > avail_h:
            alt = fit_block(above_h)
            if (alt[4], alt[5]) > (fits, kept):
                size, lines, body_lines, body_size, fits, kept, block_h = alt
                avail_h, ty0 = above_h, ty0_top
                ty1 = ty0 + block_h          # 위에서부터 쌓는다
                top_zone = True
                report["text_zone"] = "위"
    body_dropped = bool(body) and not kept
    if body_dropped:
        body, body_lines = None, []
        report["warnings"].append("문구 자리가 좁아 본문을 이미지에서 뺐습니다 (제목·CTA 만 합성)")
    font = ImageFont.truetype(font_path, size)
    if not fits:
        report["warnings"].append("문구가 영역을 넘칠 수 있습니다 (최소 글자 크기에서도 맞지 않음)")
    scrim_opacity = 0.62
    canvas = apply_scrim(canvas, tpl, scrim_hex or "#000000", scrim_opacity, top=top_zone)
    report["scrim"] = {"mode": tpl, "color": (scrim_hex or "#000000").upper(), "opacity": scrim_opacity,
                       **({"position": "위"} if top_zone else {})}
    draw = ImageDraw.Draw(canvas)
    if body and len(body_lines) > body_max_lines:
        report["warnings"].append(f"본문이 {body_max_lines}줄을 넘습니다 (말줄임 없이 전부 표시). 편집에서 줄이거나 본문을 끄세요")
    text_h = round(size * 1.28) * len(lines)
    body_h = (round(body_size * 1.28) * len(body_lines)) if body else 0
    # 아래에서 위로: CTA(띠형 제외) → 본문 → 제목
    y_cursor = ty1
    if cta and not strip:
        probe_font = ImageFont.truetype(font_path, cta_font_size)
        cw = round(draw.textlength(cta, font=probe_font) + cta_h)
        cw = min(cw, round(avail_w * 0.7)) if cw > avail_w * 0.7 else cw
        cta_box = [tx0, y_cursor - cta_h, tx0 + cw, y_cursor]
        y_cursor = cta_box[1] - inner_gap
    body_box: list[int] | None = None
    if body:
        body_box = [tx0, y_cursor - body_h, tx1, y_cursor]
        y_cursor = body_box[1] - inner_gap
    ty = y_cursor - text_h
    text_box = [tx0, ty, tx1, y_cursor]
    if strip:   # 띠형: 문구 블록을 세로 가운데로
        shift = round((ct + cb) / 2 - (ty + y_cursor) / 2)
        shift = max(ct - ty, min(shift, cb - y_cursor))
        ty += shift; text_box = [tx0, ty, tx1, y_cursor + shift]
        if body_box:
            body_box = [body_box[0], body_box[1] + shift, body_box[2], body_box[3] + shift]

    # 5) 문구 색: 브랜드 텍스트색을 쓰되 대비가 부족하면 흰색/검정으로 대체
    region = canvas.crop((tx0, max(0, ty), tx1, max(ty + 1, ty1))).resize((1, 1), Image.BOX).getpixel((0, 0))
    color, fallback = pick_text_color(region, text_color_hex)
    # 5-1) 가독성 보강: 스크림은 평균을 맞출 뿐이라 사진의 밝은 조각(피부·하늘) 위에서는 글자가 묻힌다.
    #      문구 블록을 칸으로 나눠 가장 약한 칸의 대비가 4.5:1 에 못 미치거나 배경이 복잡하면, 블록 뒤에 흐린 판을 한 장 더 깐다.
    block_bottom = y_cursor if not cta_box or strip else cta_box[3]
    block = [tx0, ty, max(tx1, cta_box[2] if cta_box and not strip else tx1), block_bottom]
    tb_pad = round(size * 0.5)
    tb_box = [max(0, block[0] - tb_pad), max(0, block[1] - tb_pad), min(w, block[2] + tb_pad), min(h, block[3] + tb_pad)]
    tb_region = canvas.crop(tuple(tb_box))
    weakest = min_local_contrast(tb_region, color)
    if weakest < 4.5 or _busyness(tb_region) > TEXT_BACKING_BUSYNESS:
        canvas = text_backing(canvas, tb_box, scrim_hex or "#000000", TEXT_BACKING_OPACITY)
        draw = ImageDraw.Draw(canvas)
        region = canvas.crop((tx0, max(0, ty), tx1, max(ty + 1, ty1))).resize((1, 1), Image.BOX).getpixel((0, 0))
        color, fallback = pick_text_color(region, text_color_hex)
        report["text_backing"] = {"box": tb_box, "color": (scrim_hex or "#000000").upper(), "opacity": TEXT_BACKING_OPACITY,
                                  "weakest_contrast": round(weakest, 2)}
    if fallback:
        report["warnings"].append("브랜드 텍스트색의 대비가 부족해 흰색/검정으로 대체했습니다")
    canvas = draw_text_soft(canvas, [(tx0, ty + i * round(size * 1.28), ln) for i, ln in enumerate(lines)], font, color)
    if body and body_box:
        bf = ImageFont.truetype(body_font_path, body_size)
        canvas = draw_text_soft(canvas, [(tx0, body_box[1] + i * round(body_size * 1.28), ln) for i, ln in enumerate(body_lines)], bf, color)
        report["elements"].append({"type": "body", "role": "본문", "text": body, "lines": body_lines, "box": body_box,
                                   "font_size": body_size, "color": "#%02X%02X%02X" % color, "cut": False})
    draw = ImageDraw.Draw(canvas)
    if cta and cta_box:
        cta_bg, cta_fg = cta_colors(accent_hex, color)
        draw_cta(canvas, cta_box, cta, font_path, cta_font_size, cta_bg, cta_fg, corner)
        if len(cta) > CTA_MAX_CHARS:
            report["warnings"].append(f"CTA 가 {CTA_MAX_CHARS}자를 넘어 버튼이 깁니다 (짧은 문구를 권장)")
        report["elements"].append({"type": "cta", "role": "CTA", "text": cta, "box": cta_box, "font_size": cta_font_size,
                                   "bg_color": "#%02X%02X%02X" % cta_bg, "text_color": "#%02X%02X%02X" % cta_fg,
                                   "corner": corner})
    report["in_image"] = ["headline"] + (["body"] if body else []) + (["cta"] if cta else [])
    if body_dropped:
        report["in_image_dropped"] = ["body"]

    # 6) 로고 합성
    if logo is not None and logo_box:
        lg = logo.resize((logo_box[2] - logo_box[0], logo_box[3] - logo_box[1]), Image.LANCZOS)
        canvas.paste(lg, (logo_box[0], logo_box[1]), lg)      # 원본 그대로(크기만 비율 유지로 맞춤). 판·테두리·색 변경 없음
        report["elements"].append({"type": "logo", "box": logo_box, "locked": True,
                                   "note": "원본 비율 유지"})
    report["elements"].append({"type": "text", "role": "제목", "text": headline, "lines": lines,
                               "box": text_box, "font_size": size, "color": "#%02X%02X%02X" % color,
                               "color_fallback": fallback, "fits": fits})
    return canvas, report
