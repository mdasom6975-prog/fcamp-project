"""편집 요소(Element) → 이미지 렌더링과 검증. 자동 배치(compose)와 같은 규칙으로 그린다."""
from PIL import Image, ImageDraw, ImageFont

from . import compose

ALIGNS = ("왼쪽", "가운데", "오른쪽")
LINE_H = 1.28


def default_elements(layout: dict, font_ref: dict, logo_asset_id: int | None, body_font_ref: dict | None = None) -> list[dict]:
    """6-3 합성 결과(layout)를 편집 가능한 요소 목록으로 바꾼다. 로고는 기본 잠금.
    본문(body)·CTA(cta)는 합성 때 지면 설정으로 넣은 경우에만 요소로 나온다."""
    crop = layout.get("crop") or {}     # 6-3 합성이 내용 상자 기준으로 자른 초점을 그대로 이어받는다
    els: list[dict] = [{"type": "image", "role": "배경", "locked": False, "z_index": 0,
                        "props": {"zoom": float(crop.get("zoom", 1.0)), "cx": float(crop.get("cx", 0.5)),
                                  "cy": float(crop.get("cy", 0.5))}}]
    sc = layout.get("scrim") or {"mode": layout.get("template", "정사각"), "color": "#000000", "opacity": 0.62}
    els.append({"type": "shape", "role": "스크림", "locked": False, "z_index": 1,
                "props": {"mode": sc["mode"], "color": sc["color"], "opacity": sc["opacity"]}})
    for e in layout.get("elements", []):
        if e["type"] == "logo":
            x0, y0, x1, _ = e["box"]
            els.append({"type": "logo", "role": "로고", "locked": True, "z_index": 2,
                        "props": {"x": x0, "y": y0, "w": x1 - x0, "asset_id": logo_asset_id}})
        elif e["type"] == "text":
            x0, y0, x1, _ = e["box"]
            els.append({"type": "text", "role": "제목", "locked": False, "z_index": 3,
                        "props": {"text": e["text"], "x": x0, "y": y0, "w": x1 - x0, "font_size": e["font_size"],
                                  "color": e["color"], "align": "왼쪽", "font": font_ref, "text_override": False}})
        elif e["type"] == "body":
            x0, y0, x1, _ = e["box"]
            els.append({"type": "body", "role": "본문", "locked": False, "z_index": 4,
                        "props": {"text": e["text"], "x": x0, "y": y0, "w": x1 - x0, "font_size": e["font_size"],
                                  "color": e["color"], "align": "왼쪽", "font": body_font_ref or font_ref,
                                  "max_lines": max(1, len(e.get("lines") or [1])), "text_override": False}})
        elif e["type"] == "cta":
            x0, y0, x1, y1 = e["box"]
            els.append({"type": "cta", "role": "CTA", "locked": False, "z_index": 5,
                        "props": {"text": e["text"], "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "font_size": e["font_size"],
                                  "bg_color": e["bg_color"], "text_color": e["text_color"], "font": font_ref,
                                  "corner": cta_corner(e.get("corner")), "text_override": False}})
    return els


def cta_corner(v) -> str:
    """CTA 버튼 모서리를 둥근/각진으로 정규화한다. 예전 합성 결과는 compose 의 변수 가림으로 corner 에 로고 위치(좌상단 등)가
    남아 있어, 각진이 아닌 값은 모두 둥근으로 본다(그때도 둥글게 그려졌다)."""
    return "각진" if v == "각진" else "둥근"


def _hexok(v) -> bool:
    return isinstance(v, str) and len(v) == 7 and v[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in v[1:])


def validate_props(el_type: str, p: dict, canvas: tuple[int, int]) -> dict:
    """범위를 벗어난 값을 거절한다 (조용히 보정하지 않는다)."""
    W, H = canvas
    def num(k, lo, hi, cast=float):
        if k not in p:
            raise ValueError(f"{k} 값이 없습니다")
        try:
            v = cast(p[k])
        except Exception:
            raise ValueError(f"{k} 는 숫자여야 합니다")
        if not (lo <= v <= hi):
            raise ValueError(f"{k} 는 {lo}~{hi} 범위여야 합니다")
        return v
    if el_type == "image":
        return {"zoom": num("zoom", 0.5, 4.0), "cx": num("cx", 0.0, 1.0), "cy": num("cy", 0.0, 1.0)}   # 0.5~1 은 축소(여백은 가장자리 색으로 채움)
    if el_type == "shape":
        if not _hexok(p.get("color")):
            raise ValueError("color 는 #RRGGBB 형식이어야 합니다")
        if p.get("mode") not in (compose.HORIZONTAL, compose.SQUARE, compose.VERTICAL):
            raise ValueError("mode 가 올바르지 않습니다")
        return {"mode": p["mode"], "color": p["color"].upper(), "opacity": num("opacity", 0.0, 1.0)}
    if el_type == "logo":
        return {"x": num("x", -W, 2 * W, int), "y": num("y", -H, 2 * H, int), "w": num("w", 8, 2 * W, int),
                "asset_id": p.get("asset_id")}
    if el_type == "text":
        if not _hexok(p.get("color")):
            raise ValueError("color 는 #RRGGBB 형식이어야 합니다")
        text = str(p.get("text", "")).strip()
        if not text:
            raise ValueError("문구가 비어 있습니다")
        if len(text) > 300:
            raise ValueError("문구는 300자 이하여야 합니다")
        if p.get("align") not in ALIGNS:
            raise ValueError(f"align 은 {ALIGNS} 중 하나")
        font = p.get("font") or {}
        if font.get("source") not in ("provided", "upload"):
            raise ValueError("font 가 올바르지 않습니다")
        return {"text": text, "x": num("x", -W, 2 * W, int), "y": num("y", -H, 2 * H, int),
                "w": num("w", 20, 2 * W, int), "font_size": num("font_size", 8, 400, int),
                "color": p["color"].upper(), "align": p["align"], "font": font,
                "text_override": bool(p.get("text_override", False))}
    if el_type == "body":
        if not _hexok(p.get("color")):
            raise ValueError("color 는 #RRGGBB 형식이어야 합니다")
        text = str(p.get("text", "")).strip()
        if not text:
            raise ValueError("본문이 비어 있습니다")
        if len(text) > 600:
            raise ValueError("본문은 600자 이하여야 합니다")
        if p.get("align") not in ALIGNS:
            raise ValueError(f"align 은 {ALIGNS} 중 하나")
        font = p.get("font") or {}
        if font.get("source") not in ("provided", "upload"):
            raise ValueError("font 가 올바르지 않습니다")
        return {"text": text, "x": num("x", -W, 2 * W, int), "y": num("y", -H, 2 * H, int),
                "w": num("w", 20, 2 * W, int), "font_size": num("font_size", 8, 400, int),
                "color": p["color"].upper(), "align": p["align"], "font": font,
                "max_lines": num("max_lines", 1, 6, int) if "max_lines" in p else 2,
                "text_override": bool(p.get("text_override", False))}
    if el_type == "cta":
        for k in ("bg_color", "text_color"):
            if not _hexok(p.get(k)):
                raise ValueError(f"{k} 는 #RRGGBB 형식이어야 합니다")
        text = str(p.get("text", "")).strip()
        if not text:
            raise ValueError("CTA 문구가 비어 있습니다")
        if len(text) > 20:
            raise ValueError("CTA 문구는 20자 이하여야 합니다")
        font = p.get("font") or {}
        if font.get("source") not in ("provided", "upload"):
            raise ValueError("font 가 올바르지 않습니다")
        return {"text": text, "x": num("x", -W, 2 * W, int), "y": num("y", -H, 2 * H, int),
                "w": num("w", 16, 2 * W, int), "h": num("h", 12, H, int), "font_size": num("font_size", 8, 200, int),
                "bg_color": p["bg_color"].upper(), "text_color": p["text_color"].upper(), "font": font,
                "corner": cta_corner(p.get("corner")), "text_override": bool(p.get("text_override", False))}
    raise ValueError("알 수 없는 요소 종류")


def edge_color(img: Image.Image) -> tuple[int, int, int]:
    """이미지 가장자리의 중앙값 색 — 축소했을 때 남는 자리를 채우는 데 쓴다(단색 벽·바닥 배경에서 자연스럽다)."""
    small = img.convert("RGB").resize((48, 48), Image.BILINEAR)
    px = small.load()
    border = [px[x, y] for x in range(48) for y in (0, 47)] + [px[x, y] for y in range(48) for x in (0, 47)]
    return tuple(sorted(c[i] for c in border)[len(border) // 2] for i in range(3))  # type: ignore[return-value]


def _placed(draw, lines: list[str], font, p: dict, lh: int) -> list[tuple[int, int, str]]:
    """정렬(왼쪽·가운데·오른쪽)을 반영한 줄별 (x, y, 글) 목록."""
    out = []
    for i, ln in enumerate(lines):
        lw = draw.textlength(ln, font=font)
        dx = 0 if p["align"] == "왼쪽" else (p["w"] - lw) / 2 if p["align"] == "가운데" else p["w"] - lw
        out.append((int(p["x"] + dx), int(p["y"]) + i * lh, ln))
    return out


def render(bg: Image.Image, W: int, H: int, elements: list[dict], font_path_of, logo_of) -> tuple[Image.Image, dict]:
    """elements 를 z 순서대로 그린다. 반환: (이미지, 측정값)"""
    els = sorted(elements, key=lambda e: e.get("z_index", 0))
    img_el = next((e for e in els if e["type"] == "image"), None)
    zp = img_el["props"] if img_el else {"zoom": 1.0, "cx": 0.5, "cy": 0.5}

    # 배경: 늘이지 않고 확대/축소(zoom) + 초점(cx, cy). zoom<1 이면 이미지가 캔버스보다 작아져 남는 자리는 가장자리 색으로 채운다
    base = bg.convert("RGB")
    scale = max(W / base.width, H / base.height) * zp["zoom"]
    nw, nh = max(1, round(base.width * scale)), max(1, round(base.height * scale))
    big = base.resize((nw, nh), Image.LANCZOS)
    left = round(zp["cx"] * nw - W / 2)
    top = round(zp["cy"] * nh - H / 2)
    left = min(max(left, 0), nw - W) if nw >= W else left        # 캔버스보다 크면 안쪽으로 고정, 작으면 어디든
    top = min(max(top, 0), nh - H) if nh >= H else top
    if nw >= W and nh >= H:
        canvas = big.crop((left, top, left + W, top + H))
    else:
        canvas = Image.new("RGB", (W, H), edge_color(big))
        canvas.paste(big, (-left, -top))

    measure: dict = {}
    for e in els:
        p = e["props"]
        if e["type"] == "shape":
            canvas = compose.apply_scrim(canvas, p["mode"], p["color"], p["opacity"])
        elif e["type"] == "logo":
            logo = logo_of(p.get("asset_id"))
            if logo is None:
                measure["logo"] = {"missing": True}
                continue
            w = max(1, int(p["w"]))
            h = max(1, round(logo.height * w / logo.width))     # 원본 비율 고정
            canvas.paste(logo.resize((w, h), Image.LANCZOS), (int(p["x"]), int(p["y"])),
                         logo.resize((w, h), Image.LANCZOS))
            measure["logo"] = {"box": [int(p["x"]), int(p["y"]), int(p["x"]) + w, int(p["y"]) + h]}
        elif e["type"] == "text":
            draw = ImageDraw.Draw(canvas)
            font = ImageFont.truetype(font_path_of(p["font"]), int(p["font_size"]))
            lines = compose._wrap(draw, p["text"], font, int(p["w"]))
            lh = round(int(p["font_size"]) * LINE_H)
            color = compose._hex(p["color"])
            canvas = compose.draw_text_soft(canvas, _placed(draw, lines, font, p, lh), font, color)
            over = any(draw.textlength(ln, font=font) > int(p["w"]) for ln in lines)
            measure["text"] = {"lines": lines, "box": [int(p["x"]), int(p["y"]), int(p["x"] + p["w"]),
                                                       int(p["y"]) + lh * len(lines)], "overflow_width": over}
        elif e["type"] == "body":
            draw = ImageDraw.Draw(canvas)
            font = ImageFont.truetype(font_path_of(p["font"]), int(p["font_size"]))
            lines = compose._wrap(draw, p["text"], font, int(p["w"]))
            over_lines = len(lines) > int(p.get("max_lines", 2))     # 말줄임 없이 모든 줄을 그리고, 상한 초과만 알린다
            lh = round(int(p["font_size"]) * LINE_H)
            color = compose._hex(p["color"])
            canvas = compose.draw_text_soft(canvas, _placed(draw, lines, font, p, lh), font, color)
            measure["body"] = {"lines": lines, "cut": False, "over_lines": over_lines, "box": [int(p["x"]), int(p["y"]), int(p["x"] + p["w"]),
                                                                  int(p["y"]) + lh * len(lines)]}
        elif e["type"] == "cta":
            box = [int(p["x"]), int(p["y"]), int(p["x"] + p["w"]), int(p["y"] + p["h"])]
            compose.draw_cta(canvas, box, p["text"], font_path_of(p["font"]), int(p["font_size"]),
                             compose._hex(p["bg_color"]), compose._hex(p["text_color"]), cta_corner(p.get("corner")))
            draw = ImageDraw.Draw(canvas)
            font = ImageFont.truetype(font_path_of(p["font"]), int(p["font_size"]))
            measure["cta"] = {"box": box, "overflow_width": draw.textlength(p["text"], font=font) > int(p["w"]) - 8,
                              "contrast": compose.contrast(compose._hex(p["bg_color"]), compose._hex(p["text_color"]))}
    return canvas, measure


def warnings_for(measure: dict, W: int, H: int, safe: dict | None, brand_hexes: set[str],
                 forbidden: list[str], text: str | None, canvas_img: Image.Image, text_color: str | None) -> list[str]:
    out: list[str] = []
    L, T, R, B, _ = compose._margins(W, H, safe)
    def box_warn(label, box):
        x0, y0, x1, y1 = box
        if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
            out.append(f"{label}이(가) 캔버스 밖으로 나갑니다 (잘림)")
        elif x0 < L or y0 < T or x1 > W - R or y1 > H - B:
            out.append(f"{label}이(가) 안전영역/여백 안쪽에 걸쳐 있습니다")
    if "text" in measure:
        box_warn("문구", measure["text"]["box"])
        if measure["text"]["overflow_width"]:
            out.append("문구 한 줄이 상자 너비보다 깁니다")
    if "logo" in measure and measure["logo"].get("box"):
        box_warn("로고", measure["logo"]["box"])
    if "body" in measure:
        box_warn("본문", measure["body"]["box"])
        if measure["body"].get("over_lines"):
            out.append("본문이 줄 수 상한을 넘었습니다 (말줄임 없이 모두 표시). 글자 크기를 줄이거나 줄 수 상한을 늘리세요")
    if "cta" in measure:
        box_warn("CTA 버튼", measure["cta"]["box"])
        if measure["cta"].get("overflow_width"):
            out.append("CTA 문구가 버튼보다 깁니다")
        if measure["cta"].get("contrast", 21) < 4.5:
            out.append("CTA 버튼의 글자·배경 대비가 낮습니다 (4.5:1 미만)")
    if text_color and brand_hexes and text_color.upper() not in brand_hexes:
        out.append("문구 색이 브랜드에 등록된 색이 아닙니다")
    if text and "text" in measure and text_color:
        x0, y0, x1, y1 = measure["text"]["box"]
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        if x1 > x0 and y1 > y0:
            reg = canvas_img.crop((x0, y0, x1, y1)).resize((1, 1), Image.BOX).getpixel((0, 0))
            if compose.contrast(compose._hex(text_color), reg) < 4.5:
                out.append("문구와 배경의 대비가 낮아 읽기 어려울 수 있습니다")
    for ph in forbidden:
        if ph and text and ph in text:
            out.append(f"브랜드 금지 표현 '{ph}' 이(가) 포함되어 있습니다")
    return out
