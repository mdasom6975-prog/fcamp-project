"""배경 이미지 위에 로고와 문구를 합성한다 (이미지 생성과 분리).

- 로고는 원본 비율을 그대로 쓰고 변형하지 않는다.
- 문구는 모델이 그리지 않고 여기서 폰트로 직접 그린다 (한글 오탈자 방지).
- 지면 비율에 따라 가로형 / 정사각 / 세로형 템플릿을 쓴다.
"""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HORIZONTAL, SQUARE, VERTICAL = "가로형", "정사각", "세로형"


def template_for(w: int, h: int) -> str:
    r = w / h
    return HORIZONTAL if r >= 1.25 else VERTICAL if r <= 0.8 else SQUARE


def cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """늘이지 않고 비율을 맞춰 중앙 기준으로 잘라 채운다."""
    img = img.convert("RGB")
    scale = max(w / img.width, h / img.height)
    nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    x, y = (nw - w) // 2, (nh - h) // 2
    return img.crop((x, y, x + w, y + h))


def load_logo(path: str, mime: str) -> Image.Image:
    if mime == "image/svg+xml":
        import resvg_py
        svg = Path(path).read_text(encoding="utf-8", errors="ignore")
        png = resvg_py.svg_to_bytes(svg_string=svg, width=1200)
        return Image.open(io.BytesIO(bytes(png))).convert("RGBA")
    return Image.open(path).convert("RGBA")


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


def apply_scrim(canvas: Image.Image, tpl: str, color_hex: str, opacity: float) -> Image.Image:
    """가독성을 위한 그라디언트 막. 가로형은 왼쪽이, 그 외는 아래쪽이 진하다."""
    w, h = canvas.size
    grad = Image.new("L", (w, h), 0)
    ramp = Image.linear_gradient("L")            # 위(0) → 아래(255)
    if tpl == HORIZONTAL:
        gw = max(1, round(w * 0.75))
        grad.paste(ramp.transpose(Image.ROTATE_270).resize((gw, h), Image.BILINEAR), (0, 0))  # 왼쪽 255 → 오른쪽 0
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


def compose(bg: Image.Image, w: int, h: int, headline: str, font_path: str, logo: Image.Image | None,
            safe: dict | None, logo_margin_pct: float | None, text_color_hex: str | None,
            scrim_hex: str | None) -> tuple[Image.Image, dict]:
    canvas = cover_crop(bg, w, h)
    tpl = template_for(w, h)
    L, T, R, B, safe_src = _margins(w, h, safe)
    cl, ct, cr, cb = L, T, w - R, h - B          # 콘텐츠 영역
    report: dict = {"template": tpl, "canvas": [w, h], "content_box": [cl, ct, cr, cb],
                    "margin_source": safe_src, "warnings": [], "elements": []}

    # 1) 로고 (원본 비율 유지, 확대·변형 없음)
    logo_box = None
    if logo is not None:
        max_h = round(h * (0.14 if tpl != HORIZONTAL else 0.18))
        max_w = round((cr - cl) * 0.4)
        s = min(max_h / logo.height, max_w / logo.width)
        lw, lh = max(1, round(logo.width * s)), max(1, round(logo.height * s))
        logo_box = [cl, ct, cl + lw, ct + lh]
    pad = round((logo_box[3] - logo_box[1]) * (logo_margin_pct or 0) / 100) if logo_box else 0

    # 2) 문구 영역
    if tpl == HORIZONTAL:
        tx0, tx1 = cl, cl + round((cr - cl) * 0.55)
    else:
        tx0, tx1 = cl, cr
    ty0 = (logo_box[3] + pad + round(h * 0.02)) if logo_box else ct
    ty1 = cb
    avail_w, avail_h = tx1 - tx0, max(ty1 - ty0, 1)

    # 3) 스크림(가독성 보조): 문구 쪽에서 투명해지는 그라디언트
    scrim_opacity = 0.62
    canvas = apply_scrim(canvas, tpl, scrim_hex or "#000000", scrim_opacity)
    report["scrim"] = {"mode": tpl, "color": (scrim_hex or "#000000").upper(), "opacity": scrim_opacity}

    # 4) 제목 크기 맞춤 (잘라내지 않고 크기를 줄이며, 그래도 넘치면 경고만 남긴다)
    draw = ImageDraw.Draw(canvas)
    size = max(24, round(min(w, h) * 0.075))
    min_size = max(16, round(min(w, h) * 0.035))
    lines: list[str] = []
    font = ImageFont.truetype(font_path, size)
    while True:
        font = ImageFont.truetype(font_path, size)
        lines = _wrap(draw, headline, font, avail_w)
        lh_px = round(size * 1.28)
        block_h = lh_px * len(lines)
        fits = block_h <= avail_h and all(draw.textlength(ln, font=font) <= avail_w for ln in lines)
        if fits or size <= min_size:
            break
        size -= 2
    if not fits:
        report["warnings"].append("문구가 영역을 넘칠 수 있습니다 (최소 글자 크기에서도 맞지 않음)")
    text_h = round(size * 1.28) * len(lines)
    ty = ty1 - text_h                      # 영역 하단에 정렬
    text_box = [tx0, ty, tx1, ty1]

    # 5) 문구 색: 브랜드 텍스트색을 쓰되 대비가 부족하면 흰색/검정으로 대체
    region = canvas.crop((tx0, max(0, ty), tx1, max(ty + 1, ty1))).resize((1, 1), Image.BOX).getpixel((0, 0))
    color, fallback = None, False
    if text_color_hex:
        cand = _hex(text_color_hex)
        if contrast(cand, region) >= 4.5:
            color = cand
    if color is None:
        fallback = bool(text_color_hex)
        color = (255, 255, 255) if contrast((255, 255, 255), region) >= contrast((0, 0, 0), region) else (0, 0, 0)
        if fallback:
            report["warnings"].append("브랜드 텍스트색의 대비가 부족해 흰색/검정으로 대체했습니다")
    for i, ln in enumerate(lines):
        draw.text((tx0, ty + i * round(size * 1.28)), ln, font=font, fill=color)

    # 6) 로고 합성
    if logo is not None and logo_box:
        lg = logo.resize((logo_box[2] - logo_box[0], logo_box[3] - logo_box[1]), Image.LANCZOS)
        canvas.paste(lg, (logo_box[0], logo_box[1]), lg)
        report["elements"].append({"type": "logo", "box": logo_box, "locked": True,
                                   "note": "원본 비율 유지"})
    report["elements"].append({"type": "text", "role": "제목", "text": headline, "lines": lines,
                               "box": text_box, "font_size": size, "color": "#%02X%02X%02X" % color,
                               "color_fallback": fallback, "fits": fits})
    return canvas, report
