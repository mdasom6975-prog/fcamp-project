from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import copywriter
from .models import Brand, BrandVersion, CopyVariant, Design, PresetVersion
from .routers_assets import get_db
from .routers_review import invalidate_reviews

router = APIRouter(prefix="/api")


def _brand_rules(bv: BrandVersion) -> dict:
    return {"voice": bv.voice, "required_phrases": bv.required_phrases,
            "forbidden_phrases": bv.forbidden_phrases, "levels": bv.levels}


def _group(design: Design, db: Session) -> list[dict]:
    """지면별로 가장 최근 차수의 카피를 돌려준다."""
    out = []
    for pid in design.preset_version_ids:
        pv = db.get(PresetVersion, pid)
        if not pv:
            continue
        last = db.scalar(select(func.max(CopyVariant.batch_no)).where(
            CopyVariant.design_id == design.id, CopyVariant.preset_version_id == pid))
        rows = db.scalars(select(CopyVariant).where(
            CopyVariant.design_id == design.id, CopyVariant.preset_version_id == pid,
            CopyVariant.batch_no == last).order_by(CopyVariant.variant_no)).all() if last else []
        limits = copywriter.limits_for(pv.text_limits)
        out.append({
            "preset_id": pid, "platform": pv.placement.platform.name, "name": pv.placement.name,
            "content_kind": pv.placement.content_kind, "width": pv.production_width, "height": pv.production_height,
            "limits": limits, "batch_no": last,
            "variants": [{"id": r.id, "variant_no": r.variant_no, "headline": r.headline, "body": r.body,
                          "cta": r.cta, "violations": r.violations, "attempts": r.attempts, "selected": r.selected} for r in rows],
        })
    return out


def _design(design_id: int, db: Session) -> Design:
    d = db.get(Design, design_id)
    if not d:
        raise HTTPException(404, "시안을 찾을 수 없습니다")
    if d.kind != "광고 이미지":
        raise HTTPException(400, "카피 생성은 광고 이미지 시안에서만 사용합니다")
    return d


@router.get("/designs/{design_id}/copy")
def get_copy(design_id: int, db: Session = Depends(get_db)):
    return _group(_design(design_id, db), db)


@router.post("/designs/{design_id}/copy")
def create_copy(design_id: int, db: Session = Depends(get_db)):
    d = _design(design_id, db)
    bv = db.get(BrandVersion, d.brand_version_id)
    brand_name = db.get(Brand, d.brand_id).name
    rules = _brand_rules(bv)
    failures = []
    for pid in d.preset_version_ids:
        pv = db.get(PresetVersion, pid)
        if not pv:
            continue
        limits = copywriter.limits_for(pv.text_limits)
        try:
            variants, attempts = copywriter.generate_checked(
                brand_name, rules, d.brief, {"platform": pv.placement.platform.name, "name": pv.placement.name}, limits)
        except Exception as e:  # 키 값 등 민감 정보는 메시지에 포함하지 않는다
            failures.append(f"{pv.placement.name}: {type(e).__name__}")
            continue
        last = db.scalar(select(func.max(CopyVariant.batch_no)).where(
            CopyVariant.design_id == d.id, CopyVariant.preset_version_id == pid)) or 0
        for i, v in enumerate(variants, 1):
            db.add(CopyVariant(
                design_id=d.id, preset_version_id=pid, batch_no=last + 1, variant_no=i,
                headline=v["headline"], body=v["body"], cta=v["cta"],
                limits={k: limits[k] for k in ("headline", "body", "cta")},
                violations=v["violations"], attempts=attempts))
    invalidate_reviews(db, d.id)
    db.commit()
    return {"groups": _group(d, db), "failures": failures}
