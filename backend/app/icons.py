"""아이콘 세트 생성 (6-4). SVG 도형은 모델이, 스타일은 코드가 강제한다.

세트 일관성 규칙(전 아이콘 동일):
- viewBox 24x24, 실제 그림 영역(live area)은 20x20 으로 중앙 정렬 → 여백 2 단위
- 선 굵기·모서리·채움·색은 브랜드 값으로 코드가 지정 (모델이 낸 style/fill/stroke 속성은 모두 버린다)
"""
import io
import json
import re
from xml.sax.saxutils import escape

from PIL import Image

from . import prompts
from .copywriter import CHAT_MODEL, client

COUNT = 10
LIVE = 20.0          # 24 단위 중 그림이 차지하는 크기
VIEW = 24
ALLOWED = {
    "path": {"d"}, "circle": {"cx", "cy", "r"}, "ellipse": {"cx", "cy", "rx", "ry"},
    "rect": {"x", "y", "width", "height", "rx", "ry"}, "line": {"x1", "y1", "x2", "y2"},
    "polyline": {"points"}, "polygon": {"points"},
}
_NUM = re.compile(r"^[0-9eE+\-.,\s]+$")
_PATH = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9eE+\-.,\s]+$")


def sanitize(elements: list) -> list[dict]:
    """허용된 태그·속성만 남긴다. 값은 숫자/경로 문자만 허용 (스크립트·스타일 차단)."""
    out = []
    for el in elements or []:
        if not isinstance(el, dict):
            continue
        tag = str(el.get("tag", "")).lower()
        if tag not in ALLOWED:
            continue
        attrs = {}
        for k, v in (el.get("attrs") or {}).items():
            if k not in ALLOWED[tag]:
                continue
            sv = str(v).strip()
            ok = _PATH.match(sv) if k == "d" else _NUM.match(sv)
            if ok and sv:
                attrs[k] = sv
        need = {"path": {"d"}, "circle": {"cx", "cy", "r"}, "ellipse": {"cx", "cy", "rx", "ry"},
                "rect": {"x", "y", "width", "height"}, "line": {"x1", "y1", "x2", "y2"},
                "polyline": {"points"}, "polygon": {"points"}}[tag]
        if need <= attrs.keys():
            out.append({"tag": tag, "attrs": attrs})
    return out


def _shape_xml(elements: list[dict]) -> str:
    parts = []
    for el in elements:
        a = " ".join(f'{k}="{escape(v)}"' for k, v in el["attrs"].items())
        parts.append(f"<{el['tag']} {a}/>")
    return "".join(parts)


def _wrap(shapes: str, style: dict, scale: float = 1.0, tx: float = 0.0, ty: float = 0.0,
          measure: bool = False) -> str:
    sw = style["stroke_width"] / scale          # 확대·축소해도 실제 선 굵기는 일정하게 유지
    cap = "round" if style["corner"] == "둥근" else "square"
    join = "round" if style["corner"] == "둥근" else "miter"
    fill = style["color"] if style["fill"] == "채움" else "none"
    # 측정할 때는 뷰박스 밖으로 나간 도형이 잘리지 않도록 캔버스를 3배로 넓힌다
    vb, size = (f"{-VIEW} {-VIEW} {VIEW * 3} {VIEW * 3}", VIEW * 3) if measure else (f"0 0 {VIEW} {VIEW}", VIEW)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="{size}" height="{size}">'
            f'<g transform="translate({tx:.3f} {ty:.3f}) scale({scale:.5f})" fill="{fill}" stroke="{style["color"]}" '
            f'stroke-width="{sw:.4f}" stroke-linecap="{cap}" stroke-linejoin="{join}">{shapes}</g></svg>')


def _render(svg: str, px: int) -> Image.Image:
    import resvg_py
    png = resvg_py.svg_to_bytes(svg_string=svg, width=px)
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA")


def _bbox_units(svg: str, wide: bool = False) -> tuple[float, float, float, float] | None:
    """렌더링한 알파 영역의 바깥 경계를 24 단위 좌표로 돌려준다."""
    px = 1440 if wide else 480
    im = _render(svg, px)
    a = im.getchannel("A").point(lambda v: 255 if v > 8 else 0)
    bb = a.getbbox()
    if not bb:
        return None
    k = (VIEW * 3 if wide else VIEW) / px
    off = VIEW if wide else 0
    return bb[0] * k - off, bb[1] * k - off, bb[2] * k - off, bb[3] * k - off


def build_svg(elements: list[dict], style: dict) -> tuple[str, dict]:
    """모든 아이콘이 같은 크기·여백·선 굵기가 되도록 맞춘 최종 SVG 를 만든다."""
    shapes = _shape_xml(elements)
    first = _bbox_units(_wrap(shapes, style, measure=True), wide=True)
    if first is None:
        raise ValueError("그려진 도형이 없습니다")
    x0, y0, x1, y1 = first
    sw = style["stroke_width"]
    gw, gh = max(x1 - x0 - sw, 0.01), max(y1 - y0 - sw, 0.01)      # 선 굵기를 뺀 도형 크기
    s = max((LIVE - sw) / max(gw, gh), 0.01)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    tx, ty = VIEW / 2 - cx * s, VIEW / 2 - cy * s
    svg = _wrap(shapes, style, s, tx, ty)
    fin = _bbox_units(_wrap(shapes, style, s, tx, ty, measure=True), wide=True)
    info = {"scale": round(s, 4), "bbox": [round(v, 2) for v in fin] if fin else None}
    return svg, info


def to_png(svg: str, w: int, h: int) -> bytes:
    side = min(w, h)
    im = _render(svg, side)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(im, ((w - im.width) // 2, (h - im.height) // 2), im)
    b = io.BytesIO()
    canvas.save(b, "PNG")
    return b.getvalue()


# ---- 모델 호출 ---- (문장은 prompts/icons/*.md, 공통 출력 규격은 prompts/icons/_output_spec.md)
def _ask(system: str, user: str) -> dict:
    r = client().chat.completions.create(
        model=CHAT_MODEL, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return json.loads(r.choices[0].message.content or "{}")


def propose_and_draw(brand_name: str, brief: dict, style: dict, forbidden: list[str]) -> list[dict]:
    system = prompts.render("icons/propose_system", count=COUNT)
    user = prompts.render("icons/propose_user", brand_name=brand_name, product_name=brief.get("product_name"),
                          description=brief.get("description") or "없음", key_message=brief.get("key_message") or "없음",
                          target=brief.get("target") or "없음", stroke_width=style["stroke_width"], corner=style["corner"],
                          fill=style["fill"], forbidden=", ".join(forbidden) or "없음", count=COUNT)
    data = _ask(system, user)
    return list(data.get("icons") or [])[:COUNT]


def redraw_one(brand_name: str, brief: dict, name: str, concept: str, style: dict) -> list[dict]:
    system = prompts.render("icons/redraw_system")
    user = prompts.render("icons/redraw_user", brand_name=brand_name, product_name=brief.get("product_name"),
                          name=name, concept=concept)
    data = _ask(system, user)
    items = data.get("icons") or []
    return items[0].get("elements", []) if items else []


def propose_more(brand_name: str, brief: dict, style: dict, forbidden: list[str], existing: list[str], need: int) -> list[dict]:
    """세트가 목표 개수에 못 미칠 때, 이미 있는 기능과 겹치지 않는 기능을 더 받아 온다."""
    system = prompts.render("icons/more_system", need=need)
    user = prompts.render("icons/more_user", brand_name=brand_name, product_name=brief.get("product_name"),
                          description=brief.get("description") or "없음", existing=", ".join(existing),
                          forbidden=", ".join(forbidden) or "없음", need=need)
    return list(_ask(system, user).get("icons") or [])[:need]
