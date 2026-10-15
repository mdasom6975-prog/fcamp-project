from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import review
from .models import (Asset, Brand, BrandVersion, CopyVariant, Design, Element, GeneratedImage, IconAsset,
                     PresetVersion, ReviewResult)
from .routers_assets import get_db

router = APIRouter(prefix="/api")


def invalidate_reviews(db: Session, design_id: int, preset_id: int | None = None) -> None:
    """시안·카피·이미지가 바뀌면 기존 검수 결과를 무효화한다 (다시 검수해야 한다)."""
    q = update(ReviewResult).where(ReviewResult.design_id == design_id)
    if preset_id is not None:
        q = q.where(ReviewResult.preset_version_id == preset_id)
    db.execute(q.values(valid=False))


def _latest(db, did, pid, kind):
    return db.scalar(select(GeneratedImage).where(
        GeneratedImage.design_id == did, GeneratedImage.preset_version_id == pid,
        GeneratedImage.kind == kind).order_by(GeneratedImage.id.desc()).limit(1))


def _brand_dict(bv: BrandVersion, name: str = "") -> dict:
    return {"name": name, "levels": bv.levels or {}, "forbidden_phrases": bv.forbidden_phrases or [],
            "required_phrases": bv.required_phrases or [], "colors": bv.colors or [], "fonts": bv.fonts or [],
            "logo_asset_id": bv.logo_asset_id, "icon_style": bv.icon_style or {}}


def gather(db: Session, d: Design, pv: PresetVersion, brand: dict) -> dict:
    """한 지면의 검수·내보내기에 필요한 입력을 모은다 (같은 값을 두 곳이 공유한다)."""
    sep = pv.placement.content_kind == "분리 자산형"
    comp = _latest(db, d.id, pv.id, "composite")
    sel = db.scalar(select(CopyVariant).where(CopyVariant.design_id == d.id, CopyVariant.preset_version_id == pv.id,
                                              CopyVariant.selected.is_(True)))
    layout = (comp.layout if comp else {}) or {}
    text_el = next((e for e in layout.get("elements", []) if e.get("type") == "text"), None)
    logo_el = next((e for e in layout.get("elements", []) if e.get("type") == "logo"), None)
    scrim = (layout.get("scrim") or {}).get("color")
    rows = db.scalars(select(Element).where(Element.design_id == d.id, Element.preset_version_id == pv.id)).all()
    t_row = next((r for r in rows if r.type == "text"), None)
    font_ref = t_row.props.get("font") if t_row else None
    if font_ref is None and text_el:       # 편집 전 자동 배치: 브랜드 '제목' 글꼴을 사용
        f = next((x for x in brand["fonts"] if x.get("role") == "제목"), None) or (brand["fonts"][0] if brand["fonts"] else None)
        font_ref = ({"source": f["source"], "name": f.get("name"), "asset_id": f.get("asset_id")} if f
                    else {"source": "provided", "name": "Pretendard-Bold"})
    texts = {"headline": (text_el["text"] if text_el else (sel.headline if sel else "")),
             "body": sel.body if sel else "", "cta": sel.cta if sel else ""}
    logo_aspect = None
    if brand["logo_asset_id"]:
        a = db.get(Asset, brand["logo_asset_id"])
        if a and a.width and a.height:
            logo_aspect = a.width / a.height
    return {"sep": sep, "comp": comp, "sel": sel, "text_el": text_el, "logo_el": logo_el, "scrim": scrim,
            "font_ref": font_ref, "texts": texts, "logo_aspect": logo_aspect}


def _out(r: ReviewResult) -> dict:
    status = r.status
    note = r.message
    if r.needs_human and r.confirmed and r.status == "확인 필요":
        status, note = "통과", f"{r.message} — 사람이 확인함"
    return {"id": r.id, "category": r.category, "item": r.item, "status": status, "raw_status": r.status,
            "message": note, "level": r.level, "blocking": r.blocking and status != "통과",
            "needs_human": r.needs_human, "confirmed": r.confirmed, "location": r.location}


def _summary(rows: list[dict]) -> dict:
    s = {"통과": 0, "경고": 0, "수정 필요": 0, "확인 필요": 0}
    for r in rows:
        s[r["status"]] = s.get(r["status"], 0) + 1
    return s


def _collect(db: Session, d: Design) -> dict:
    rows = db.scalars(select(ReviewResult).where(ReviewResult.design_id == d.id, ReviewResult.valid.is_(True))
                      .order_by(ReviewResult.id)).all()
    groups: dict = {}
    for r in rows:
        groups.setdefault(r.preset_version_id, []).append(_out(r))
    out_groups, blockers = [], []
    for pid in (d.preset_version_ids if d.kind == "광고 이미지" else [None]):
        items = groups.get(pid, [])
        name = None
        if pid is not None:
            pv = db.get(PresetVersion, pid)
            name = f"{pv.placement.platform.name} · {pv.placement.name}" if pv else str(pid)
        bl = [i for i in items if i["blocking"]]
        for b in bl:
            blockers.append({"preset": name, "item": b["item"], "status": b["status"], "message": b["message"]})
        out_groups.append({"preset_id": pid, "name": name or "아이콘 세트", "items": items,
                           "summary": _summary(items), "export_ready": bool(items) and not bl})
    reviewed = bool(rows)
    return {"reviewed": reviewed, "stale": not reviewed, "groups": out_groups, "blockers": blockers,
            "export_ready": reviewed and all(g["export_ready"] for g in out_groups)}


@router.get("/designs/{design_id}/review")
def get_review(design_id: int, db: Session = Depends(get_db)):
    d = db.get(Design, design_id)
    if not d:
        raise HTTPException(404, "시안을 찾을 수 없습니다")
    return _collect(db, d)


@router.post("/designs/{design_id}/review")
def run_review(design_id: int, db: Session = Depends(get_db)):
    d = db.get(Design, design_id)
    if not d:
        raise HTTPException(404, "시안을 찾을 수 없습니다")
    bv = db.get(BrandVersion, d.brand_version_id)
    cur = db.get(Brand, d.brand_id)
    brand = _brand_dict(bv, cur.name if cur else "")
    if bv.logo_asset_id:
        la = db.get(Asset, bv.logo_asset_id)
        if la and la.width and la.height:
            brand["logo_dims"] = (la.width, la.height)
    results: list[tuple[int | None, dict]] = []

    meta_items = []
    if cur and cur.current_version_id and cur.current_version_id != bv.id:
        meta_items.append(review.item("시안", "브랜드 버전", "경고",
                                      "시안을 만든 뒤 브랜드가 새 버전으로 바뀌었습니다. 영향받는 시안이므로 확인해 주세요."))
    else:
        meta_items.append(review.item("시안", "브랜드 버전", "통과", f"브랜드 버전 {bv.version_no} 기준"))

    if d.kind == "아이콘 세트":
        batch = db.scalar(select(IconAsset.batch_no).where(IconAsset.design_id == d.id)
                          .order_by(IconAsset.batch_no.desc()).limit(1))
        icons = db.scalars(select(IconAsset).where(IconAsset.design_id == d.id, IconAsset.batch_no == batch)).all() if batch else []
        for it in meta_items + review.review_icons(icons, brand):
            results.append((None, it))
    else:
        for pid in d.preset_version_ids:
            pv = db.get(PresetVersion, pid)
            items = list(meta_items)
            if pv.status == "확인 필요":
                items.append(review.item("규격", "규격 출처", "확인 필요", "공식 규격이 확인되지 않아 검수할 수 없습니다", "강제", True))
                results += [(pid, it) for it in items]
                continue
            g = gather(db, d, pv, brand)
            sep, comp, sel, text_el, logo_el, scrim = g["sep"], g["comp"], g["sel"], g["text_el"], g["logo_el"], g["scrim"]
            font_ref, texts, logo_aspect = g["font_ref"], g["texts"], g["logo_aspect"]
            if sel is None:
                items.append(review.item("문구", "사용할 카피", "수정 필요", "내보낼 카피가 선택되지 않았습니다", "강제", True))
            items += review.review_preset(pv, comp, texts, text_el, logo_el["box"] if logo_el else None,
                                          scrim, font_ref, brand, logo_aspect, sep)
            results += [(pid, it) for it in items]

    invalidate_reviews(db, d.id)
    for pid, it in results:
        db.add(ReviewResult(design_id=d.id, preset_version_id=pid, category=it["category"], item=it["item"],
                            status=it["status"], level=it["level"], message=it["message"],
                            blocking=it["blocking"], needs_human=it["needs_human"], location=it["location"]))
    db.commit()
    out = _collect(db, d)
    if out["export_ready"]:
        d.status = "검수 완료"
        db.commit()
    return out


@router.post("/review-results/{result_id}/confirm")
def confirm(result_id: int, confirmed: bool = Body(..., embed=True), db: Session = Depends(get_db)):
    r = db.get(ReviewResult, result_id)
    if not r or not r.valid:
        raise HTTPException(404, "검수 결과를 찾을 수 없습니다 (수정으로 무효화되었을 수 있습니다)")
    if not r.needs_human:
        raise HTTPException(400, "사람이 확인하는 항목이 아닙니다")
    r.confirmed = confirmed
    db.commit()
    d = db.get(Design, r.design_id)
    return _collect(db, d)
