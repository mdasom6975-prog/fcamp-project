"""편집 요소(Element) → 이미지 렌더링과 검증. 자동 배치(compose)와 같은 규칙으로 그린다."""
from PIL import Image, ImageDraw, ImageFont

from . import compose

ALIGNS = ("왼쪽", "가운데", "오른쪽")
LINE_H = 1.28


def default_elements(layout: dict, font_ref: dict, logo_asset_id: int | None) -> list[dict]:
    """6-3 합성 결과(layout)를 편집 가능한 요소 목록으로 바꾼다. 로고는 기본 잠금."""
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
    return els


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
        return {"zoom": num("zoom", 1.0, 4.0), "cx": num("cx", 0.0, 1.0), "cy": num("cy", 0.0, 1.0)}
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
    raise ValueError("알 수 없는 요소 종류")


def render(bg: Image.Image, W: int, H: int, elements: list[dict], font_path_of, logo_of) -> tuple[Image.Image, dict]:
    """elements 를 z 순서대로 그린다. 반환: (이미지, 측정값)"""
    els = sorted(elements, key=lambda e: e.get("z_index", 0))
    img_el = next((e for e in els if e["type"] == "image"), None)
    zp = img_el["props"] if img_el else {"zoom": 1.0, "cx": 0.5, "cy": 0.5}

    # 배경: 늘이지 않고 확대(zoom) + 초점(cx, cy)으로 자른다
    base = bg.convert("RGB")
    scale = max(W / base.width, H / base.height) * zp["zoom"]
    nw, nh = max(W, round(base.width * scale)), max(H, round(base.height * scale))
    big = base.resize((nw, nh), Image.LANCZOS)
    left = min(max(round(zp["cx"] * nw - W / 2), 0), nw - W)
    top = min(max(round(zp["cy"] * nh - H / 2), 0), nh - H)
    canvas = big.crop((left, top, left + W, top + H))

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
            for i, ln in enumerate(lines):
                lw = draw.textlength(ln, font=font)
                dx = 0 if p["align"] == "왼쪽" else (p["w"] - lw) / 2 if p["align"] == "가운데" else p["w"] - lw
                draw.text((int(p["x"] + dx), int(p["y"]) + i * lh), ln, font=font, fill=color)
            over = any(draw.textlength(ln, font=font) > int(p["w"]) for ln in lines)
            measure["text"] = {"lines": lines, "box": [int(p["x"]), int(p["y"]), int(p["x"] + p["w"]),
                                                       int(p["y"]) + lh * len(lines)], "overflow_width": over}
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
