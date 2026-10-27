"""브랜드 허용 색상을 배경 프롬프트에 넣으면 어떻게 달라지는지 비교한다. 같은 모델·품질·비율로 프롬프트만 바꿔 생성한다.
호출당 과금(low 약 14원). 운영 프롬프트(prompts/image/background.md)는 바꾸지 않는다.
사용: .venv/bin/python scripts/compare_brand_colors.py [design_id] [quality] [변형 ...]   (결과: storage/compare/brand_colors/index.html)
예) scripts/compare_brand_colors.py 29 low C   → C 만 새로 생성하고 A·B 는 이미 저장된 이미지를 다시 쓴다(없으면 생성)

A 는 imagegen.build_prompt 그대로(운영), B 는 A 에 '색감을 브랜드 색 계열로 맞춘다'는 지시를 덧붙인다.
C 는 강조색을 일부(포인트)에만 쓰고 큰 면은 밝은 중성색으로 두게 한다(B 가 화면 전체를 브랜드 색으로 덮던 문제).
D 는 C 를 채택해 반영한 운영 프롬프트(background 1.8 + _brand_colors: 큰 면은 브랜드 배경색) 그대로다. A 는 colors 를 넘기지 않아 1.7 과 같다.
색 일치는 분석 화면과 같은 기준(analysis.palette·color_match: 주조·보조·강조색과 ΔE ≤ 20 인 면적)으로 재고,
밝은 중성색 면적(L ≥ 75, 채도 ≤ 15)도 함께 잰다. 문구·CTA 가 브랜드 색이라 배경이 밝고 중립적일수록 잘 읽힌다."""
import html
import math
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import analysis, compose, icons, imagegen  # noqa: E402
from app.db import SessionLocal, STORAGE_DIR  # noqa: E402
from app.models import Brand, BrandVersion, Design, PresetVersion  # noqa: E402

OUT = STORAGE_DIR / "compare" / "brand_colors"


def color_line(colors: list[dict]) -> str:
    """B 에 덧붙이는 문장. 아이콘처럼 '이 색만'으로 묶지 않고 주요 색감만 브랜드 색 계열로 맞추게 한다."""
    cols = [f"{c['hex']}({c['role']})" for role in icons.PALETTE_ROLES for c in colors if c.get("role") == role and c.get("hex")]
    return (f"[브랜드 색] 화면의 주요 색감은 {', '.join(cols)} 계열로 맞추고, 그 밖의 색은 무채색과 같은 색의 명암 위주로 쓴다. "
            "색은 사물·조명·배경의 색으로 자연스럽게 드러나게 하고, 색 견본이나 색 띠처럼 그리지 않는다.")


def accent_line(colors: list[dict]) -> str:
    """C 에 덧붙이는 문장. 강조색(없으면 주조색)을 포인트로만, 큰 면은 밝은 중성색으로."""
    by_role = {c.get("role"): c["hex"] for c in colors if c.get("hex")}
    accent = by_role.get("강조색") or by_role.get("주조색")
    sub = [f"{by_role[r]}({r})" for r in ("주조색", "보조색") if r in by_role and by_role[r] != accent]
    return (f"[브랜드 색] 강조색 {accent} 는 화면 면적의 15% 이내로 작은 소품 하나, 그래픽 일부, 포인트 조명 같은 곳에만 쓴다. "
            + (f"{', '.join(sub)} 는 쓰더라도 아주 작게 쓴다. " if sub else "")
            + "배경 벽·바닥·큰 면 등 화면의 대부분은 흰색, 밝은 회색, 연한 크림 같은 밝은 중성색으로 둔다. "
            "색은 사물·조명·배경의 색으로 자연스럽게 드러나게 하고, 색 견본이나 색 띠처럼 그리지 않는다.")


def neutral_share(pal: list[dict]) -> float:
    """밝은 중성색(L ≥ 75, 채도 ≤ 15) 팔레트 색들의 비율 합."""
    def ok(h: str) -> bool:
        L, a, b = analysis.rgb_to_lab(analysis.hex_to_rgb(h))
        return L >= 75 and math.hypot(a, b) <= 15
    return round(sum(p["share"] for p in pal if ok(p["hex"])), 3)


def main() -> None:
    did = int(sys.argv[1]) if len(sys.argv) > 1 else 29
    quality = sys.argv[2] if len(sys.argv) > 2 else "low"
    only = set(sys.argv[3:])      # 비어 있으면 모두 새로 생성
    db = SessionLocal()
    d = db.get(Design, did)
    pv = next(p for p in (db.get(PresetVersion, i) for i in d.preset_version_ids) if p.production_width)
    tpl = compose.template_for(pv.production_width, pv.production_height)
    bv, brand = db.get(BrandVersion, d.brand_version_id), db.get(Brand, d.brand_id)
    style, _ = imagegen.pick_style(bv.image_tone or {}, d.brief or {})
    model = imagegen.settings_for(d.brief)["model"]
    gw, gh = imagegen.size_for(tpl)
    base = imagegen.build_prompt(brand.name, bv.image_tone or {}, d.brief, tpl, style)
    variants = [("A", "현재(색 지시 없음)", base), ("B", "브랜드 색 추가", base + "\n" + color_line(bv.colors or [])),
                ("C", "강조색 포인트 + 밝은 중성색", base + "\n" + accent_line(bv.colors or [])),
                ("D", "운영(background 1.8)", imagegen.build_prompt(brand.name, bv.image_tone or {}, d.brief, tpl, style,
                                                                  colors=bv.colors))]
    OUT.mkdir(parents=True, exist_ok=True)
    swatches = "".join(f"<span class=sw style='background:{c['hex']}'></span>{html.escape(c.get('role') or '')} {c['hex']} "
                       for c in bv.colors or [] if c.get("hex"))
    cards = []
    for slug, name, prompt in variants:
        fn = f"{did}_{slug}_{quality}.png"
        if only and slug not in only and (OUT / fn).exists():
            im = Image.open(OUT / fn).convert("RGB")
        else:
            im = imagegen.generate_background(prompt, gw, gh, quality, model=model)
            im.save(OUT / fn)
        pal = analysis.palette(im)
        m = analysis.color_match(pal, bv.colors or [])
        bar = "".join(f"<span style='flex:{p['share']};background:{p['hex']}' title='{p['hex']} {p['share']:.0%}'></span>" for p in pal)
        rows = "".join(f"<tr><td><span class=sw style='background:{c['hex']}'></span>{html.escape(c['role'])} {c['hex']}</td>"
                       f"<td><span class=sw style='background:{c['nearest']}'></span>{c['nearest']}</td>"
                       f"<td>{c['delta_e']}</td><td>{c['share']:.0%}</td></tr>" for c in m["per_color"])
        neutral = neutral_share(pal)
        cards.append(f"<div class=c><h3>{slug} {html.escape(name)}</h3><img src='{fn}'><div class=bar>{bar}</div>"
                     f"<p>주조·보조·강조색과 가까운 면적(ΔE ≤ {m['near_de']}): <b>{(m['coverage'] or 0):.0%}</b><br>"
                     f"밝은 중성색 면적(L ≥ 75, 채도 ≤ 15): <b>{neutral:.0%}</b></p>"
                     f"<table><tr><th>브랜드 색</th><th>가장 가까운 색</th><th>ΔE</th><th>비율</th></tr>{rows}</table>"
                     f"<details><summary>프롬프트</summary><pre>{html.escape(prompt)}</pre></details></div>")
        print("ok", slug, name, "coverage", m["coverage"], "neutral", neutral)
    (OUT / "index.html").write_text(
        "<meta charset=utf-8><title>브랜드 색 비교</title><style>body{font-family:sans-serif}.c{display:inline-block;"
        "vertical-align:top;width:23%;margin:1%}img{width:100%}.bar{display:flex;height:18px;margin:6px 0}"
        ".sw{display:inline-block;width:12px;height:12px;border:1px solid #ccc;margin:0 4px;vertical-align:middle}"
        "td,th{padding:2px 8px;text-align:left;font-size:13px}pre{white-space:pre-wrap;font-size:12px}</style>"
        f"<h2>시안 {did} · {html.escape(brand.name)} · {tpl} {gw}×{gh} · {html.escape(model)} {quality} · 형태 {html.escape(style)}</h2>"
        f"<p>허용 색상: {swatches}</p>" + "".join(cards), encoding="utf-8")


if __name__ == "__main__":
    main()
