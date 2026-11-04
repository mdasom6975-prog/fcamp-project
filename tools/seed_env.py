"""환경 하나(dev 또는 prod)를 같은 입력으로 채운다: 브랜드(brands/*/brand.json) → 지식(brands/*/sources.json).

실행 (저장소 루트에서):
    backend/.venv/bin/python tools/seed_env.py                                    # dev: 로컬에서 띄운 앱(8000) + backend/.env 의 DB
    backend/.venv/bin/python tools/seed_env.py --base https://demo:<APP_PASSWORD>@<프로젝트>.vercel.app   # prod: DATABASE_URL 등을 prod 값으로 바꿔서
    backend/.venv/bin/python tools/seed_env.py --no-knowledge                     # 브랜드만

- 브랜드·캠페인은 앱 API 로(tools/seed_demo.py, 재실행 안전·과금 없음), 지식은 DB 에 직접(tools/register_knowledge.py, 임베딩 과금).
  두 단계가 같은 DB 를 봐야 하므로 --base 의 앱이 쓰는 DB 와 현재 DATABASE_URL 이 같은 프로젝트여야 한다.
- 지식은 raw/<id>.txt 가 있는 출처만 등록된다(없으면 건너뛰고 알린다). 같은 출처를 다시 올리면 이전 문서는 '보관'.
- 생성물(카피·이미지)은 채우지 않는다. 그것까지 같게 하려면 tools/sync_env.py 로 prod → dev 복제.
"""
import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="실행 중인 앱 주소 (비밀번호가 있으면 https://demo:<pw>@호스트)")
    ap.add_argument("--no-knowledge", action="store_true", help="지식 등록(임베딩 과금)은 건너뛴다")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    import httpx
    seed_demo = _load("seed_demo")
    with httpx.Client(base_url=a.base, timeout=120.0) as client:
        try:
            client.get("/api/health").raise_for_status()
        except httpx.HTTPError as e:
            print(f"앱에 연결할 수 없습니다({a.base}): {type(e).__name__}. 앱을 먼저 띄우거나 --base 를 확인하세요", file=sys.stderr)
            return 2
        if a.dry_run:
            print("브랜드:", ", ".join(seed_demo.load_brands()))
        else:
            seed_demo.seed(client, log=print)
    if a.no_knowledge:
        return 0
    rc = 0
    for src in sorted(ROOT.glob("brands/*/sources.json")):
        print(f"\n== 지식 {src.relative_to(ROOT)}")
        cmd = [sys.executable, str(ROOT / "tools" / "register_knowledge.py"), str(src)] + (["--dry-run"] if a.dry_run else [])
        rc = subprocess.run(cmd, cwd=ROOT).returncode or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
