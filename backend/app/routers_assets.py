import io
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fontTools.ttLib import TTFont
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import storage
from .db import BASE_DIR, SessionLocal
from .models import Asset, Design, GeneratedImage

router = APIRouter(prefix="/api")
FONT_DIR = BASE_DIR / "fonts"
MAX_BYTES = 10 * 1024 * 1024
LOGO_MAX_BYTES = 300 * 1024      # 로고 파일 크기 기준 (300KB 이하)
PRODUCT_MAX_BYTES = 200 * 1024   # 제품 이미지 크기 기준 (200KB 이하)
SQUARE_MIN = 128                 # 정사각 로고 최소 크기 (PMax 1:1 로고 128×128 이상)
SQUARE_SVG_SIZE = 1200           # 정사각으로 만든 SVG 의 width/height (권장 1200×1200, 벡터라 그대로 커진다)
SQUARE_PAD = 0.1                 # 정사각으로 만들 때 긴 변 양쪽에 두는 투명 여백 (긴 변 대비)
SQUARE_TOL = 0.05                # 이 비율 차이 이내면 이미 1:1 로 본다
WIDE_WARN = 3.0                  # 이보다 가로(세로)로 긴 로고는 정사각 썸네일에서 작게 보인다고 경고

PROVIDED_FONTS = [
    {"name": "Pretendard-Regular", "label": "Pretendard Regular", "file": "Pretendard-Regular.otf"},
    {"name": "Pretendard-Bold", "label": "Pretendard Bold", "file": "Pretendard-Bold.otf"},
]


def get_db():
    with SessionLocal() as db:
        yield db


def get_design(db: Session, design_id: int, ad_only: bool = False) -> Design:
    """캠페인을 찾는다. 없으면 404. ad_only 면 광고 이미지 캠페인만(아이콘 세트 캠페인도 404)."""
    d = db.get(Design, design_id)
    if not d or (ad_only and d.kind != "광고 이미지"):
        raise HTTPException(404, "광고 이미지 캠페인을 찾을 수 없습니다" if ad_only else "캠페인을 찾을 수 없습니다")
    return d


def latest_image(db: Session, design_id: int, preset_id: int, kind: str, original_only: bool = False) -> GeneratedImage | None:
    """지면의 가장 최근 이미지 행(kind: background | composite). original_only 면 편집 저장본(layout.edited)을 건너뛴다."""
    q = select(GeneratedImage).where(GeneratedImage.design_id == design_id, GeneratedImage.preset_version_id == preset_id,
                                     GeneratedImage.kind == kind).order_by(GeneratedImage.id.desc())
    if not original_only:
        return db.scalar(q.limit(1))
    return next((r for r in db.scalars(q) if not (r.layout or {}).get("edited")), None)


def _svg_size(data: bytes) -> tuple[int | None, int | None]:
    head = data[:4096].decode("utf-8", "ignore")
    if "<svg" not in head:
        raise HTTPException(400, "SVG 파일이 아닙니다")
    m = re.search(r'viewBox\s*=\s*"[\s,]*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)"', head)
    if m:
        return round(float(m.group(1))), round(float(m.group(2)))
    w = re.search(r'\bwidth\s*=\s*"([\d.]+)', head)
    h = re.search(r'\bheight\s*=\s*"([\d.]+)', head)
    return (round(float(w.group(1))) if w else None, round(float(h.group(1))) if h else None)


def _is_square(w: int, h: int) -> bool:
    return abs(w / h - 1) <= SQUARE_TOL


def _square_png(im: Image.Image) -> tuple[bytes, int, int]:
    """원본 PNG 를 늘리거나 자르지 않고 투명 정사각 캔버스 가운데에 붙인다. 긴 변 양쪽에 SQUARE_PAD 여백."""
    w, h = im.size
    side = round(max(w, h) * (1 + 2 * SQUARE_PAD))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im.convert("RGBA"), ((side - w) // 2, (side - h) // 2))
    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    return buf.getvalue(), side, side


_SVG_ROOT = re.compile(r"<svg\b[^>]*>", re.S)
_VIEWBOX = re.compile(r"""\sviewBox\s*=\s*["']([^"']*)["']""")


def _svg_attr(tag: str, name: str) -> re.Pattern:
    return re.compile(rf"""(?<![\w:-]){name}\s*=\s*["']([^"']*)["']""")


def _svg_root_box(text: str) -> tuple[re.Match, float, float, float, float]:
    """루트 <svg> 태그와 그 좌표 상자 (x, y, w, h). viewBox 우선, 없으면 width·height. 자식 도형의 width 는 보지 않는다
    (_svg_size 는 앞부분 전체에서 찾으므로 루트에 크기가 없으면 자식 도형의 크기를 읽는다). 읽을 수 없으면 거절한다."""
    m = _SVG_ROOT.search(text)
    if not m:
        raise HTTPException(400, "SVG 파일이 아닙니다")
    tag = m.group(0)
    vb = _VIEWBOX.search(tag)
    try:
        if vb:
            x, y, w, h = (float(v) for v in re.split(r"[\s,]+", vb.group(1).strip()))
        else:
            wm, hm = _svg_attr(tag, "width").search(tag), _svg_attr(tag, "height").search(tag)
            x = y = 0.0
            w, h = float(re.match(r"[\d.]+", wm.group(1)).group()), float(re.match(r"[\d.]+", hm.group(1)).group())
    except (AttributeError, ValueError):
        raise HTTPException(400, "SVG 의 크기(viewBox 또는 width·height)를 읽을 수 없어 정사각으로 만들 수 없습니다")
    if w <= 0 or h <= 0:
        raise HTTPException(400, "SVG 의 크기가 올바르지 않습니다")
    return m, x, y, w, h


def _square_svg(data: bytes) -> tuple[bytes, int, int]:
    """원본 SVG 의 도형은 그대로 두고 루트 <svg> 의 viewBox 만 정사각으로 넓힌다(가운데 정렬, 긴 변 양쪽에 SQUARE_PAD 여백).
    width/height 는 SQUARE_SVG_SIZE 로 둔다."""
    text = data.decode("utf-8", "ignore")
    m, x, y, w, h = _svg_root_box(text)
    tag = m.group(0)
    side = max(w, h) * (1 + 2 * SQUARE_PAD)
    box = f"{x - (side - w) / 2:g} {y - (side - h) / 2:g} {side:g} {side:g}"
    new = _VIEWBOX.sub("", tag)
    for name in ("width", "height", "preserveAspectRatio"):
        new = _svg_attr(new, name).sub("", new)
    new = re.sub(r"^<svg\b", f'<svg viewBox="{box}" width="{SQUARE_SVG_SIZE}" height="{SQUARE_SVG_SIZE}" '
                 'preserveAspectRatio="xMidYMid meet"', new)
    out = text[:m.start()] + new + text[m.end():]
    return out.encode("utf-8"), SQUARE_SVG_SIZE, SQUARE_SVG_SIZE


def _make_square(data: bytes, ext: str, width: int | None, height: int | None) -> tuple[bytes, int, int, str | None]:
    """정사각 로고 칸에 올린 파일을 1:1 로 맞춘다. 반환: (데이터, 가로, 세로, 화면에 띄울 안내 또는 None).
    이미 1:1 이면 그대로(작으면 거절), 아니면 투명 여백을 넣어 정사각으로 만든다(로고 자체는 늘리거나 자르지 않는다)."""
    if ext == "svg":     # 크기는 루트 태그에서 다시 읽는다(_svg_root_box)
        _, _, _, bw, bh = _svg_root_box(data.decode("utf-8", "ignore"))
        width, height = round(bw), round(bh)
    if width and height and _is_square(width, height):
        if ext == "png" and min(width, height) < SQUARE_MIN:
            raise HTTPException(400, f"정사각 로고는 {SQUARE_MIN}×{SQUARE_MIN} 이상이어야 합니다 (올린 파일 {width}×{height})")
        return data, width, height, None
    if ext == "png":
        if max(width, height) * (1 + 2 * SQUARE_PAD) < SQUARE_MIN:
            raise HTTPException(400, f"파일이 너무 작아 정사각으로 만들면 {SQUARE_MIN}×{SQUARE_MIN} 보다 작습니다 "
                                     f"(올린 파일 {width}×{height}). 더 큰 PNG 나 SVG 를 올려 주세요")
        out, w, h = _square_png(Image.open(io.BytesIO(data)))
    else:
        out, w, h = _square_svg(data)
    note = f"1:1 이 아니라 투명 여백을 넣어 정사각으로 만들었습니다 (원본 {width}×{height} → {w}×{h})" \
        if width and height else f"투명 여백을 넣어 정사각으로 만들었습니다 ({w}×{h})"
    if width and height and max(width / height, height / width) >= WIDE_WARN:
        note += ". 로고가 길쭉해 정사각 썸네일에서는 작게 보일 수 있습니다. 심볼형 파일이 있으면 그쪽을 올려 주세요"
    return out, w, h, note


@router.get("/fonts/provided")
def provided_fonts():
    return PROVIDED_FONTS


@router.post("/assets")
async def upload_asset(kind: str = Form(...), brand_id: int | None = Form(None), square: bool = Form(False),
                       file: UploadFile = File(...), db: Session = Depends(get_db)):
    """kind: logo | font | tone_ref | product. square=True(정사각 로고 칸)면 1:1 이 아닌 로고에 투명 여백을 넣어 정사각으로 저장하고
    응답의 note 에 안내를 담는다(_make_square)."""
    if kind not in ("logo", "font", "tone_ref", "product"):
        raise HTTPException(400, "kind 는 logo / font / tone_ref / product")
    data = await file.read()
    if not data:
        raise HTTPException(400, "빈 파일입니다")
    if len(data) > MAX_BYTES:
        raise HTTPException(400, "파일은 10MB 이하여야 합니다")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    width = height = None
    note = None

    if kind == "logo":
        if len(data) > LOGO_MAX_BYTES:
            raise HTTPException(400, "로고 파일은 300KB 이하여야 합니다")
        if ext == "svg":
            width, height = _svg_size(data)
            mime = "image/svg+xml"
        elif ext == "png":
            try:
                im = Image.open(io.BytesIO(data))
                im.verify()
                im = Image.open(io.BytesIO(data))
            except Exception:
                raise HTTPException(400, "PNG 파일을 읽을 수 없습니다")
            if im.format != "PNG":
                raise HTTPException(400, "PNG 파일이 아닙니다")
            width, height = im.size
            if "A" not in im.getbands() and "transparency" not in im.info:
                raise HTTPException(400, "투명 배경 PNG 가 아닙니다. 투명 PNG 또는 SVG 를 올려 주세요")
            mime = "image/png"
        else:
            raise HTTPException(400, "로고는 PNG(투명) 또는 SVG 만 올릴 수 있습니다")
        if square:
            data, width, height, note = _make_square(data, ext, width, height)
    elif kind == "product":
        if len(data) > PRODUCT_MAX_BYTES:
            raise HTTPException(400, "제품 이미지는 200KB 이하여야 합니다")
        if ext not in ("png", "jpg", "jpeg", "webp"):
            raise HTTPException(400, "제품 이미지는 PNG·JPG·WEBP 만 올릴 수 있습니다")
        try:
            im = Image.open(io.BytesIO(data))
            im.verify()
            im = Image.open(io.BytesIO(data))
            width, height = im.size
        except Exception:
            raise HTTPException(400, "이미지 파일을 읽을 수 없습니다")
        mime = {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    elif kind == "font":
        if ext not in ("ttf", "otf"):
            raise HTTPException(400, "폰트는 TTF 또는 OTF 만 올릴 수 있습니다")
        try:
            cmap = TTFont(io.BytesIO(data)).getBestCmap()
        except Exception:
            raise HTTPException(400, "폰트 파일을 읽을 수 없습니다")
        if 0xAC00 not in cmap:
            raise HTTPException(400, "한글 글리프가 없는 폰트입니다. 한글을 지원하는 폰트를 올려 주세요")
        mime = "font/otf" if ext == "otf" else "font/ttf"
    else:
        if ext not in ("png", "jpg", "jpeg", "webp"):
            raise HTTPException(400, "기준 이미지는 PNG / JPG / WEBP 만 올릴 수 있습니다")
        try:
            im = Image.open(io.BytesIO(data))
            im.verify()
            im = Image.open(io.BytesIO(data))
            width, height = im.size
        except Exception:
            raise HTTPException(400, "이미지 파일을 읽을 수 없습니다")
        mime = {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")

    key = storage.put(f"{kind}_{uuid.uuid4().hex[:10]}.{ext}", data, mime)
    a = Asset(brand_id=brand_id, kind={"tone_ref": "reference"}.get(kind, kind),
              file_path=key, mime=mime, bytes=len(data), width=width, height=height)
    db.add(a)
    db.commit()
    return {"id": a.id, "kind": a.kind, "mime": mime, "bytes": a.bytes,
            "width": width, "height": height, "name": file.filename, "note": note}


@router.get("/assets/{asset_id}/file")
def asset_file(asset_id: int, db: Session = Depends(get_db)):
    a = db.get(Asset, asset_id)
    if not a or not storage.exists(a.file_path):
        raise HTTPException(404, "파일을 찾을 수 없습니다")
    return storage.response(a.file_path, a.mime)
