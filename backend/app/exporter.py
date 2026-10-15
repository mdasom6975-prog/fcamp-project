"""규격별 파일과 카피를 ZIP 으로 만든다 (6-7). 목록은 실제로 쓴 파일에서 만들어 파일과 항상 일치한다."""
import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import copyfields, exporting
from .textcount import count_len


def safe(name: str, limit: int = 60) -> str:
    n = re.sub(r'[\\/*?"<>|\r\n\t]+', "_", name.replace(":", "-")).strip(" ._")
    return (n or "item")[:limit]


class Pack:
    def __init__(self):
        self.buf = io.BytesIO()
        self.zip = zipfile.ZipFile(self.buf, "w", zipfile.ZIP_DEFLATED)
        self.files: list[dict] = []

    def add(self, path: str, data: bytes, role: str, preset: str | None = None):
        self.zip.writestr(path, data)
        self.files.append({"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                           "role": role, "preset": preset})

    def finish(self, manifest: dict) -> bytes:
        manifest = {**manifest, "files": self.files}
        self.zip.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        self.zip.close()
        return self.buf.getvalue()


def ad_preset_folder(pack: Pack, pv, g: dict, fields: list[dict], rows_csv: list[list]) -> dict:
    """한 지면의 이미지·(분리 자산형이면 로고)·카피 파일을 쓴다."""
    folder = f"{safe(pv.placement.platform.name)}_{safe(pv.placement.name)}"
    img = Image.open(g["comp"].file_path)
    conds = pv.conditions or []
    fmt_c, size_c = exporting.cond(conds, "파일 형식"), exporting.cond(conds, "최대 용량")
    formats = exporting.parse_formats(fmt_c["value"]) if fmt_c and fmt_c.get("value") else []
    max_bytes = exporting.parse_bytes(size_c["value"]) if size_c and size_c.get("value") else None
    enc = exporting.encode(img, formats, max_bytes)
    # 한도를 맞추지 못해도 파일은 만든다(미검수 작업본). 검수 결과에 '수정 필요'로 남아 있다.
    if enc["bytes"] is None:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "PNG", optimize=True)
        enc = {**enc, "bytes": buf.getvalue(), "format": "PNG", "size": len(buf.getvalue())}
    ext = "jpg" if enc["format"] == "JPG" else "png"
    pack.add(f"{folder}/image.{ext}", enc["bytes"], "이미지", folder)
    if g["sep"] and g["logo_path"]:
        pack.add(f"{folder}/logo{Path(g['logo_path']).suffix.lower()}", Path(g["logo_path"]).read_bytes(), "로고 원본", folder)
    lines = []
    for f in fields:
        n = count_len(f["value"], f.get("count_method"))
        rng = f"{f['min_chars'] if f['min_chars'] is not None else ''}~{f['max_chars'] if f['max_chars'] is not None else ''}자"
        lines.append(f"[{f['field']}] {f['value']}\n  - {n}자 / 제한 {rng if f['max_chars'] or f['min_chars'] else '정보 없음'} · 출처: {f['source']}")
        rows_csv.append([f"{pv.placement.platform.name} · {pv.placement.name}", f["field"], f["value"], n,
                         f["max_chars"] if f["max_chars"] is not None else "", f["source"]])
    txt = "\n\n".join(lines) or "(문구 없음)"
    pack.add(f"{folder}/copy.txt", ("﻿" + txt + "\n").encode("utf-8"), "카피(TXT)", folder)
    return {"folder": folder, "image": f"{folder}/image.{ext}", "format": enc["format"], "bytes": enc["size"],
            "size": [img.width, img.height]}


def csv_bytes(header: list[str], rows: list[list]) -> bytes:
    s = io.StringIO()
    w = csv.writer(s)
    w.writerow(header)
    w.writerows(rows)
    return ("﻿" + s.getvalue()).encode("utf-8")      # BOM: 엑셀에서 한글이 깨지지 않게


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def review_csv(groups: list[dict]) -> bytes:
    rows = []
    for g in groups:
        for it in g["items"]:
            rows.append([g["name"], it["category"], it["item"], it["status"], "차단" if it["blocking"] else "",
                         it["message"], "확인함" if it["confirmed"] else ""])
    return csv_bytes(["지면", "분류", "항목", "상태", "차단", "내용", "사람 확인"], rows)
