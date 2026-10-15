"""배경 이미지 위에 로고와 문구를 합성한다 (이미지 생성과 분리).

- 로고는 원본 비율을 그대로 쓰고 변형하지 않는다.
- 문구는 모델이 그리지 않고 여기서 폰트로 직접 그린다 (한글 오탈자 방지).
- 지면 비율에 따라 가로형 / 정사각 / 세로형 템플릿을 쓴다.
"""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

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


def _busyness(region: Image.Image) -> float:
    """영역이 얼마나 복잡한지(질감·대비) + 피부색 비율. 값이 클수록 로고를 올리기 나쁜 자리다 (사람·디테일 회피용 휴리스틱)."""
    small = region.convert("RGB").resize((48, 48), Image.BILINEAR)
    gray = small.convert("L")
    std = ImageStat.Stat(gray).stddev[0] / 64
    edge = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES).crop((1, 1, 47, 47))).mean[0] / 32   # 테두리 1px 은 필터 아티팩트라 제외
    px = list(small.getdata())
    skin = sum(1 for r, g, b in px if r > 95 and g > 40 and b > 20 and r > g and r > b
               and abs(r - g) > 15 and max(r, g, b) - min(r, g, b) > 15) / len(px)
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


def _logo_plate(canvas: Image.Image, logo: Image.Image, box: list[int]) -> Image.Image:
    """복잡한 배경 위에서 로고가 묻히지 않게 반투명 판을 깐다. 로고가 밝으면 어두운 판, 어두우면 밝은 판."""
    a = logo.getchannel("A")
    lum = logo.convert("L")
    px = [v for v, al in zip(lum.getdata(), a.getdata()) if al > 128]
    light_logo = bool(px) and sum(px) / len(px) > 140
    x0, y0, x1, y1 = box
    p = round((y1 - y0) * 0.25)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle((x0 - p, y0 - p, x1 + p, y1 + p), radius=p,
                                              fill=(0, 0, 0, 150) if light_logo else (255, 255, 255, 170))
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


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

    # 1-1) 로고 위치 자동 선택: 후보 중 이미지가 가장 단순한 곳(사람·디테일 회피). 후보는 문구 영역과 겹치지 않는 곳만 쓴다.
    logo_plate = False
    if logo_box:
        lw, lh = logo_box[2] - logo_box[0], logo_box[3] - logo_box[1]
        cands = {"좌상단": [cl, ct, cl + lw, ct + lh], "우상단": [cr - lw, ct, cr, ct + lh]}
        if tpl == HORIZONTAL:   # 가로형은 문구가 왼쪽 55%에 있어 오른쪽 아래도 비어 있다
            cands["우하단"] = [cr - lw, cb - lh, cr, cb]
        corner, score, _ = pick_logo_corner(canvas, cands, pad)
        logo_box = cands[corner]
        report["logo_placement"] = {"corner": corner, "busyness": round(score, 2)}
        if score > 0.8:
            logo_plate = True
            report["warnings"].append("로고 주변 이미지가 복잡해 반투명 판을 깔았습니다")
    left_logo = bool(logo_box) and logo_box[0] <= cl + 1

    # 2) 문구 영역
    if tpl == HORIZONTAL:
        tx0, tx1 = cl, cl + round((cr - cl) * 0.55)
    else:
        tx0, tx1 = cl, cr
    # 문구는 로고 아래에서 시작한다. 로고가 오른쪽이고 문구가 왼쪽 55%인 가로형은 로고와 겹치지 않아 위까지 쓴다.
    ty0 = (logo_box[3] + pad + round(h * 0.02)) if (logo_box and (left_logo or tpl != HORIZONTAL)) else ct
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
        if logo_plate:
            canvas = _logo_plate(canvas, lg, logo_box)
        canvas.paste(lg, (logo_box[0], logo_box[1]), lg)
        report["elements"].append({"type": "logo", "box": logo_box, "locked": True,
                                   "note": "원본 비율 유지"})
    report["elements"].append({"type": "text", "role": "제목", "text": headline, "lines": lines,
                               "box": text_box, "font_size": size, "color": "#%02X%02X%02X" % color,
                               "color_fallback": fallback, "fits": fits})
    return canvas, report
