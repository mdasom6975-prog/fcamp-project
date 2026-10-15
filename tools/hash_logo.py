"""로고 원본의 해시와 비율을 기록한다 (워크북 Day 3: tools/hash_logo.py).

실행: backend/.venv/bin/python tools/hash_logo.py <로고 파일> [--write brands/<id>/brand.json]
PNG/SVG 모두 sha256 을 남기고, PNG 는 Pillow 로 크기·비율을 읽는다. SVG 는 viewBox/width/height 속성을 읽는다.
"""
import hashlib
import json
import re
import sys
from pathlib import Path


def logo_info(path: Path) -> dict:
    data = path.read_bytes()
    info = {"file": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if path.suffix.lower() == ".svg":
        text = data.decode("utf-8", "ignore")
        m = re.search(r'viewBox="\s*[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)', text)
        if m:
            w, h = float(m.group(1)), float(m.group(2))
        else:
            mw = re.search(r'\swidth="([\d.]+)', text)
            mh = re.search(r'\sheight="([\d.]+)', text)
            w, h = (float(mw.group(1)), float(mh.group(1))) if mw and mh else (None, None)
    else:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            info["mode"] = im.mode
    info["width"], info["height"] = w, h
    info["aspect"] = round(w / h, 4) if w and h else None
    return info


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    info = logo_info(Path(sys.argv[1]))
    print(json.dumps(info, ensure_ascii=False, indent=2))
    if "--write" in sys.argv:
        target = Path(sys.argv[sys.argv.index("--write") + 1])
        doc = json.loads(target.read_text(encoding="utf-8"))
        doc.setdefault("meta", {})["logo"] = info
        target.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"→ {target} 의 meta.logo 갱신")


if __name__ == "__main__":
    main()
