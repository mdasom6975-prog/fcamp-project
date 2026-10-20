import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import calllog, icons, imagegen, phrases, prompts
from .db import STORAGE_DIR
from .models import Brand, BrandVersion, Design, GeneratedImage, IconAsset
from .routers_assets import get_db
from .routers_review import invalidate_icon_reviews, version_icon_set

router = APIRouter(prefix="/api", dependencies=[Depends(calllog.track_design)])   # 호출 기록에 시안 id
WEB_SIZE = (256, 256)   # 웹용 아이콘 기본 크기 (사용자 결정)


def _design(db: Session, design_id: int) -> Design:
    d = db.get(Design, design_id)
    if not d:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")
    return d


def _size(d: Design) -> tuple[int, int]:
    ic = (d.brief or {}).get("icon") or {}
    if ic.get("purpose") == "사용자 지정" and ic.get("width") and ic.get("height"):
        return int(ic["width"]), int(ic["height"])
    return WEB_SIZE


def _style(bv: BrandVersion) -> dict:
    st = bv.icon_style or {}
    color = next((c["hex"] for c in (bv.colors or []) if c.get("role") == "주조색"), "#000000")
    return {"stroke_width": float(st.get("stroke_width") or 2), "corner": st.get("corner") or "둥근",
            "fill": st.get("fill") or "윤곽", "color": color, "material": st.get("material") or "자동"}


def _palette(bv: BrandVersion) -> list[dict]:
    """3D 아이콘에 쓰는 색: 브랜드 주조·보조·강조색."""
    return [c for c in (bv.colors or []) if c.get("role") in icons.PALETTE_ROLES and c.get("hex")]


def _out(a: IconAsset) -> dict:
    return {"id": a.id, "position": a.position, "name": a.name, "concept": a.concept, "style": a.style,
            "width": a.width, "height": a.height, "violations": a.violations, "batch_no": a.batch_no,
            "render": (a.style or {}).get("render", "svg"), "has_svg": bool(a.svg), "has_source": bool(a.source_path)}


def _forbidden_viol(name: str, forbidden: list[str]) -> list[dict]:
    return [{"type": "forbidden", "detail": f"기능명에 금지 표현 '{p}' 포함"} for p in forbidden if p and p in name]


def _apply(db, d, bv, batch, pos, fields: dict, existing: IconAsset | None) -> IconAsset:
    if existing is not None:
        for k, v in fields.items():
            setattr(existing, k, v)
        return existing
    a = IconAsset(design_id=d.id, brand_version_id=bv.id, batch_no=batch, position=pos, **fields)
    db.add(a)
    return a


def _save(db, d, bv, batch, pos, name, concept, elements, style, w, h, forbidden, existing=None):
    """svg 방식: 도형을 정리해 SVG 를 만들고 PNG 로 렌더한다."""
    clean = icons.sanitize(elements)
    if not clean:
        raise ValueError("유효한 도형이 없습니다")
    svg, info = icons.build_svg(clean, style)
    STORAGE_DIR.mkdir(exist_ok=True)
    path = STORAGE_DIR / f"icon_{uuid.uuid4().hex[:10]}.png"
    path.write_bytes(icons.to_png(svg, w, h))
    fields = dict(name=name[:60], concept=(concept or "")[:200], elements=clean,
                  style={**style, **info, "render": "svg", "viewBox": icons.VIEW, "live_area": icons.LIVE},
                  svg=svg, png_path=str(path), width=w, height=h, violations=_forbidden_viol(name, forbidden),
                  prompt=None, source_path=None)
    return _apply(db, d, bv, batch, pos, fields, existing)


def _old_files(a: IconAsset | None) -> list[Path]:
    return [Path(p) for p in ((a.png_path, a.source_path) if a else ()) if p]


def _save3d(db, d, bv, batch, pos, name, concept, prompt, im, style, palette, w, h, forbidden, existing=None):
    """3d 방식: 렌더된 투명 PNG 를 live area 에 맞춰 저장한다. 1024 원본은 source_path 에 따로 둔다."""
    fitted, info = icons.fit_alpha(im, w, h)
    STORAGE_DIR.mkdir(exist_ok=True)
    uid = uuid.uuid4().hex[:10]
    src, path = STORAGE_DIR / f"icon3d_{uid}_src.png", STORAGE_DIR / f"icon3d_{uid}.png"
    im.save(src)
    fitted.save(path)
    viol = _forbidden_viol(name, forbidden)
    if not info["transparent"]:
        viol.append({"type": "background", "detail": "배경이 투명하지 않습니다. 다시 그려 주세요"})
    st = {"render": "3d", "model": icons.MODEL_3D, "quality": icons.QUALITY_3D,
          "prompt_ref": prompts.ref("icons/render3d"), "style_ref": prompts.ref("icons/_3d_style"),
          "corner": style["corner"], "material": style.get("material") or "자동",
          "colors": [c["hex"] for c in palette] or [style["color"]],
          "bbox": info["bbox"], "scale": info["scale"], "transparent": info["transparent"],
          "viewBox": icons.VIEW, "live_area": icons.LIVE}
    fields = dict(name=name[:60], concept=(concept or "")[:200], elements=[], style=st, svg="",
                  png_path=str(path), source_path=str(src), prompt=prompt, width=w, height=h, violations=viol)
    old = _old_files(existing)
    a = _apply(db, d, bv, batch, pos, fields, existing)
    for f in old:   # 다시 그린 아이콘의 이전 파일은 더 참조되지 않으므로 지운다 (storage 하위만)
        if STORAGE_DIR in f.parents and f.exists():
            f.unlink()
    return a


def _dedupe(items: list[dict]) -> list[dict]:
    uniq: list[dict] = []
    for it in items:
        n = str(it.get("name") or "").strip()
        if n and n not in [str(u.get("name")).strip() for u in uniq]:
            uniq.append(it)
    return uniq


def _fill(items: list[dict], more) -> list[dict]:
    """모델이 목표 개수보다 적게 주면 겹치지 않는 기능을 추가로 받는다 (최대 2회). more(existing_names, need) -> list"""
    for _ in range(2):
        items = _dedupe(items)
        if len(items) >= icons.COUNT:
            break
        try:
            items += more([str(x.get("name")) for x in items], icons.COUNT - len(items))
        except Exception:
            break
    return _dedupe(items)[:icons.COUNT]


@router.get("/designs/{design_id}/icons")
def list_icons(design_id: int, db: Session = Depends(get_db)):
    d = _design(db, design_id)
    return [_out(a) for a in version_icon_set(db, d.brand_version_id)]


@router.get("/icon-settings")
def icon_settings():
    """프론트 안내용: 현재 렌더 방식·모델·품질."""
    return {"render": icons.RENDER, "model": icons.MODEL_3D, "quality": icons.QUALITY_3D, "count": icons.COUNT}


def _draw_save_svg(db, d, bv, brand, items, style, w, h, forbidden, batch=1, start=1) -> tuple[int, list[dict]]:
    """svg 방식: 제안받은 도형을 position=start 부터 저장한다. 세트 생성과 부족분 채우기가 같이 쓴다."""
    made, skipped = 0, []
    for i, it in enumerate(items, start):
        name = str(it.get("name") or "").strip()
        try:
            if not name:
                raise ValueError("기능명이 없습니다")
            _save(db, d, bv, batch, i, name, str(it.get("concept") or ""), it.get("elements"), style, w, h, forbidden)
            made += 1
        except Exception:
            # 도형이 잘못된 아이콘은 한 번 다시 그려 본다
            try:
                els = icons.redraw_one(brand.name, d.brief, name or f"아이콘 {i}", str(it.get("concept") or ""), style)
                _save(db, d, bv, batch, i, name or f"아이콘 {i}", str(it.get("concept") or ""), els, style, w, h, forbidden)
                made += 1
            except Exception as e2:
                skipped.append({"position": i, "name": name, "reason": str(e2)[:80]})
    return made, skipped


def _create_svg(db, d, bv, brand, style, w, h, forbidden) -> tuple[int, list[dict]]:
    try:
        items = icons.propose_and_draw(brand.name, d.brief, style, forbidden)
    except Exception as e:
        raise HTTPException(502, f"아이콘 생성에 실패했습니다 ({type(e).__name__})")
    items = _fill(items, lambda names, need: icons.propose_more(brand.name, d.brief, style, forbidden, names, need))
    return _draw_save_svg(db, d, bv, brand, items, style, w, h, forbidden)


def _candidates_from_brief(d, forbidden) -> list[dict]:
    """카피 생성 때 같은 호출로 받아 둔 기능명 후보(brief.icon_candidates). 금지 표현이 든 이름은 뺀다.
    세트 크기(COUNT) 이상 남으면 기능명 제안 호출을 건너뛴다."""
    cands = [c for c in ((d.brief or {}).get("icon_candidates") or []) if isinstance(c, dict) and str(c.get("name") or "").strip()]
    return [c for c in cands if not any(f and f in c["name"] for f in forbidden)]


def _create_3d(db, d, bv, brand, style, w, h, forbidden) -> tuple[int, list[dict]]:
    items = _candidates_from_brief(d, forbidden)
    if len(_dedupe(items)) < icons.COUNT:      # 후보가 모자라면(카피 생성 전, 예전 시안) 전처럼 제안 호출
        try:
            items = icons.propose3d(brand.name, d.brief, forbidden)
        except Exception as e:
            raise HTTPException(502, f"기능명 제안에 실패했습니다 ({type(e).__name__})")
    items = _fill(items, lambda names, need: icons.propose3d_more(brand.name, d.brief, forbidden, names, need))
    items = [it for it in items if str(it.get("name") or "").strip()]
    if not items:
        raise HTTPException(502, "기능명을 받지 못했습니다")
    return _render_save_3d(db, d, bv, items, style, w, h, forbidden)


def _render_save_3d(db, d, bv, items, style, w, h, forbidden, batch=1, start=1) -> tuple[int, list[dict]]:
    """3d 방식: 기능명 목록을 렌더해 position=start 부터 저장한다. 세트 생성과 부족분 채우기가 같이 쓴다."""
    palette = _palette(bv)
    sv = icons.style_vars(style, palette, bv.image_tone or {})
    plist = [icons.render_prompt(str(it["name"]).strip(), str(it.get("concept") or ""), sv) for it in items]
    results = icons.render_many(plist)      # 이미지 모델 호출은 동시에, DB 저장은 이 스레드에서
    made, skipped = 0, []
    for i, (it, pr, res) in enumerate(zip(items, plist, results), start):
        name = str(it["name"]).strip()
        if isinstance(res, Exception):
            reason = str(res) if isinstance(res, imagegen.QuotaExceeded) else f"렌더 실패 ({type(res).__name__})"
            skipped.append({"position": i, "name": name, "reason": reason[:120]})
            continue
        try:
            _save3d(db, d, bv, batch, i, name, str(it.get("concept") or ""), pr, res, style, palette, w, h, forbidden)
            made += 1
        except Exception as e:
            skipped.append({"position": i, "name": name, "reason": str(e)[:80]})
    return made, skipped


def _fail_if_none(made: int, skipped: list[dict]) -> None:
    """하나도 못 만들었으면 성공 응답 대신 원인을 돌려준다."""
    if made == 0 and skipped:
        reason = skipped[0]["reason"]
        raise HTTPException(429 if "한도" in reason or "크레딧" in reason else 502, f"아이콘 생성에 실패했습니다: {reason}")


def _check_daily_limit() -> None:
    if icons.RENDER == "3d" and imagegen.usage("openai")["exhausted"]:
        raise HTTPException(429, f"오늘 설정한 이미지 생성 한도({imagegen.DAILY_LIMIT}회)를 모두 사용했습니다")


def _ensure_candidates(db, d, brand, style, forbidden, render: str) -> list[dict]:
    """브리프의 기능명 후보(카피 호출에서 받은 것)를 돌려준다. 세트 크기보다 적으면 제안 호출 1회로 받아 브리프에 저장한다
    (svg 방식은 도형까지 받는다). 이후에는 호출 없이 재사용한다."""
    cands = _dedupe(_candidates_from_brief(d, forbidden))
    if len(cands) >= icons.COUNT and (render != "svg" or all(c.get("elements") for c in cands)):
        return cands
    try:
        got = (icons.propose_and_draw(brand.name, d.brief, style, forbidden) if render == "svg"
               else icons.propose3d(brand.name, d.brief, forbidden))
    except Exception as e:
        raise HTTPException(502, f"기능명 제안에 실패했습니다 ({type(e).__name__})")
    cands = _dedupe([c for c in got if str(c.get("name") or "").strip()])
    d.brief = {**(d.brief or {}), "icon_candidates": cands}
    db.flush()
    return cands


@router.get("/designs/{design_id}/icons/candidates")
def icon_candidates(design_id: int, db: Session = Depends(get_db)):
    """아이콘 기능명 후보 목록(렌더 전, 비용 없음). 이미 렌더된 이름은 rendered=True. 후보가 없으면 제안 호출 1회로 채운다.
    사용자는 여기서 고른 것만 렌더한다(POST /icons 의 names)."""
    d = _design(db, design_id)
    bv = db.get(BrandVersion, d.brand_version_id)
    brand = db.get(Brand, d.brand_id)
    forbidden = phrases.effective(bv, d.brief)["forbidden_phrases"]
    had = len(_candidates_from_brief(d, forbidden))
    cands = _ensure_candidates(db, d, brand, _style(bv), forbidden, icons.RENDER)
    db.commit()
    done = {a.name for a in version_icon_set(db, d.brand_version_id)}
    return {"candidates": [{"name": c["name"], "concept": c.get("concept") or "", "rendered": c["name"] in done} for c in cands],
            "source": "copy" if had >= icons.COUNT else "proposal", "target": icons.COUNT, "rendered": len(done)}


@router.post("/designs/{design_id}/icons")
def create_icons(design_id: int, names: list[str] | None = Body(None, embed=True), db: Session = Depends(get_db)):
    """아이콘 세트 생성. names 를 주면 후보 중 그 이름만 렌더한다(세트가 있으면 이어 붙임, 이미지 1개당 1장 비용).
    names 가 없으면 전처럼 후보에서 COUNT 개를 한 번에 만든다(규칙 버전당 한 세트)."""
    d = _design(db, design_id)
    existing = version_icon_set(db, d.brand_version_id)
    # 규칙 버전마다 한 세트만: 이미 있으면 새로 만들지 않는다 (개별 아이콘은 다시 그릴 수 있다). 고른 이름 추가는 허용
    if existing and names is None:
        raise HTTPException(409, "이 브랜드 규칙 버전의 아이콘 세트는 이미 있습니다. 개별 아이콘은 다시 그릴 수 있습니다.")
    if d.kind == "광고 이미지":
        has_image = db.scalar(select(func.count()).select_from(GeneratedImage).where(
            GeneratedImage.design_id == d.id, GeneratedImage.kind == "composite"))
        if not has_image:
            raise HTTPException(400, "광고 이미지를 먼저 생성한 뒤 아이콘 세트를 만들 수 있습니다")
    bv = db.get(BrandVersion, d.brand_version_id)
    brand = db.get(Brand, d.brand_id)
    style, (w, h) = _style(bv), _size(d)
    forbidden = phrases.effective(bv, d.brief)["forbidden_phrases"]
    _check_daily_limit()
    if names is not None:
        made, skipped = _create_picked(db, d, bv, brand, style, w, h, forbidden, names, existing)
    else:
        make = _create_3d if icons.RENDER == "3d" else _create_svg
        made, skipped = make(db, d, bv, brand, style, w, h, forbidden)
    _fail_if_none(made, skipped)
    invalidate_icon_reviews(db, bv.id)
    db.commit()
    return _set_result(db, design_id, made, skipped)


def _create_picked(db, d, bv, brand, style, w, h, forbidden, names: list[str], existing) -> tuple[int, list[dict]]:
    """후보 중 names 에 든 것만 렌더한다. 이미 세트에 있는 이름은 건너뛰고, 후보에 없는 이름은 400."""
    render = (existing[0].style or {}).get("render", icons.RENDER) if existing else icons.RENDER
    if existing:
        src = db.get(Design, existing[0].design_id) or d
        w, h = existing[0].width, existing[0].height
        batch, start = existing[0].batch_no, max(a.position for a in existing) + 1
    else:
        src, batch, start = d, 1, 1
    cands = {c["name"]: c for c in _ensure_candidates(db, d, brand, style, forbidden, render)}
    wanted = list(dict.fromkeys(str(n).strip() for n in names if str(n).strip()))
    unknown = [n for n in wanted if n not in cands]
    if unknown:
        raise HTTPException(400, f"후보에 없는 기능명입니다: {unknown}")
    done = {a.name for a in existing}
    items = [cands[n] for n in wanted if n not in done]
    if not items:
        raise HTTPException(409, "고른 기능명이 모두 이미 세트에 있습니다")
    room = icons.COUNT - len(existing)
    if len(items) > room:
        raise HTTPException(400, f"세트는 {icons.COUNT}개까지입니다. 지금 {len(items)}개를 더 고르면 넘칩니다(남은 자리 {max(room, 0)}개)")
    if render == "3d":
        return _render_save_3d(db, src, bv, items, style, w, h, forbidden, batch, start)
    return _draw_save_svg(db, src, bv, brand, items, style, w, h, forbidden, batch, start)


def _set_result(db, design_id: int, made: int, skipped: list[dict]) -> dict:
    lst = list_icons(design_id, db)
    return {"icons": lst, "requested": icons.COUNT, "made": made, "skipped": skipped,
            "complete": len(lst) >= icons.COUNT, "render": icons.RENDER}


@router.post("/designs/{design_id}/icons/fill")
def fill_icons(design_id: int, db: Session = Depends(get_db)):
    """세트가 목표 개수(10)에 못 미칠 때(크레딧 소진 등) 빠진 만큼만 채운다.
    현재 기능명과 겹치지 않는 기능을 제안받아 같은 세트(batch)·같은 스타일로 이어서 렌더한다. 아이콘 1개당 이미지 1장 비용."""
    d = _design(db, design_id)
    cur = version_icon_set(db, d.brand_version_id)
    if not cur:
        raise HTTPException(409, "이 브랜드 규칙 버전에는 아직 아이콘 세트가 없습니다. 먼저 세트를 만들어 주세요.")
    need = icons.COUNT - len(cur)
    if need <= 0:
        raise HTTPException(409, f"아이콘 세트가 이미 {len(cur)}개로 채워져 있습니다")
    bv = db.get(BrandVersion, d.brand_version_id)
    brand = db.get(Brand, d.brand_id)
    src = db.get(Design, cur[0].design_id) or d      # 세트를 만든 캠페인의 설명·크기 기준으로 이어 만든다
    style, (w, h) = _style(bv), (cur[0].width, cur[0].height)
    forbidden = phrases.effective(bv, src.brief)["forbidden_phrases"]
    render = (cur[0].style or {}).get("render", "svg")
    names = [a.name for a in cur]
    batch, start = cur[0].batch_no, max(a.position for a in cur) + 1
    if render == "3d":
        _check_daily_limit()
    try:
        if render == "3d":
            items = icons.propose3d_more(brand.name, src.brief, forbidden, names, need)
        else:
            items = icons.propose_more(brand.name, src.brief, style, forbidden, names, need)
    except Exception as e:
        raise HTTPException(502, f"기능명 제안에 실패했습니다 ({type(e).__name__})")
    items = [it for it in _dedupe(items) if str(it.get("name") or "").strip() not in names][:need]
    if not items:
        raise HTTPException(502, "겹치지 않는 기능명을 받지 못했습니다")
    if render == "3d":
        made, skipped = _render_save_3d(db, src, bv, items, style, w, h, forbidden, batch, start)
    else:
        made, skipped = _draw_save_svg(db, src, bv, brand, items, style, w, h, forbidden, batch, start)
    _fail_if_none(made, skipped)
    invalidate_icon_reviews(db, bv.id)
    db.commit()
    return _set_result(db, design_id, made, skipped)


@router.post("/designs/{design_id}/icons/{icon_id}/regenerate")
def regenerate_icon(design_id: int, icon_id: int, db: Session = Depends(get_db)):
    """한 아이콘만 다시 그린다. 스타일은 세트와 동일하게(3d: 같은 스타일 블록, svg: 코드 강제) 유지한다."""
    d = _design(db, design_id)
    a = db.get(IconAsset, icon_id)
    if not a or a.brand_version_id != d.brand_version_id:
        raise HTTPException(404, "아이콘을 찾을 수 없습니다")
    bv = db.get(BrandVersion, a.brand_version_id)
    brand = db.get(Brand, d.brand_id)
    src = db.get(Design, a.design_id) or d      # 세트를 만든 캠페인의 설명 기준으로 다시 그린다
    style, (w, h) = _style(bv), (a.width, a.height)
    forbidden = phrases.effective(bv, src.brief)["forbidden_phrases"]
    if (a.style or {}).get("render") == "3d":
        palette = _palette(bv)
        pr = icons.render_prompt(a.name, a.concept, icons.style_vars(style, palette, bv.image_tone or {}))
        try:
            im = icons.render3d(pr)
            _save3d(db, d, bv, a.batch_no, a.position, a.name, a.concept, pr, im, style, palette, w, h, forbidden, existing=a)
        except imagegen.QuotaExceeded as e:
            raise HTTPException(429, str(e))
        except Exception as e:
            raise HTTPException(502, f"아이콘 다시 그리기에 실패했습니다 ({type(e).__name__})")
    else:
        try:
            els = icons.redraw_one(brand.name, src.brief, a.name, a.concept, style)
            _save(db, d, bv, a.batch_no, a.position, a.name, a.concept, els, style, w, h, forbidden, existing=a)
        except Exception as e:
            raise HTTPException(502, f"아이콘 다시 그리기에 실패했습니다 ({type(e).__name__})")
    invalidate_icon_reviews(db, bv.id)
    db.commit()
    return _out(a)


@router.get("/icons/{icon_id}/svg")
def icon_svg(icon_id: int, db: Session = Depends(get_db)):
    a = db.get(IconAsset, icon_id)
    if not a or not a.svg:
        raise HTTPException(404, "SVG 가 없는 아이콘입니다 (3D 아이콘은 PNG 로 제공)" if a else "아이콘을 찾을 수 없습니다")
    return Response(a.svg, media_type="image/svg+xml")


@router.get("/icons/{icon_id}/png")
def icon_png(icon_id: int, db: Session = Depends(get_db)):
    a = db.get(IconAsset, icon_id)
    if not a or not Path(a.png_path).exists():
        raise HTTPException(404, "아이콘을 찾을 수 없습니다")
    return FileResponse(a.png_path, media_type="image/png")


@router.get("/icons/{icon_id}/source")
def icon_source(icon_id: int, db: Session = Depends(get_db)):
    """3D 아이콘의 1024 원본(투명 PNG)."""
    a = db.get(IconAsset, icon_id)
    if not a or not a.source_path or not Path(a.source_path).exists():
        raise HTTPException(404, "원본 파일이 없는 아이콘입니다")
    return FileResponse(a.source_path, media_type="image/png")
