"""최종 결과 세트(results/)를 만든다 (워크북 Day 10: 브랜드 1 · 아이콘 10 · 지면 6 + 평가 보고서, docs/05 '최종 결과 세트').

앱에서 만든 '검수 완료' 내보내기 ZIP 과 브랜드 폴더, 평가 보고서를 한 폴더로 모은다. 앱 DB·API 는 바꾸지 않는다.

실행 (저장소 루트에서):
    backend/.venv/bin/python tools/build_results.py --zip 검수완료_쿠챠_20.zip --brand brands/kucha --report eval/reports/20261023_1530
    backend/.venv/bin/python tools/build_results.py --export-id 2 --brand brands/kucha --report <보고서 폴더>   # 실행 중인 앱(:8000)에서 ZIP 을 받아 온다

결과:
    results/brand/     brand.json · 로고 원본 · logo.sha256.txt
    results/icons/     png(256) · src(1024 투명 원본)
    results/designs/   지면별 폴더 (image.png · copy.txt …)
    results/copy.csv · 검수결과.csv · manifest.json
    results/report/    report.html · results.json · summary.csv
manifest.json 의 sha256 은 모은 뒤 실제 파일과 다시 대조한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOT_FILES = ("copy.csv", "검수결과.csv", "manifest.json")


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def assemble(zip_path: Path, brand_dir: Path, report_dir: Path | None, out: Path) -> dict:
    """ZIP·브랜드·보고서를 out 아래 결과 세트 구조로 모은다. 반환: 개수와 manifest 대조 결과."""
    if out.exists():
        raise FileExistsError(f"이미 있습니다. 직접 지우거나 다른 폴더를 지정하세요: {out}")
    (out / "designs").mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            parts = Path(info.filename).parts
            if parts[0] == "icons" or (len(parts) == 1 and parts[0] in ROOT_FILES):
                dest = out / info.filename
            else:
                dest = out / "designs" / info.filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(info))

    brand_out = out / "brand"
    brand_out.mkdir()
    for p in sorted(brand_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in (".json", ".svg", ".png"):
            shutil.copy2(p, brand_out / p.name)
    logos = [p for p in brand_out.iterdir() if p.suffix.lower() in (".svg", ".png")]
    (brand_out / "logo.sha256.txt").write_text(
        "".join(f"{_sha256(p)}  {p.name}\n" for p in sorted(logos)), encoding="utf-8")

    if report_dir:
        shutil.copytree(report_dir, out / "report")

    # manifest 의 파일 목록·sha256 이 모은 파일과 같은지 확인한다 (ZIP 안의 경로 → 결과 세트 안의 경로)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    mismatched, missing = [], []
    for f in manifest.get("files", []):
        rel = Path(f["path"])
        p = out / rel if rel.parts[0] == "icons" or len(rel.parts) == 1 else out / "designs" / rel
        if not p.exists():
            missing.append(f["path"])
        elif _sha256(p) != f["sha256"]:
            mismatched.append(f["path"])
    files = [p for p in out.rglob("*") if p.is_file()]
    return {"files": len(files), "icons": len(list((out / "icons" / "png").glob("*.png"))),
            "presets": len([d for d in (out / "designs").iterdir() if d.is_dir()]),
            "manifest_files": len(manifest.get("files", [])), "missing": missing, "mismatched": mismatched}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="최종 결과 세트(results/) 만들기")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--zip", help="앱에서 받은 '검수 완료' ZIP")
    src.add_argument("--export-id", type=int, help="실행 중인 앱의 내보내기 id (ZIP 을 받아 온다)")
    ap.add_argument("--api", default="http://127.0.0.1:8000", help="앱 주소 (--export-id 용)")
    ap.add_argument("--brand", default=str(ROOT / "brands" / "kucha"), help="브랜드 폴더 (brand.json·로고)")
    ap.add_argument("--report", help="평가 보고서 폴더 (eval/run_eval.py 출력)")
    ap.add_argument("--out", default=str(ROOT / "results"), help="출력 폴더 (기본 results/)")
    a = ap.parse_args(argv)

    zip_path = Path(a.zip) if a.zip else None
    if a.export_id:
        zip_path = Path(a.out + ".download.zip")
        with urllib.request.urlopen(f"{a.api}/api/exports/{a.export_id}/download", timeout=60) as r:
            zip_path.write_bytes(r.read())
    try:
        res = assemble(zip_path, Path(a.brand), Path(a.report) if a.report else None, Path(a.out))
    finally:
        if a.export_id and zip_path and zip_path.exists():
            zip_path.unlink()
    print(f"결과 세트: {a.out} — 파일 {res['files']}개 · 아이콘 {res['icons']} · 지면 {res['presets']} · manifest {res['manifest_files']}건")
    if res["missing"] or res["mismatched"]:
        print(f"manifest 불일치: 없음 {res['missing']} · 해시 다름 {res['mismatched']}", file=sys.stderr)
        return 1
    print("manifest 의 파일 목록·sha256 이 실제 파일과 일치합니다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
