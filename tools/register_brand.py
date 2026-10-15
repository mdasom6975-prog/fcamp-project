"""brands/<id>/brand.json 을 실행 중인 앱에 등록한다 (Day 4: 브랜드 저장·불러오기, 브랜드 2개 이상).

실행: backend/.venv/bin/python tools/register_brand.py brands/kucha/brand.json [--logo 로고파일] [--base http://127.0.0.1:8000]
같은 이름의 브랜드가 이미 있으면 새 버전(PUT)으로 갱신한다.
"""
import json
import sys
from pathlib import Path

import httpx


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    base = args[args.index("--base") + 1] if "--base" in args else "http://127.0.0.1:8000"
    logo = Path(args[args.index("--logo") + 1]) if "--logo" in args else None
    doc = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    body = dict(doc["brand"])
    with httpx.Client(base_url=base, timeout=30) as c:
        if logo:
            r = c.post("/api/assets", data={"kind": "logo"}, files={"file": (logo.name, logo.read_bytes())})
            r.raise_for_status()
            body["logo_asset_id"] = r.json()["id"]
        existing = next((b for b in c.get("/api/brands").json() if b["name"] == body["name"]), None)
        if existing:
            r = c.put(f"/api/brands/{existing['id']}", json=body)
        else:
            r = c.post("/api/brands", json=body)
        if r.status_code >= 400:
            print("거절:", r.status_code, r.text)
            sys.exit(2)
        out = r.json()
        print(f"{'갱신' if existing else '등록'}: {out['name']} (id={out['id']}, 버전 {out['version']['version_no']})")
        for w in out.get("warnings", []):
            print("  경고:", w)


if __name__ == "__main__":
    main()
