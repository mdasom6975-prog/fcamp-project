from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Placement, Platform, PresetVersion
from .routers_assets import get_db

router = APIRouter(prefix="/api")


def _preset_out(pl: Placement, v: PresetVersion) -> dict:
    return {
        "id": v.id, "placement_id": pl.id, "name": pl.name, "ad_type": pl.ad_type,
        "content_kind": pl.content_kind, "purpose": pl.purpose,
        "version_no": v.version_no, "active": v.active, "status": v.status,
        "production_width": v.production_width, "production_height": v.production_height,
        "ratio": v.ratio, "conditions": v.conditions, "text_limits": v.text_limits,
        "safe_area": v.safe_area, "required_assets": v.required_assets, "note": v.note,
        "source_urls": v.source_urls, "checked_at": v.checked_at,
        # 공식 규격을 확인하지 못해 검수할 수 없는 항목
        "reviewable": v.status != "확인 필요",
    }


@router.get("/presets")
def list_presets(db: Session = Depends(get_db)):
    """플랫폼 > 광고 유형 > 지면 구조. 활성(active) 규격 버전만 돌려준다."""
    out = []
    for plat in db.scalars(select(Platform).order_by(Platform.id)).all():
        items = []
        for pl in plat.placements:
            vs = [v for v in pl.versions if v.active]
            if vs:
                items.append(_preset_out(pl, max(vs, key=lambda x: x.version_no)))
        # 모든 항목이 '확인 필요' 이고 수치가 하나도 없으면 매체 단위로만 표시
        media_only = bool(items) and all(
            p["status"] == "확인 필요" and not p["conditions"] and p["production_width"] is None
            for p in items)
        groups: dict[str, list] = {}
        for p in items:
            groups.setdefault(p["ad_type"], []).append(p)
        if not items:
            continue          # 활성 규격이 없는 매체는 목록에서 숨긴다
        out.append({
            "id": plat.id, "name": plat.name, "media_only": media_only,
            "note": items[0]["note"] if media_only else None,
            "ad_types": [] if media_only else [{"name": k, "presets": v} for k, v in groups.items()],
        })
    return out


@router.get("/presets/{preset_id}")
def get_preset(preset_id: int, db: Session = Depends(get_db)):
    v = db.get(PresetVersion, preset_id)
    if not v:
        raise HTTPException(404, "규격을 찾을 수 없습니다")
    return {"platform": v.placement.platform.name, **_preset_out(v.placement, v)}
