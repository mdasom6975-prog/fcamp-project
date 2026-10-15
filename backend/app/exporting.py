"""파일 인코딩·용량 판정에 쓰는 공용 함수 (검수 6-6, 내보내기 6-7 에서 같이 사용)."""
import io
import re

from PIL import Image

_SIZE = re.compile(r"([\d][\d,\.]*)\s*(KB|MB)", re.I)
_DIM = re.compile(r"(\d+)\s*[x×]\s*(\d+)", re.I)
_PX = re.compile(r"(\d+)\s*px", re.I)


def parse_bytes(text: str | None) -> int | None:
    m = _SIZE.search(text or "")
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return int(n * (1024 if m.group(2).upper() == "KB" else 1024 * 1024))


def parse_dim(text: str | None) -> tuple[int, int] | None:
    m = _DIM.search(text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_px(text: str | None) -> int | None:
    m = _PX.search(text or "")
    return int(m.group(1)) if m else None


def parse_formats(text: str | None) -> list[str]:
    t = (text or "").upper()
    out = []
    if "PNG" in t:
        out.append("PNG")
    if "JPG" in t or "JPEG" in t:
        out.append("JPG")
    return out


def cond(conditions: list[dict], *names: str) -> dict | None:
    return next((c for c in conditions if c["name"] in names), None)


def encode(img: Image.Image, formats: list[str], max_bytes: int | None) -> dict:
    """허용 형식 순서(PNG 우선)로 인코딩해 용량 한도에 맞는 첫 결과를 돌려준다.
    PNG 가 한도를 넘고 JPG 가 허용되면 품질을 낮춰 가며 맞춘다."""
    tried = []
    fmts = formats or ["PNG"]
    for f in fmts:
        buf = io.BytesIO()
        if f == "PNG":
            img.convert("RGB").save(buf, "PNG", optimize=True)
            data = buf.getvalue()
            tried.append(("PNG", len(data)))
            if max_bytes is None or len(data) <= max_bytes:
                return {"ok": True, "format": "PNG", "bytes": data, "size": len(data), "tried": tried, "quality": None}
        else:
            for q in (95, 90, 85, 80, 70, 60):
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=q, optimize=True)
                data = buf.getvalue()
                if max_bytes is None or len(data) <= max_bytes:
                    tried.append(("JPG", len(data)))
                    return {"ok": True, "format": "JPG", "bytes": data, "size": len(data), "tried": tried, "quality": q}
            tried.append(("JPG", len(data)))
    last = tried[-1]
    return {"ok": False, "format": last[0], "bytes": None, "size": last[1], "tried": tried, "quality": None}
