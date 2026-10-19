"""3D 아이콘 모델·품질 비교: 같은 렌더 프롬프트(prompts/icons/render3d + _3d_style)로 투명 PNG 를 1장씩 만들어
한 페이지(체커보드 배경)에 모은다. DB 없이 brands/<id>/brand.json 과 고정 브리프로 동작한다.
사용: .venv/bin/python scripts/compare_icon_models.py [kucha] [이름 일부 ...]   (결과: storage/compare/icons/index.html)
예) scripts/compare_icon_models.py kucha 1.5   → 이름에 "1.5" 가 들어간 항목만
호출마다 실제 과금이 발생한다(항목당 1장: 1.5 low 약 13원, 1.5 medium 약 50원, gpt-image-2 medium 약 77원).
10/19 요건 변경(2D 도형 → 3D 아이콘)에 맞춰 추가. 운영 기본값은 icons.MODEL_3D / QUALITY_3D.
10/19 결과: 1.5 low·medium 모두 투명 배경 OK(원본 약 1.5MB), medium 채택. gpt-image-2 는 background=transparent 가
400 "Transparent background is not supported for this model" 로 거절됨 — 지원이 열리면 다시 시험한다."""
import html
import io
import json
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import icons, imagegen  # noqa: E402
from app.db import STORAGE_DIR  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = STORAGE_DIR / "compare" / "icons"
# 비교 대상 아이콘 1개 (쿠챠 기준 기능명·오브젝트 묘사)
NAME, CONCEPT = "보관함", "뚜껑이 살짝 열린 둥근 보관 상자 안에 캡슐 하나"
CANDIDATES = [  # (표시 이름, 모델, 품질)
    ("gpt-image-1.5 low", "gpt-image-1.5", "low"),
    ("gpt-image-1.5 medium", "gpt-image-1.5", "medium"),
    ("gpt-image-2 medium", "gpt-image-2", "medium"),
]


def main() -> None:
    brand_id = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1][0].isdigit() else "kucha"
    only = [x.lower() for x in sys.argv[2:]]
    brand = json.loads((ROOT / "brands" / brand_id / "brand.json").read_text(encoding="utf-8"))["brand"]
    st = brand.get("icon_style") or {}
    style = {"corner": st.get("corner") or "둥근", "color": next((c["hex"] for c in brand["colors"] if c["role"] == "주조색"), "#000000")}
    palette = [c for c in brand["colors"] if c["role"] in icons.PALETTE_ROLES]
    prompt = icons.render_prompt(NAME, CONCEPT, icons.style_vars(style, palette, brand.get("image_tone") or {}))
    OUT.mkdir(parents=True, exist_ok=True)
    cards = []
    for label, model, quality in CANDIDATES:
        if only and not any(o in label.lower() for o in only):
            continue
        slug = "".join(c if c.isalnum() else "_" for c in label)
        t0 = time.time()
        try:
            im = imagegen.generate_transparent(prompt, model, quality)
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            cards.append(f"<div class=c><h3>{html.escape(label)}</h3><p>실패: {html.escape(msg)}</p></div>")
            print("fail", label, msg)
            continue
        sec = time.time() - t0
        im.save(OUT / f"{slug}_src.png")
        fitted, info = icons.fit_alpha(im, 256, 256)
        fitted.save(OUT / f"{slug}.png")
        size_kb = len((OUT / f"{slug}_src.png").read_bytes()) // 1024
        cards.append(f"<div class=c><h3>{html.escape(label)}</h3><p>{im.width}×{im.height} · {sec:.0f}초 · 원본 {size_kb}KB · "
                     f"투명 배경 {'예' if info['transparent'] else '아니오'} · bbox {info['bbox']}</p>"
                     f"<div class=r><figure><img src='{slug}_src.png'><figcaption>원본</figcaption></figure>"
                     f"<figure><img src='{slug}.png' class=s><figcaption>256 (live 20/24)</figcaption></figure></div></div>")
        print("ok", label, f"{sec:.0f}s", "transparent" if info["transparent"] else "OPAQUE")
    (OUT / "index.html").write_text(
        "<meta charset=utf-8><title>3D 아이콘 모델 비교</title><style>body{font:14px sans-serif;margin:20px}"
        ".c{border:1px solid #ddd;border-radius:8px;padding:12px;margin:0 0 16px}.r{display:flex;gap:12px;align-items:flex-start}"
        "img{max-height:320px;border-radius:4px;background:repeating-conic-gradient(#eee 0 25%,#fff 0 50%) 0 0/16px 16px}"
        "img.s{width:256px;height:256px}figure{margin:0}figcaption{color:#666;font-size:12px}</style>"
        f"<h2>3D 아이콘 모델 비교 — {html.escape(NAME)}</h2>"
        f"<details><summary>프롬프트</summary><pre style='white-space:pre-wrap'>{html.escape(prompt)}</pre></details>" + "".join(cards),
        encoding="utf-8")
    print("->", OUT / "index.html")


if __name__ == "__main__":
    main()
