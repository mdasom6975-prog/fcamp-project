"""정사각 로고 칸 업로드(square=true): 1:1 이 아닌 로고는 늘리거나 자르지 않고 투명 여백을 넣어 정사각으로 저장한다."""
import io
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

WIDE_SVG = ('<svg width="120" height="19" viewBox="0 0 120 19" fill="none" xmlns="http://www.w3.org/2000/svg">'
            '<rect x="0" y="0" width="120" height="19" stroke-width="2" fill="#FC1C49"/></svg>')


@pytest.fixture
def client(tmp_path):
    from app import storage
    from app.db import Base
    from app.main import app
    from app.routers_assets import get_db

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app), storage
    finally:
        app.dependency_overrides.clear()


def _png(w: int, h: int) -> bytes:
    im = Image.new("RGBA", (w, h), (252, 28, 73, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _post(c, name: str, data: bytes, square: bool = True):
    form = {"kind": "logo", **({"square": "true"} if square else {})}
    return c.post("/api/assets", data=form, files={"file": (name, data)})


def _file(c, asset_id: int) -> bytes:
    return c.get(f"/api/assets/{asset_id}/file").content


def test_wide_png_is_padded_to_square(client):
    c, _ = client
    r = _post(c, "logo.png", _png(400, 100)).json()
    side = round(400 * 1.2)
    assert (r["width"], r["height"]) == (side, side) and "정사각으로 만들었습니다" in r["note"]
    assert "작게 보일 수" in r["note"]                 # 4:1 → 길쭉한 로고 경고
    im = Image.open(io.BytesIO(_file(c, r["id"])))
    assert im.size == (side, side)
    assert im.getpixel((0, 0))[3] == 0                   # 여백은 투명
    assert im.getpixel((side // 2, side // 2)) == (252, 28, 73, 255)   # 원본은 가운데에 그대로
    # 원본 로고 크기는 그대로(늘리지 않음): 빨간 픽셀 영역이 400×100
    assert im.getchannel("A").getbbox() == ((side - 400) // 2, (side - 100) // 2, (side - 400) // 2 + 400, (side - 100) // 2 + 100)


def test_wide_svg_viewbox_is_widened(client):
    c, _ = client
    r = _post(c, "logo.svg", WIDE_SVG.encode()).json()
    assert (r["width"], r["height"]) == (1200, 1200) and "120×19" in r["note"]
    svg = _file(c, r["id"]).decode()
    root = re.search(r"<svg\b[^>]*>", svg).group(0)
    assert 'viewBox="-12 -62.5 144 144"' in root and 'width="1200"' in root and 'height="1200"' in root
    assert root.count("viewBox") == 1 and 'width="120"' not in root
    assert '<rect x="0" y="0" width="120" height="19" stroke-width="2"' in svg     # 도형은 그대로


def test_square_and_regular_upload_unchanged(client):
    c, _ = client
    data = _png(300, 300)
    r = _post(c, "sq.png", data).json()
    assert (r["width"], r["height"], r["note"]) == (300, 300, None) and _file(c, r["id"]) == data
    r = _post(c, "wide.png", _png(400, 100), square=False).json()      # 기본 로고 칸은 변형하지 않는다
    assert (r["width"], r["height"], r["note"]) == (400, 100, None)


@pytest.mark.parametrize("size,msg", [((100, 100), "128×128 이상"), ((90, 20), "너무 작아")])
def test_too_small_png_is_rejected(client, size, msg):
    c, _ = client
    r = _post(c, "small.png", _png(*size))
    assert r.status_code == 400 and msg in r.json()["detail"]


def test_svg_without_size_is_rejected(client):
    c, _ = client
    r = _post(c, "nosize.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>')
    assert r.status_code == 400 and "크기" in r.json()["detail"]
