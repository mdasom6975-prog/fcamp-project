"""brands/*/brand.json 의 브랜드와 demo/campaigns/*.json 의 캠페인(있을 때만)을 실행 중인 앱에 API 로 넣는다.

실행: backend/.venv/bin/python tools/seed_demo.py [--base http://127.0.0.1:8000] [--brands a,b] [--campaigns x,y]
      [--category 금융·핀테크] [--svg-logo] [--list] [--dry-run]

- 같은 이름의 브랜드, 같은 브랜드의 같은 상품명 캠페인이 이미 있으면 새로 만들지 않는다 (여러 번 실행해도 안전).
- 카피·이미지 생성(과금)은 하지 않는다. 캠페인은 '규격 선택 + 입력 저장' 상태로 만들어지므로 화면에서 '시안 생성하기'부터 누른다.
- 브랜드: 로고(logo.png, 없으면 로고 없이)와 정사각 로고(logo_square.png)를 올린다. 쿠챠처럼 로고 파일이 없는 브랜드는 로고 없이 등록한다.
- 캠페인 JSON 은 기본으로 두지 않는다(demo/ 폴더가 없으면 브랜드만 등록). 만들 때 brand 는 brands/ 아래 폴더 이름이다.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANDS_DIR = ROOT / "brands"
CAMPAIGNS_DIR = ROOT / "demo" / "campaigns"


def load_brands(only: set[str] | None = None, category: str | None = None) -> dict[str, dict]:
    out = {}
    for d in sorted(BRANDS_DIR.iterdir()):
        f = d / "brand.json"
        if not (d.is_dir() and f.exists()) or (only and d.name not in only):
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        if category and doc.get("meta", {}).get("category") != category:
            continue
        out[d.name] = doc["brand"]
    return out


def load_campaigns(only: set[str] | None = None, brands: set[str] | None = None) -> list[dict]:
    out = []
    for f in sorted(CAMPAIGNS_DIR.glob("*.json")):  # 폴더가 없으면 빈 목록
        c = json.loads(f.read_text(encoding="utf-8"))
        if (not only or c["slug"] in only) and (not brands or c["brand"] in brands):
            out.append(c)
    return out


def _upload_logo(client, path: Path) -> int:
    mime = "image/svg+xml" if path.suffix == ".svg" else "image/png"
    with path.open("rb") as fh:
        r = client.post("/api/assets", data={"kind": "logo"}, files={"file": (path.name, fh, mime)})
    r.raise_for_status()
    return r.json()["id"]


def seed(client, brand_slugs: set[str] | None = None, campaign_slugs: set[str] | None = None,
         category: str | None = None, svg_logo: bool = False, log=print) -> dict:
    """client 는 httpx.Client 또는 FastAPI TestClient. 반환: {brand slug: id, campaign slug: id}"""
    made: dict[str, int] = {}
    existing = {b["name"]: b["id"] for b in client.get("/api/brands").json()}
    brands = load_brands(brand_slugs, category)
    for slug, body in brands.items():
        if body["name"] in existing:
            made[slug] = existing[body["name"]]
            log(f"[브랜드] {body['name']} 이미 있음 → id {made[slug]}")
            continue
        bdir = BRANDS_DIR / slug
        logo = bdir / ("logo.svg" if svg_logo and (bdir / "logo.svg").exists() else "logo.png")
        body = dict(body)
        if logo.exists():
            body["logo_asset_id"] = _upload_logo(client, logo)
        if (bdir / "logo_square.png").exists():
            body["logo_square_asset_id"] = _upload_logo(client, bdir / "logo_square.png")
        r = client.post("/api/brands", json=body)
        if r.status_code >= 400:
            log(f"[브랜드] {body['name']} 실패: {r.text}")
            continue
        made[slug] = r.json()["id"]
        warns = r.json().get("warnings") or []
        log(f"[브랜드] {body['name']} 생성 → id {made[slug]} (로고 {logo.name if logo.exists() else '없음'})"
            + (f" ⚠ {warns}" if warns else ""))

    presets = client.get("/api/presets").json()
    by_name = {p["name"]: p["id"] for plat in presets for a in plat["ad_types"] for p in a["presets"]}
    have = {(d["brand"]["id"], d["brief"]["product_name"]): d["id"] for d in client.get("/api/designs").json()}
    for c in load_campaigns(campaign_slugs, set(brands) if category else brand_slugs):
        bslug = c["brand"]
        if bslug not in made:
            name = load_brands({bslug}).get(bslug, {}).get("name")
            if name in existing:
                made[bslug] = existing[name]
            else:
                log(f"[캠페인] {c['slug']}: 브랜드 {bslug} 가 없어 건너뜀 (--brands 에 포함하세요)")
                continue
        key = (made[bslug], c["brief"]["product_name"])
        if key in have:
            made[c["slug"]] = have[key]
            log(f"[캠페인] {c['brief']['product_name']} ({c['slug']}) 이미 있음 → id {have[key]}")
            continue
        ids, missing = [], []
        for n in c["placements"]:
            (ids if n in by_name else missing).append(by_name.get(n, n))
        if missing:
            log(f"[캠페인] {c['slug']}: 규격 이름을 찾지 못함 {missing} (규격 라이브러리 이름과 맞추세요)")
        brief = dict(c["brief"])
        pimg = c.get("product_image")
        if pimg and (BRANDS_DIR / bslug / pimg).exists():
            with (BRANDS_DIR / bslug / pimg).open("rb") as fh:
                r = client.post("/api/assets", data={"kind": "product", "brand_id": str(made[bslug])},
                                files={"file": (pimg, fh, "image/png")})
            if r.status_code < 400:
                brief["product_image_asset_id"] = r.json()["id"]
        r = client.post("/api/designs", json={"brand_id": made[bslug], "kind": "광고 이미지", "brief": brief,
                                              "preset_version_ids": ids})
        if r.status_code >= 400:
            log(f"[캠페인] {c['slug']} 실패: {r.text}")
            continue
        made[c["slug"]] = r.json()["id"]
        log(f"[캠페인] {brief['product_name']} ({c['slug']}) → id {made[c['slug']]}, 규격 {len(ids)}개")
    return made


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--brands", help="쉼표로 구분한 브랜드 폴더 이름 (기본: 전부)")
    ap.add_argument("--campaigns", help="쉼표로 구분한 캠페인 slug (기본: 전부)")
    ap.add_argument("--category", help="meta.category 가 같은 브랜드만 (예: 금융·핀테크)")
    ap.add_argument("--svg-logo", action="store_true", help="logo.svg 가 있는 브랜드는 SVG 로 올린다")
    ap.add_argument("--list", action="store_true", help="카테고리별 브랜드와 캠페인 수를 보여 주고 끝낸다")
    ap.add_argument("--dry-run", action="store_true", help="서버에 보내지 않고 무엇을 만들지만 출력")
    a = ap.parse_args()
    bs = set(a.brands.split(",")) if a.brands else None
    cs = set(a.campaigns.split(",")) if a.campaigns else None
    if a.list or a.dry_run:
        brands = load_brands(bs, a.category)
        camps = load_campaigns(cs, set(brands) if (a.category or bs) else None)
        cnt = {}
        for c in camps:
            cnt[c["brand"]] = cnt.get(c["brand"], 0) + 1
        for slug, b in brands.items():
            cat = json.loads((BRANDS_DIR / slug / "brand.json").read_text(encoding="utf-8")).get("meta", {}).get("category", "-")
            print(f"{slug:14} {b['name']:10} [{cat}] 캠페인 {cnt.get(slug, 0)}개 · 필수 {b['required_phrases']} · 금지 {b['forbidden_phrases']}")
        if a.dry_run:
            for c in camps:
                print(f"  캠페인 {c['slug']:24} ← {c['brand']} · 규격 {len(c['placements'])}개 · 배경 {c['brief'].get('image_style')}")
        print(f"브랜드 {len(brands)}개 · 캠페인 {len(camps)}개")
        return 0
    import httpx
    with httpx.Client(base_url=a.base, timeout=60.0) as client:
        try:
            client.get("/api/brands").raise_for_status()
        except Exception as e:
            print(f"서버에 연결하지 못했습니다 ({a.base}): {e}", file=sys.stderr)
            return 1
        seed(client, bs, cs, a.category, a.svg_logo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
