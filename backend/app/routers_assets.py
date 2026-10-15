import io
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fontTools.ttLib import TTFont
from PIL import Image
from sqlalchemy.orm import Session

from .db import BASE_DIR, STORAGE_DIR, SessionLocal
from .models import Asset

router = APIRouter(prefix="/api")
FONT_DIR = BASE_DIR / "fonts"
MAX_BYTES = 10 * 1024 * 1024

PROVIDED_FONTS = [
    {"name": "Pretendard-Regular", "label": "Pretendard Regular", "file": "Pretendard-Regular.otf"},
    {"name": "Pretendard-Bold", "label": "Pretendard Bold", "file": "Pretendard-Bold.otf"},
]


def get_db():
    with SessionLocal() as db:
        yield db


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


@router.get("/fonts/provided")
def provided_fonts():
    return PROVIDED_FONTS


@router.post("/assets")
async def upload_asset(kind: str = Form(...), brand_id: int | None = Form(None),
                       file: UploadFile = File(...), db: Session = Depends(get_db)):
    """kind: logo | font | tone_ref"""
    if kind not in ("logo", "font", "tone_ref"):
        raise HTTPException(400, "kind 는 logo / font / tone_ref")
    data = await file.read()
    if not data:
        raise HTTPException(400, "빈 파일입니다")
    if len(data) > MAX_BYTES:
        raise HTTPException(400, "파일은 10MB 이하여야 합니다")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    width = height = None

    if kind == "logo":
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

    STORAGE_DIR.mkdir(exist_ok=True)
    path = STORAGE_DIR / f"{kind}_{uuid.uuid4().hex[:10]}.{ext}"
    path.write_bytes(data)
    a = Asset(brand_id=brand_id, kind={"tone_ref": "reference"}.get(kind, kind),
              file_path=str(path), mime=mime, bytes=len(data), width=width, height=height)
    db.add(a)
    db.commit()
    return {"id": a.id, "kind": a.kind, "mime": mime, "bytes": a.bytes,
            "width": width, "height": height, "name": file.filename}


@router.get("/assets/{asset_id}/file")
def asset_file(asset_id: int, db: Session = Depends(get_db)):
    a = db.get(Asset, asset_id)
    if not a:
        raise HTTPException(404, "파일을 찾을 수 없습니다")
    return FileResponse(a.file_path, media_type=a.mime)
