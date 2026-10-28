"""brands/<slug>/brand.json 의 브랜드명과 주조색으로 시연용 대체 로고(워드마크)를 만든다.

실행: backend/.venv/bin/python tools/make_logos.py [slug ...]
- logo.png: 가로형 투명 PNG(글자만, 주조색). logo_square.png: 512×512 둥근 사각 타일 + 글자.
- 실제 상표 로고가 아니다. 공식 로고 파일이 있으면 같은 이름으로 덮어쓰면 된다.
- logo.svg 가 이미 있는 브랜드(쿠챠)는 건너뛴다.
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "backend" / "fonts" / "Pretendard-Bold.otf"
# 긴 브랜드명은 로고에 쓸 짧은 이름으로
SHORT = {"samsung": "GALAXY", "starbucks": "스타벅스", "kbbank": "KB국민은행", "notion": "노션"}


def _lum(hex_: str) -> float:
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _fit(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int) -> ImageFont.FreeTypeFont:
    size = max_h
    while size > 8:
        f = ImageFont.truetype(str(FONT), size)
        l, t, r, b = draw.textbbox((0, 0), text, font=f)
        if r - l <= max_w and b - t <= max_h:
            return f
        size -= 2
    return ImageFont.truetype(str(FONT), 8)


def _draw_center(img: Image.Image, text: str, fill, box: tuple[int, int, int, int]) -> None:
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    f = _fit(d, text, x1 - x0, y1 - y0)
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    d.text((x0 + (x1 - x0 - (r - l)) / 2 - l, y0 + (y1 - y0 - (b - t)) / 2 - t), text, font=f, fill=fill)


def make(slug: str) -> bool:
    d = ROOT / "brands" / slug
    doc = json.loads((d / "brand.json").read_text(encoding="utf-8"))["brand"]
    primary = next(c["hex"] for c in doc["colors"] if c["role"] == "주조색")
    text = SHORT.get(slug, doc["name"])

    # 가로형: 글자만 주조색. 주조색이 어두운 브랜드도 그대로 둔다(밝은 배경 위 사용 전제)
    wide = Image.new("RGBA", (960, 280), (0, 0, 0, 0))
    _draw_center(wide, text, "#191919" if _lum(primary) > 190 else primary, (40, 40, 920, 240))  # 노랑 계열은 흰 배경에서 안 보여 어두운 글자로
    wide.save(d / "logo.png")

    # 정사각: 주조색 타일 위에 대비되는 글자색
    ink = "#111111" if _lum(primary) > 150 else "#FFFFFF"
    sq = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    ImageDraw.Draw(sq).rounded_rectangle((0, 0, 511, 511), radius=112, fill=primary)
    _draw_center(sq, text, ink, (56, 150, 456, 362))
    sq.save(d / "logo_square.png")
    return True


def main() -> None:
    only = set(sys.argv[1:])
    n = 0
    for d in sorted((ROOT / "brands").iterdir()):
        if not (d / "brand.json").exists() or (only and d.name not in only) or (d / "logo.svg").exists():
            continue
        n += make(d.name)
    print(f"{n}개 브랜드의 로고를 만들었습니다")


if __name__ == "__main__":
    main()
