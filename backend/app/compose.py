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
    bg = bg.convert("RGB")
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
    logo_plate = False
    if logo_box:
        lw, lh = logo_box[2] - logo_box[0], logo_box[3] - logo_box[1]
        cands = {"좌상단": [cl, ct, cl + lw, ct + lh], "우상단": [cr - lw, ct, cr, ct + lh]}
        if tpl == HORIZONTAL:   # 가로형은 문구가 왼쪽 55%에 있어 오른쪽 아래도 비어 있다
            cands["우하단"] = [cr - lw, cb - lh, cr, cb]
        else:
            cands["좌하단"] = [cl, cb - lh, cl + lw, cb]
            cands["우하단"] = [cr - lw, cb - lh, cr, cb]
        corner, score, scores = pick_logo_corner(canvas, cands, pad)
        # 세로형·정사각에서 피사체가 아래 자리를 비워 두면(위 모서리가 피사체와 안 겹치고 단순하면) 위 모서리를 우선한다 —
        # 아래 띠는 문구 자리라 로고까지 두면 문구가 피사체 위로 밀려 올라간다
        subj_box = crop.get("content_box_canvas")
        if tpl != HORIZONTAL and subj_box and corner in ("좌하단", "우하단"):
            sx0, sy0, sx1, sy1 = subj_box
            tops = {k: v for k, v in cands.items() if k in ("좌상단", "우상단")
                    and (v[3] + pad < sy0 or v[2] + pad < sx0 or v[0] - pad > sx1) and scores[k] < 0.35}
            if tops:
                corner = min(tops, key=scores.get)
                score = scores[corner]
        logo_box = cands[corner]
        report["logo_placement"] = {"corner": corner, "busyness": round(score, 2)}
        if score > 0.8:
            logo_plate = True
            report["warnings"].append("로고 주변 이미지가 복잡해 반투명 판을 깔았습니다")
    left_logo = bool(logo_box) and logo_box[0] <= cl + 1
    bottom_logo = bool(logo_box) and tpl != HORIZONTAL and logo_box[1] > ct + 1

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
    avail_w, avail_h = tx1 - tx0, max(ty1 - ty0, 1)

    # 3) 스크림(가독성 보조): 문구 쪽에서 투명해지는 그라디언트
    scrim_opacity = 0.62
    canvas = apply_scrim(canvas, tpl, scrim_hex or "#000000", scrim_opacity)
    report["scrim"] = {"mode": tpl, "color": (scrim_hex or "#000000").upper(), "opacity": scrim_opacity}

    # 4) 제목 크기 맞춤 (잘라내지 않고 크기를 줄이며, 그래도 넘치면 경고만 남긴다)
    draw = ImageDraw.Draw(canvas)
    size = max(24, round(min(w, h) * 0.09))      # 여백이 넓어 보이지 않게 제목을 크게 시작한다(맞지 않으면 줄인다)
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
