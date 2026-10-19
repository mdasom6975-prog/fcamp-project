import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import icons, imagegen, prompts
from .db import STORAGE_DIR
from .models import Brand, BrandVersion, Design, GeneratedImage, IconAsset
from .routers_assets import get_db
from .routers_review import invalidate_reviews

router = APIRouter(prefix="/api")
WEB_SIZE = (256, 256)   # 웹용 아이콘 기본 크기 (사용자 결정)


def _design(db: Session, design_id: int) -> Design:
    d = db.get(Design, design_id)
    if not d:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")
    return d


def _version_set(db: Session, version_id: int) -> list[IconAsset]:
    """브랜드 규칙 버전 하나에는 아이콘 세트가 한 세트만 있다. (가장 최근 차수의 세트)"""
    last = db.scalar(select(func.max(IconAsset.batch_no)).where(IconAsset.brand_version_id == version_id))
    if not last:
        return []
    return list(db.scalars(select(IconAsset).where(IconAsset.brand_version_id == version_id, IconAsset.batch_no == last)
                           .order_by(IconAsset.position)).all())


def _size(d: Design) -> tuple[int, int]:
    ic = (d.brief or {}).get("icon") or {}
    if ic.get("purpose") == "사용자 지정" and ic.get("width") and ic.get("height"):
        return int(ic["width"]), int(ic["height"])
    return WEB_SIZE


def _style(bv: BrandVersion) -> dict:
    st = bv.icon_style or {}
    color = next((c["hex"] for c in (bv.colors or []) if c.get("role") == "주조색"), "#000000")
    return {"stroke_width": float(st.get("stroke_width") or 2), "corner": st.get("corner") or "둥근",
            "fill": st.get("fill") or "윤곽", "color": color}


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
          "corner": style["corner"], "colors": [c["hex"] for c in palette] or [style["color"]],
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
    return [_out(a) for a in _version_set(db, d.brand_version_id)]


@router.get("/icon-settings")
def icon_settings():
    """프론트 안내용: 현재 렌더 방식·모델·품질."""
    return {"render": icons.RENDER, "model": icons.MODEL_3D, "quality": icons.QUALITY_3D, "count": icons.COUNT}


def _create_svg(db, d, bv, brand, style, w, h, forbidden) -> tuple[int, list[dict]]:
    try:
        items = icons.propose_and_draw(brand.name, d.brief, style, forbidden)
    except Exception as e:
        raise HTTPException(502, f"아이콘 생성에 실패했습니다 ({type(e).__name__})")
    items = _fill(items, lambda names, need: icons.propose_more(brand.name, d.brief, style, forbidden, names, need))
    made, skipped = 0, []
    for i, it in enumerate(items, 1):
        name = str(it.get("name") or "").strip()
        try:
            if not name:
                raise ValueError("기능명이 없습니다")
            _save(db, d, bv, 1, i, name, str(it.get("concept") or ""), it.get("elements"), style, w, h, forbidden)
            made += 1
        except Exception:
            # 도형이 잘못된 아이콘은 한 번 다시 그려 본다
            try:
                els = icons.redraw_one(brand.name, d.brief, name or f"아이콘 {i}", str(it.get("concept") or ""), style)
                _save(db, d, bv, 1, i, name or f"아이콘 {i}", str(it.get("concept") or ""), els, style, w, h, forbidden)
                made += 1
            except Exception as e2:
                skipped.append({"position": i, "name": name, "reason": str(e2)[:80]})
    return made, skipped


def _create_3d(db, d, bv, brand, style, w, h, forbidden) -> tuple[int, list[dict]]:
    try:
        items = icons.propose3d(brand.name, d.brief, forbidden)
    except Exception as e:
        raise HTTPException(502, f"기능명 제안에 실패했습니다 ({type(e).__name__})")
    items = _fill(items, lambda names, need: icons.propose3d_more(brand.name, d.brief, forbidden, names, need))
    items = [it for it in items if str(it.get("name") or "").strip()]
    if not items:
        raise HTTPException(502, "기능명을 받지 못했습니다")
    palette = _palette(bv)
    sv = icons.style_vars(style, palette, bv.image_tone or {})
    plist = [icons.render_prompt(str(it["name"]).strip(), str(it.get("concept") or ""), sv) for it in items]
    results = icons.render_many(plist)      # 이미지 모델 호출은 동시에, DB 저장은 이 스레드에서
    made, skipped = 0, []
    for i, (it, pr, res) in enumerate(zip(items, plist, results), 1):
        name = str(it["name"]).strip()
        if isinstance(res, Exception):
            reason = str(res) if isinstance(res, imagegen.QuotaExceeded) else f"렌더 실패 ({type(res).__name__})"
            skipped.append({"position": i, "name": name, "reason": reason[:120]})
            continue
        try:
            _save3d(db, d, bv, 1, i, name, str(it.get("concept") or ""), pr, res, style, palette, w, h, forbidden)
            made += 1
        except Exception as e:
            skipped.append({"position": i, "name": name, "reason": str(e)[:80]})
    return made, skipped


@router.post("/designs/{design_id}/icons")
def create_icons(design_id: int, db: Session = Depends(get_db)):
    d = _design(db, design_id)
    # 규칙 버전마다 한 세트만: 이미 있으면 새로 만들지 않는다 (개별 아이콘은 다시 그릴 수 있다)
    if _version_set(db, d.brand_version_id):
        raise HTTPException(409, "이 브랜드 규칙 버전의 아이콘 세트는 이미 있습니다. 개별 아이콘은 다시 그릴 수 있습니다.")
    if d.kind == "광고 이미지":
        has_image = db.scalar(select(func.count()).select_from(GeneratedImage).where(
            GeneratedImage.design_id == d.id, GeneratedImage.kind == "composite"))
        if not has_image:
            raise HTTPException(400, "광고 이미지를 먼저 생성한 뒤 아이콘 세트를 만들 수 있습니다")
    bv = db.get(BrandVersion, d.brand_version_id)
    brand = db.get(Brand, d.brand_id)
    style, (w, h) = _style(bv), _size(d)
    forbidden = bv.forbidden_phrases or []
    if icons.RENDER == "3d" and imagegen.usage("openai")["exhausted"]:
        raise HTTPException(429, f"오늘 설정한 이미지 생성 한도({imagegen.DAILY_LIMIT}회)를 모두 사용했습니다")
    make = _create_3d if icons.RENDER == "3d" else _create_svg
    made, skipped = make(db, d, bv, brand, style, w, h, forbidden)
    if made == 0 and skipped:   # 하나도 못 만들었으면 성공 응답 대신 원인을 돌려준다
        reason = skipped[0]["reason"]
        raise HTTPException(429 if "한도" in reason or "크레딧" in reason else 502, f"아이콘 생성에 실패했습니다: {reason}")
    invalidate_reviews(db, d.id)
    db.commit()
    return {"icons": list_icons(design_id, db), "requested": icons.COUNT, "made": made, "skipped": skipped,
            "complete": made >= icons.COUNT, "render": icons.RENDER}


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
    forbidden = bv.forbidden_phrases or []
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
    invalidate_reviews(db, d.id)
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
