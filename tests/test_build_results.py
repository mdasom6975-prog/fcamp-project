"""tools/build_results.py — 내보내기 ZIP·브랜드·보고서를 결과 세트 구조로 모으고 manifest 해시를 다시 대조한다. 외부 API 없음."""
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("build_results", ROOT / "tools" / "build_results.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_results"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_zip(tmp_path, corrupt=False):
    files = {"Meta_피드/image.png": b"png-bytes", "Meta_피드/copy.txt": b"copy", "icons/png/01_a.png": b"i1",
             "icons/src/01_a.png": b"i1src", "copy.csv": b"a,b", "검수결과.csv": b"c,d"}
    manifest = {"files": [{"path": p, "sha256": hashlib.sha256(b).hexdigest()} for p, b in files.items()]}
    if corrupt:
        manifest["files"][0]["sha256"] = "0" * 64
    z = tmp_path / "e.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p, b in files.items():
            zf.writestr(p, b)
        zf.writestr("manifest.json", json.dumps(manifest))
    brand = tmp_path / "brand"
    brand.mkdir()
    (brand / "brand.json").write_text("{}", encoding="utf-8")
    (brand / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (brand / "QUESTIONS.md").write_text("x", encoding="utf-8")
    report = tmp_path / "rep"
    report.mkdir()
    (report / "report.html").write_text("<html/>", encoding="utf-8")
    return z, brand, report


def test_assemble_layout_and_manifest_match(tmp_path):
    mod = _load()
    z, brand, report = _make_zip(tmp_path)
    out = tmp_path / "results"
    res = mod.assemble(z, brand, report, out)
    assert res["missing"] == [] and res["mismatched"] == [] and res["icons"] == 1 and res["presets"] == 1
    assert (out / "designs" / "Meta_피드" / "image.png").exists() and (out / "icons" / "src" / "01_a.png").exists()
    assert (out / "copy.csv").exists() and (out / "manifest.json").exists() and (out / "report" / "report.html").exists()
    assert sorted(p.name for p in (out / "brand").iterdir()) == ["brand.json", "logo.sha256.txt", "logo.svg"]
    assert hashlib.sha256(b"<svg/>").hexdigest() in (out / "brand" / "logo.sha256.txt").read_text(encoding="utf-8")


def test_assemble_detects_hash_mismatch_and_refuses_existing_folder(tmp_path):
    import pytest
    mod = _load()
    z, brand, report = _make_zip(tmp_path, corrupt=True)
    out = tmp_path / "results"
    assert len(mod.assemble(z, brand, report, out)["mismatched"]) == 1
    with pytest.raises(FileExistsError):
        mod.assemble(z, brand, report, out)
