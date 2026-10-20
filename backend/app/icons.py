"""아이콘 세트 생성 (6-4). 두 가지 렌더 방식을 ICON_RENDER 로 고른다.

- 3d (기본): 텍스트 모델이 기능명·오브젝트 묘사를 정하고, 이미지 모델이 아이콘마다 투명 배경 PNG 를 렌더한다.
  세트 일관성은 공통 스타일 블록(prompts/icons/_3d_style.md: 같은 카메라·광원·재질·색)으로 잡는다.
- svg: 텍스트 모델이 SVG 도형(JSON)을 내고 코드가 선 굵기·모서리·채움·색을 브랜드 값으로 강제한다.

두 방식 공통 규칙: viewBox 24 단위 기준으로 그림 영역(live area) 20 을 중앙 정렬 → 여백 2 단위.
3d 는 알파 채널의 경계 상자를, svg 는 렌더링한 도형의 경계 상자를 기준으로 맞춘다.
"""
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from xml.sax.saxutils import escape

from PIL import Image

from . import imagegen, prompts
from .copywriter import ICON_CANDIDATES

COUNT = 10
CANDIDATES = ICON_CANDIDATES   # 제안 호출 1회에 받는 기능명 수(여유분 포함). 중복·오류 제거 뒤 COUNT 개를 쓴다
LIVE = 20.0          # 24 단위 중 그림이 차지하는 크기
VIEW = 24
RENDER = os.getenv("ICON_RENDER", "3d").strip().lower()          # 3d | svg
MODEL_3D = os.getenv("ICON_IMAGE_MODEL", imagegen.OPENAI_MODEL)    # 3D 아이콘 이미지 모델 (기본: 배경과 같은 모델)
QUALITY_3D = os.getenv("ICON_IMAGE_QUALITY", "medium")             # low | medium | high
WORKERS_3D = 3                                                     # 동시 렌더 수 (세트 10개 → 약 2~3분)
# 격자 렌더(ICON_GRID="열x행" 또는 n → n×n, 0 이면 끔): 한 장에 여러 아이콘을 그려 칸 단위로 잘라 쓴다.
# 기본 4x3: 가로 캔버스(1536×1024)에 12칸 → 세트 10개가 호출 1회. 칸은 384×341px 라 출력(256px)에 충분하다.
# 2 (2×2, 1024²) 는 10개가 3회·칸 512px 로 더 안전하다. 모델이 격자를 어기면 칸이 잘려 개별 '다시 그리기'(1장)가 필요할 수 있다.
def _parse_grid(v: str) -> tuple[int, int]:
    v = (v or "").strip().lower()
    if not v or v in ("0", "off", "false"):
        return 0, 0
    if "x" in v:
        c, r = v.split("x", 1)
        return max(0, int(c or 0)), max(0, int(r or 0))
    n = max(0, int(v))
    return (n, n) if n >= 2 else (0, 0)


GRID_COLS, GRID_ROWS = _parse_grid(os.getenv("ICON_GRID", "4x3"))
GRID_CELLS = GRID_COLS * GRID_ROWS if GRID_COLS >= 1 and GRID_ROWS >= 1 and GRID_COLS * GRID_ROWS >= 2 else 0
# 격자 캔버스: 열이 많으면 가로(1536×1024), 행이 많으면 세로, 같으면 정사각 — 칸이 정사각에 가깝게
GRID_SIZE = "1536x1024" if GRID_COLS > GRID_ROWS else "1024x1536" if GRID_ROWS > GRID_COLS else "1024x1024"


def grid_label() -> str | None:
    return f"{GRID_COLS}x{GRID_ROWS}" if GRID_CELLS else None
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
def _ask(system: str, user: str, kind: str = "icon_names", schema: dict | None = None) -> dict:
    """텍스트 모델에 JSON 을 요청한다(공급자 전환은 llm.py). schema 는 svg 제안이면 ICONS_SVG_SCHEMA, 아니면 3D 기능명 스키마."""
    from . import llm
    return llm.chat_json(system, user, kind, schema or llm.ICONS_3D_SCHEMA)


def propose_and_draw(brand_name: str, brief: dict, style: dict, forbidden: list[str]) -> list[dict]:
    system = prompts.render("icons/propose_system", count=CANDIDATES)
    user = prompts.render("icons/propose_user", brand_name=brand_name, product_name=brief.get("product_name"),
                          description=brief.get("description") or "없음", key_message=brief.get("key_message") or "없음",
                          target=brief.get("target") or "없음", stroke_width=style["stroke_width"], corner=style["corner"],
                          fill=style["fill"], forbidden=", ".join(forbidden) or "없음", count=CANDIDATES)
    from . import llm
    data = _ask(system, user, schema=llm.ICONS_SVG_SCHEMA)
    return list(data.get("icons") or [])[:CANDIDATES]


def redraw_one(brand_name: str, brief: dict, name: str, concept: str, style: dict) -> list[dict]:
    system = prompts.render("icons/redraw_system")
    user = prompts.render("icons/redraw_user", brand_name=brand_name, product_name=brief.get("product_name"),
                          name=name, concept=concept)
    from . import llm
    data = _ask(system, user, "icon_redraw", schema=llm.ICONS_SVG_SCHEMA)
    items = data.get("icons") or []
    return items[0].get("elements", []) if items else []


def propose_more(brand_name: str, brief: dict, style: dict, forbidden: list[str], existing: list[str], need: int) -> list[dict]:
    """세트가 목표 개수에 못 미칠 때, 이미 있는 기능과 겹치지 않는 기능을 더 받아 온다."""
    system = prompts.render("icons/more_system", need=need)
    user = prompts.render("icons/more_user", brand_name=brand_name, product_name=brief.get("product_name"),
                          description=brief.get("description") or "없음", existing=", ".join(existing),
                          forbidden=", ".join(forbidden) or "없음", need=need)
    from . import llm
    return list(_ask(system, user, schema=llm.ICONS_SVG_SCHEMA).get("icons") or [])[:need]


# ---- 3D 렌더 ---- (기능명·오브젝트 묘사는 prompts/icons/propose3d_*·more3d_*, 렌더 문장은 render3d + _3d_style)
PALETTE_ROLES = ("주조색", "보조색", "강조색")


def style_vars(style: dict, palette: list[dict], tone: dict) -> dict:
    """_3d_style 블록의 변수. 색은 브랜드 주조·보조·강조색(없으면 아이콘 색 하나), 모서리는 icon_style.corner,
    재질·광원은 image_tone 설명. 분기('지정 없음')는 여기서 계산해 md 에는 조건문을 두지 않는다."""
    cols = [f"{c['hex']}({c['role']})" for role in PALETTE_ROLES for c in palette if c.get("role") == role and c.get("hex")]
    material = (style.get("material") or "자동").strip()
    return {"corner": style.get("corner") or "둥근",
            "colors": ", ".join(cols) or f"{style.get('color', '#000000')}(주조색)",
            # 재질 프리셋이 있으면 그 재질을 세트 전체에 고정, '자동'이면 톤 설명의 재질 묘사를 따른다(분기는 여기서 계산)
            "material": "아래 브랜드 톤 설명의 재질·광택 묘사를 따른다" if material == "자동" else f"{material} 재질로 통일한다",
            "tone": (tone or {}).get("description") or "지정 없음"}


def propose3d(brand_name: str, brief: dict, forbidden: list[str]) -> list[dict]:
    """기능명 COUNT 개 + 렌더할 오브젝트 묘사(concept). 도형은 받지 않는다."""
    system = prompts.render("icons/propose3d_system", count=CANDIDATES)
    user = prompts.render("icons/propose3d_user", brand_name=brand_name, product_name=brief.get("product_name"),
                          description=brief.get("description") or "없음", key_message=brief.get("key_message") or "없음",
                          target=brief.get("target") or "없음", forbidden=", ".join(forbidden) or "없음", count=CANDIDATES)
    return list(_ask(system, user).get("icons") or [])[:CANDIDATES]


def propose3d_more(brand_name: str, brief: dict, forbidden: list[str], existing: list[str], need: int) -> list[dict]:
    system = prompts.render("icons/more3d_system", need=need)
    user = prompts.render("icons/more3d_user", brand_name=brand_name, product_name=brief.get("product_name"),
                          description=brief.get("description") or "없음", existing=", ".join(existing),
                          forbidden=", ".join(forbidden) or "없음", need=need)
    return list(_ask(system, user).get("icons") or [])[:need]


def render_prompt(name: str, concept: str, vars: dict) -> str:
    """아이콘 1개의 이미지 프롬프트. 다시 그리기도 같은 문장으로 재렌더한다(이미지 모델의 무작위성으로 구도가 바뀐다)."""
    return prompts.render("icons/render3d", name=name, concept=concept or name, **vars)


def render3d(prompt: str) -> Image.Image:
    return imagegen.generate_transparent(prompt, MODEL_3D, QUALITY_3D, kind="icon")


def render_grid_prompt(cells: list[tuple[str, str]], vars: dict, cols: int, rows: int) -> str:
    """격자 1장의 프롬프트. cells = [(기능명, 오브젝트 묘사)] (최대 cols*rows). 빈 칸은 모델에게 비워 두라고 한다."""
    lines = [f"{i}. \"{name}\": {concept or name}" for i, (name, concept) in enumerate(cells, 1)]
    return prompts.render("icons/render3d_grid", count=len(cells), cols=cols, rows=rows, cells="\n".join(lines), **vars)


def render_grid(prompt: str) -> Image.Image:
    """격자 1장을 격자 캔버스 크기로 렌더한다."""
    return imagegen.generate_transparent(prompt, MODEL_3D, QUALITY_3D, kind="icon", size=GRID_SIZE)


def split_grid(im: Image.Image, cols: int, rows: int) -> list[Image.Image]:
    """cols×rows 격자 이미지를 칸 순서(왼쪽 위 → 오른쪽 → 다음 줄)대로 잘라 돌려준다. 칸 경계 2% 는 이웃 번짐을 피해 버린다."""
    im = im.convert("RGBA")
    cw, ch = im.width / cols, im.height / rows
    pad_x, pad_y = cw * 0.02, ch * 0.02
    out = []
    for r in range(rows):
        for c in range(cols):
            box = (round(c * cw + pad_x), round(r * ch + pad_y), round((c + 1) * cw - pad_x), round((r + 1) * ch - pad_y))
            out.append(im.crop(box))
    return out


def render_many(prompt_list: list[str], fn=None) -> list[Image.Image | Exception]:
    """여러 장을 동시에 렌더한다(fn 기본 render3d, 격자는 render_grid). 실패는 예외 객체로 돌려주어 호출자가 건너뛰기·재시도를 정한다."""
    fn = fn or render3d
    def one(pr: str):
        try:
            return fn(pr)
        except Exception as e:  # noqa: BLE001 — 개별 실패가 세트 전체를 막지 않게
            return e
    ctx = copy_context()     # 호출 기록의 시안 id(ContextVar)가 작업 스레드에도 보이게
    with ThreadPoolExecutor(max_workers=WORKERS_3D) as ex:
        return list(ex.map(lambda pr: ctx.copy().run(one, pr), prompt_list))


def fit_alpha(im: Image.Image, w: int, h: int) -> tuple[Image.Image, dict]:
    """렌더된 RGBA 이미지를 알파 경계 기준으로 잘라 live area(짧은 변의 20/24)에 맞춰 중앙 정렬한다.
    bbox 는 svg 방식과 같은 24 단위 좌표(짧은 변 기준 정사각)로 기록해 여백 검수를 같은 식으로 한다."""
    im = im.convert("RGBA")
    a = im.getchannel("A").point(lambda v: 255 if v > 8 else 0)
    bb = a.getbbox()
    if not bb:
        raise ValueError("그려진 오브젝트가 없습니다")
    # 배경이 투명하게 나왔는지: 네 모서리가 비어 있고 알파 영역이 전체를 덮지 않아야 한다
    corners = [im.getpixel((x, y))[3] for x in (0, im.width - 1) for y in (0, im.height - 1)]
    cover = (bb[2] - bb[0]) * (bb[3] - bb[1]) / (im.width * im.height)
    transparent = max(corners) <= 8 and cover < 0.97
    crop = im.crop(bb)
    side = min(w, h)
    s = side * LIVE / VIEW / max(crop.width, crop.height)
    nw, nh = max(1, round(crop.width * s)), max(1, round(crop.height * s))
    small = crop.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(small, ((w - nw) // 2, (h - nh) // 2), small)
    k = VIEW / side
    bbox = [VIEW / 2 - nw * k / 2, VIEW / 2 - nh * k / 2, VIEW / 2 + nw * k / 2, VIEW / 2 + nh * k / 2]
    return canvas, {"scale": round(s, 4), "bbox": [round(v, 2) for v in bbox], "transparent": transparent}


def png_bytes(im: Image.Image) -> bytes:
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()
