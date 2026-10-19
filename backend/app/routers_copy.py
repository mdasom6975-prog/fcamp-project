from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import copywriter, phrases
from .models import Brand, BrandVersion, CopyVariant, Design, PresetVersion
from .routers_assets import get_db
from .routers_review import invalidate_reviews

router = APIRouter(prefix="/api")


def _brand_rules(bv: BrandVersion, brief: dict | None = None) -> dict:
    """카피 규칙. 필수·금지 표현은 캠페인 입력이 우선, 비면 브랜드 규칙 (phrases.effective)."""
    return phrases.rules(bv, brief)


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
    """캠페인당 1회 생성해 모든 지면이 같은 문장을 쓴다 (시안 선택 시 매체 간 카피 통일).
    제한은 선택 지면 중 가장 엄격한 값으로 합쳐 생성 조건으로 주고, 저장은 지면마다 하되 위반은 각 지면 제한으로 다시 검사한다."""
    d = _design(design_id, db)
    bv = db.get(BrandVersion, d.brand_version_id)
    brand_name = db.get(Brand, d.brand_id).name
    rules = _brand_rules(bv, d.brief)
    presets = [pv for pid in d.preset_version_ids if (pv := db.get(PresetVersion, pid))]
    if not presets:
        raise HTTPException(400, "선택한 규격이 없습니다")
    per = [(pv.placement.name, copywriter.limits_for(pv.text_limits)) for pv in presets]
    merged = copywriter.merge_limits(per)
    names = [pv.placement.name for pv in presets]
    placement = {"platform": "모든 선택 지면 공통",
                 "name": ", ".join(names[:3]) + (f" 외 {len(names) - 3}개" if len(names) > 3 else "")}
    try:
        variants, attempts = copywriter.generate_checked(brand_name, rules, d.brief, placement, merged)
    except Exception as e:  # 키 값 등 민감 정보는 메시지에 포함하지 않는다
        return {"groups": _group(d, db), "failures": [f"카피 생성: {type(e).__name__}"]}
    for pv, (_, limits) in zip(presets, per):
        last = db.scalar(select(func.max(CopyVariant.batch_no)).where(
            CopyVariant.design_id == d.id, CopyVariant.preset_version_id == pv.id)) or 0
        for i, v in enumerate(variants, 1):
            db.add(CopyVariant(
                design_id=d.id, preset_version_id=pv.id, batch_no=last + 1, variant_no=i,
                headline=v["headline"], body=v["body"], cta=v["cta"],
                limits={k: limits[k] for k in ("headline", "body", "cta")},
                violations=copywriter.check(v, limits, rules), attempts=attempts))
    invalidate_reviews(db, d.id)
    db.commit()
    return {"groups": _group(d, db), "failures": [], "merged_limits": merged}


@router.post("/designs/{design_id}/copy/select-variant")
def select_variant(design_id: int, variant_no: int = Body(..., embed=True), db: Session = Depends(get_db)):
    """시안 번호 하나를 모든 지면에 한 번에 적용한다 (매체 간 카피 통일). 각 지면의 최신 차수에서 같은 번호를 고른다."""
    d = _design(design_id, db)
    picked: list[int] = []
    for pid in d.preset_version_ids:
        last = db.scalar(select(func.max(CopyVariant.batch_no)).where(
            CopyVariant.design_id == d.id, CopyVariant.preset_version_id == pid))
        if not last:
            continue
        rows = db.scalars(select(CopyVariant).where(
            CopyVariant.design_id == d.id, CopyVariant.preset_version_id == pid, CopyVariant.batch_no == last)).all()
        target = next((r for r in rows if r.variant_no == variant_no), None)
        if target is None:
            continue
        # 이전 차수에 남아 있던 선택도 지운다 (합성은 차수와 무관하게 selected 행을 쓴다)
        db.execute(update(CopyVariant).where(CopyVariant.design_id == d.id,
                                             CopyVariant.preset_version_id == pid).values(selected=False))
        target.selected = True
        picked.append(pid)
        invalidate_reviews(db, d.id, pid)
    if not picked:
        raise HTTPException(404, "해당 번호의 시안이 없습니다")
    db.commit()
    return {"variant_no": variant_no, "preset_ids": picked}
