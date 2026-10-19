import json
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import copyfields, exporter
from .db import STORAGE_DIR
from .models import Asset, Brand, BrandVersion, Design, Export, PresetVersion
from .routers_assets import get_db
from .routers_review import _brand_dict, _collect, gather, version_icon_set

router = APIRouter(prefix="/api")
EXPORT_DIR = STORAGE_DIR / "exports"


def _out(e: Export) -> dict:
    rs = e.review_summary or {}
    return {"id": e.id, "design_id": e.design_id, "status": rs.get("status"), "filename": rs.get("filename"),
            "preset_ids": e.preset_version_ids, "file_count": len(e.files or []),
            "unresolved": rs.get("unresolved", []), "created_at": e.created_at.isoformat(),
            "download_url": f"/api/exports/{e.id}/download"}


@router.get("/designs/{design_id}/exports")
def list_exports(design_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(Export).where(Export.design_id == design_id).order_by(Export.id.desc())).all()
    return [_out(e) for e in rows]


@router.get("/exports/{export_id}/download")
def download(export_id: int, db: Session = Depends(get_db)):
    e = db.get(Export, export_id)
    if not e or not e.zip_path or not Path(e.zip_path).exists():
        raise HTTPException(404, "내보내기 파일을 찾을 수 없습니다")
    return FileResponse(e.zip_path, media_type="application/zip", filename=(e.review_summary or {}).get("filename"))


@router.post("/designs/{design_id}/export")
def make_export(design_id: int, preset_ids: list[int] | None = Body(None), draft: bool = Body(False),
                db: Session = Depends(get_db)):
    d = db.get(Design, design_id)
    if not d:
        raise HTTPException(404, "시안을 찾을 수 없습니다")
    bv = db.get(BrandVersion, d.brand_version_id)
    brand_row = db.get(Brand, d.brand_id)
    brand = _brand_dict(bv, brand_row.name, d.brief)
    review = _collect(db, d)
    by_pid = {g["preset_id"]: g for g in review["groups"]}

    targets = preset_ids or list(d.preset_version_ids)
    bad = [p for p in targets if p not in d.preset_version_ids]
    if bad:
        raise HTTPException(400, f"이 시안에 없는 규격입니다: {bad}")
    # 아이콘 세트는 브랜드 규칙 버전에 붙으므로 지면 선택과 무관하게 항상 함께 나간다 (프론트도 같은 규칙)
    icons = version_icon_set(db, d.brand_version_id)
    if not targets and not icons:
        raise HTTPException(409, {"message": "내보낼 지면이나 아이콘 세트가 없습니다", "blockers": []})
    wanted: list[int | None] = list(targets) + ([None] if icons else [])

    groups = [by_pid[p] for p in wanted if p in by_pid]
    ready = bool(groups) and all(g["export_ready"] for g in groups) and len(groups) == len(wanted)
    unresolved = [{"preset": g["name"], "item": i["item"], "status": i["status"], "message": i["message"]}
                  for g in groups for i in g["items"] if i["blocking"]]
    if not draft and not ready:
        why = "검수 결과가 없습니다. 먼저 검수를 실행해 주세요." if not review["reviewed"] else \
              "차단 항목이 있어 '검수 완료' 묶음을 만들 수 없습니다. 미검수 작업본으로 내보낼 수 있습니다."
        raise HTTPException(409, {"message": why, "blockers": unresolved})
    status = "검수 완료" if ready else "미검수 작업본"

    pack = exporter.Pack()
    manifest_presets, csv_rows, skipped = [], [], []
    if targets:
        logo_path = None
        if bv.logo_asset_id:
            a = db.get(Asset, bv.logo_asset_id)
            logo_path = a.file_path if a and Path(a.file_path).exists() else None
        for pid in targets:
            pv = db.get(PresetVersion, pid)
            g = gather(db, d, pv, brand)
            if g["comp"] is None or not Path(g["comp"].file_path).exists():
                skipped.append({"preset": pv.placement.name, "reason": "생성된 이미지가 없습니다"})
                continue
            g["logo_path"] = logo_path
            fields = copyfields.fields_for(pv.text_limits or [], g["texts"], brand_row.name)
            info = exporter.ad_preset_folder(pack, pv, g, fields, csv_rows)
            grp = by_pid.get(pid)
            manifest_presets.append({
                "preset_id": pid, "platform": pv.placement.platform.name, "name": pv.placement.name,
                "content_kind": pv.placement.content_kind, "preset_version_no": pv.version_no,
                "spec_status": pv.status, "spec_checked_at": pv.checked_at, "spec_sources": pv.source_urls,
                "image": info["image"], "format": info["format"], "bytes": info["bytes"], "size": info["size"],
                "review": grp["summary"] if grp else None, "ready": bool(grp and grp["export_ready"]),
            })
        pack.add("copy.csv", exporter.csv_bytes(["지면", "필드", "값", "글자 수", "최대 글자 수", "출처"], csv_rows),
                 "카피(CSV)")
    if icons:
        grp = by_pid.get(None)
        for a in icons:
            base = f"{a.position:02d}_{exporter.safe(a.name, 30)}"
            if a.svg:   # svg 방식만 원본 SVG 가 있다
                pack.add(f"icons/svg/{base}.svg", a.svg.encode("utf-8"), "아이콘 SVG(원본)")
            pack.add(f"icons/png/{base}.png", Path(a.png_path).read_bytes(), "아이콘 PNG")
            if a.source_path and Path(a.source_path).exists():   # 3d 방식: 1024 투명 원본
                pack.add(f"icons/src/{base}.png", Path(a.source_path).read_bytes(), "아이콘 원본 PNG(1024, 투명)")
        manifest_presets.append({"name": "아이콘 세트", "count": len(icons), "size": [icons[0].width, icons[0].height],
                                 "render": (icons[0].style or {}).get("render", "svg"), "style": icons[0].style,
                                 "prompts": sorted({a.prompt for a in icons if a.prompt}) if icons[0].prompt else None,
                                 "review": grp["summary"] if grp else None, "ready": bool(grp and grp["export_ready"])})

    pack.add("검수결과.csv", exporter.review_csv(groups), "검수 결과")
    if status != "검수 완료":
        lines = ["이 묶음은 '미검수 작업본'입니다. 아래 항목이 해결되지 않았습니다.", ""]
        lines += [f"- [{u['status']}] {u['preset']} · {u['item']}: {u['message']}" for u in unresolved] or \
                 ["- (검수 결과가 없습니다. 먼저 검수를 실행해 주세요.)"]
        pack.add("미검수_작업본.txt", ("﻿" + "\n".join(lines) + "\n").encode("utf-8"), "미검수 안내")
    manifest = {
        "status": status, "exported_at": exporter.now_str(), "design_id": d.id, "kind": d.kind,
        "brand": {"id": brand_row.id, "name": brand_row.name, "version_no": bv.version_no},
        "presets": manifest_presets, "skipped": skipped, "unresolved": unresolved,
        "notice": "파일 규격 검수는 광고 심사나 실제 게재를 보장하지 않습니다. 외부 플랫폼 업로드는 사용자가 직접 수행합니다.",
    }
    data = pack.finish(manifest)
    prefix = "검수완료" if status == "검수 완료" else "미검수작업본"
    stamp = exporter.now_str().replace(":", "").replace("-", "").replace(" ", "_")
    filename = f"{prefix}_{exporter.safe(brand_row.name, 20)}_{d.id}_{stamp}.zip"
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"exp_{d.id}_{stamp}.zip"
    path.write_bytes(data)
    e = Export(design_id=d.id, brand_version_id=bv.id, preset_version_ids=[p for p in targets if p is not None],
               files=pack.files, zip_path=str(path),
               review_summary={"status": status, "filename": filename, "unresolved": unresolved,
                               "groups": [{"name": g["name"], "summary": g["summary"]} for g in groups]})
    db.add(e)
    db.commit()
    return {**_out(e), "files": pack.files, "skipped": skipped}
