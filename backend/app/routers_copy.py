from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import calllog, copyfields, copywriter, knowledge, legal, phrases
from .models import Brand, BrandVersion, CopyVariant, Design, PresetVersion
from .routers_assets import get_db, get_design
from .routers_review import invalidate_reviews

router = APIRouter(prefix="/api", dependencies=[Depends(calllog.track_design)])   # 호출 기록에 시안 id


def _brand_rules(bv: BrandVersion, brief: dict | None = None) -> dict:
    """카피 규칙. 필수·금지 표현은 캠페인 입력이 우선, 비면 브랜드 규칙 (phrases.effective)."""
    return phrases.rules(bv, brief)


def with_legal(r: CopyVariant, brief: dict | None) -> list[dict]:
    """저장된 위반 중 법적 표현은 버리고 지금의 표현 사전으로 다시 검사해 붙인다(사전이 바뀌거나 사전 도입 전 카피여도 최신 기준)."""
    text = " ".join([r.headline or "", r.body or "", r.cta or ""])
    return [v for v in (r.violations or []) if v.get("type") != "legal"] + legal.violations(text, legal.source_text(brief), (brief or {}).get("functional"))


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
                          "cta": r.cta, "violations": with_legal(r, design.brief), "attempts": r.attempts, "selected": r.selected} for r in rows],
        })
    return out


def _knowledge(d: Design, db: Session) -> dict:
    """카피 프롬프트에 붙일 규제 원문·브랜드 지식(knowledge.for_copy). 브랜드의 그룹으로 그룹 공통 자료를 찾는다."""
    brand = db.get(Brand, d.brand_id)
    return knowledge.for_copy(db, d.brand_id, brand.group_id if brand else None, d.brief or {})


def _knowledge_summary(k: dict) -> dict:
    """응답용 요약: 넣은 조각 수, 검색 실패, 기능성 불일치 안내."""
    return {"law": len(k["law"]), "guide": len(k["guide"]), "industries": k["industries"], "error": k["error"],
            "functional_mismatch": k["functional_mismatch"], "product_note": k["product_note"]}


def _latest_variants(d: Design, db: Session) -> list[dict]:
    """첫 지면의 가장 최근 차수 카피(모든 지면이 같은 문장). 추가 문구의 어조 참고용."""
    groups = _group(d, db)
    return [{"headline": v["headline"], "body": v["body"]} for v in groups[0]["variants"]] if groups else []


def _design(design_id: int, db: Session) -> Design:
    d = get_design(db, design_id)
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
    spec = copyfields.asset_spec(presets)      # PMax 등 '문구 수량' 조건 지면의 추가 문구 요구
    names = [pv.placement.name for pv in presets]
    placement = {"platform": "모든 선택 지면 공통",
                 "name": ", ".join(names[:3]) + (f" 외 {len(names) - 3}개" if len(names) > 3 else "")}
    k = _knowledge(d, db)
    try:
        variants, attempts = copywriter.generate_checked(brand_name, rules, d.brief, placement, merged, knowledge=k)
    except Exception as e:  # 키 값 등 민감 정보는 메시지에 포함하지 않는다
        return {"groups": _group(d, db), "failures": [f"카피 생성: {type(e).__name__}"], "knowledge": _knowledge_summary(k)}
    # 아이콘 후보·추가 문구는 작은 모델로 따로 받는다. 실패해도 카피는 살린다(아이콘은 세트 생성 때 다시 제안, 추가 문구는 별도 버튼·검수 안내)
    failures: list[str] = []
    icon_candidates, text_assets = [], {}
    try:
        icon_candidates, text_assets = copywriter.extras(brand_name, rules, d.brief, spec, variants, k)
    except Exception as e:
        failures.append(f"아이콘 후보·추가 문구: {type(e).__name__}")
    # 이번 카피에 넣은 규제·지식 조각과 시안별 각도·근거(분석 화면 '참고한 자료')
    extra = {"knowledge": {**knowledge.refs(k), "variants": [
        {"variant_no": i, "angle": v.get("angle", ""), "evidence": v.get("evidence", "")} for i, v in enumerate(variants, 1)]}}
    if icon_candidates:
        # 같은 호출에서 받은 아이콘 기능명 후보를 브리프에 두면 아이콘 세트 생성이 기능명 제안 호출 없이 이것을 쓴다
        extra["icon_candidates"] = icon_candidates
    if spec:
        # 추가 문구 자산(필드 → 문구 목록)은 검수('문구 수량')와 내보내기(text_assets.txt)가 쓴다. 카피를 다시 만들면 교체된다
        extra["text_assets"] = text_assets
    d.brief = {**(d.brief or {}), **extra}
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
    return {"groups": _group(d, db), "failures": failures, "merged_limits": merged,
            "icon_candidates": len(icon_candidates),
            "text_assets": {name: len(v) for name, v in (text_assets or {}).items()},
            "knowledge": _knowledge_summary(k)}


@router.post("/designs/{design_id}/copy/assets")
def create_text_assets(design_id: int, db: Session = Depends(get_db)):
    """문구를 여러 개 요구하는 지면(PMax 등)의 추가 문구 자산만 새로 받는다(채팅 1회). 카피 시안·선택은 그대로 둔다.
    카피를 추가 문구 지원 전에 만들었거나, 자산만 다시 받고 싶을 때 쓴다."""
    d = _design(design_id, db)
    bv = db.get(BrandVersion, d.brand_version_id)
    brand_name = db.get(Brand, d.brand_id).name
    rules = _brand_rules(bv, d.brief)
    presets = [pv for pid in d.preset_version_ids if (pv := db.get(PresetVersion, pid))]
    spec = copyfields.asset_spec(presets)
    if not spec:
        raise HTTPException(400, "문구를 여러 개 요구하는 지면이 선택되어 있지 않습니다")
    last = _latest_variants(d, db)
    try:
        _, got = copywriter.extras(brand_name, rules, d.brief, spec, last, _knowledge(d, db), want_icons=False)
    except Exception as e:
        raise HTTPException(502, f"추가 문구 생성에 실패했습니다 ({type(e).__name__})")
    if not any(got.values()):
        raise HTTPException(502, "추가 문구를 받지 못했습니다")
    d.brief = {**(d.brief or {}), "text_assets": got}
    invalidate_reviews(db, d.id)
    db.commit()
    return {"text_assets": {k: len(v) for k, v in got.items()}, "fields": [a["field"] for a in spec]}


@router.post("/designs/{design_id}/presets/{preset_id}/copy/fit")
def fit_copy_to_preset(design_id: int, preset_id: int, db: Session = Depends(get_db)):
    """한 지면의 선택 카피 중 글자 수 제한을 넘는 필드만 그 지면 제한에 맞게 고쳐 쓴다(채팅 1회). 다른 지면·시안 선택은 그대로.
    고친 뒤 코드로 다시 검사해 위반을 갱신하고 그 지면의 검수 결과를 무효화한다."""
    d = _design(design_id, db)
    if preset_id not in d.preset_version_ids:
        raise HTTPException(404, "이 시안에 포함된 규격이 아닙니다")
    pv = db.get(PresetVersion, preset_id)
    sel = db.scalar(select(CopyVariant).where(CopyVariant.design_id == d.id, CopyVariant.preset_version_id == preset_id,
                                              CopyVariant.selected.is_(True)))
    if sel is None:
        raise HTTPException(400, "이 지면에 선택된 카피가 없습니다")
    bv = db.get(BrandVersion, d.brand_version_id)
    rules = _brand_rules(bv, d.brief)
    limits = copywriter.limits_for(pv.text_limits or [])
    cur = {"headline": sel.headline, "body": sel.body, "cta": sel.cta}
    viol = copywriter.check(cur, limits, rules)
    slots = {}
    for v in viol:
        if v["type"] in ("length", "min_length"):
            slot = "headline" if v["field"].startswith("제목") else "body" if v["field"].startswith("본문") else "cta"
            slots[slot] = v
    if not slots:
        raise HTTPException(400, "이 지면에서 글자 수를 넘는 필드가 없습니다")
    def rule_line(slot: str) -> str:
        l = limits[slot]
        rng = []
        if l.get("min") is not None:
            rng.append(f"{l['min']}자 이상")
        if l.get("max") is not None:
            rng.append(f"{l['max']}자 이하")
        how = ("한글 등 전각 문자는 글자당 2자로 계산(한글 한 글자=2), 영문·숫자·공백은 1자"
               if l.get("method") == "전각 2자 계산" else "공백 포함")
        return f"- {slot}: {', '.join(rng)} ({how}, 매체 필드명 '{l['field']}') — 현재 {slots[slot]['detail']}"
    from . import llm, prompts
    system = prompts.render("copy/fit_system")
    user = prompts.render("copy/fit_user", brand_name=db.get(Brand, d.brand_id).name,
                          required=", ".join(rules.get("required_phrases", [])) or "없음",
                          forbidden=", ".join(rules.get("forbidden_phrases", [])) or "없음",
                          platform=pv.placement.platform.name, placement=pv.placement.name,
                          headline=cur["headline"], body=cur["body"], cta=cur["cta"],
                          fixes="\n".join(rule_line(s) for s in slots))
    try:
        data = llm.chat_json(system, user, "copy_fit", llm._obj({"headline": llm.STR, "body": llm.STR, "cta": llm.STR}))
    except Exception as e:
        raise HTTPException(502, f"문구 줄이기에 실패했습니다 ({type(e).__name__})")
    new = dict(cur)
    for slot in slots:                     # 고치라고 한 필드만 받아들인다
        t = str(data.get(slot) or "").strip()
        if t:
            new[slot] = t
    new_viol = copywriter.check(new, limits, rules)
    sel.headline, sel.body, sel.cta = new["headline"], new["body"], new["cta"]
    sel.violations = new_viol
    invalidate_reviews(db, d.id, preset_id)
    db.commit()
    remaining = [v for v in new_viol if v["type"] in ("length", "min_length")]
    return {"preset_id": preset_id, "variant_no": sel.variant_no, "changed": sorted(slots),
            "headline": sel.headline, "body": sel.body, "cta": sel.cta, "violations": new_viol,
            "fits": not remaining, "note": None if not remaining else "줄였지만 아직 제한을 넘습니다. 다시 누르거나 편집하세요"}


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
