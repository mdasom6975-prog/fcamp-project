"""분석 화면(학습·개발용): 한 캠페인의 입력↔결과 대조, 보낸 프롬프트 전문, 단계별 비용을 한 번에 돌려준다. 검수와 무관."""
from fastapi import APIRouter, Body, Depends, HTTPException
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import analysis, calllog, storage
from .models import ArchivedImage, Brand, BrandVersion, GeneratedImage
from .routers_assets import get_db, get_design

router = APIRouter(prefix="/api", dependencies=[Depends(calllog.track_design)])   # 채점 호출 기록에 시안 id


@router.get("/designs/{design_id}/analysis")
def get_analysis(design_id: int, db: Session = Depends(get_db)):
    return analysis.build(db, get_design(db, design_id))


@router.post("/designs/{design_id}/analysis/judge")
def judge(design_id: int, background_id: int | None = Body(None, embed=True), db: Session = Depends(get_db)):
    """배경 1장(기본: 가장 최근 배경)을 Claude 가 보고 브랜드 톤·브리프와의 일치를 항목별로 채점한다. 채팅 1회 과금."""
    d = get_design(db, design_id)
    if background_id is not None:
        g = db.get(GeneratedImage, background_id)
        if g is None or g.design_id != d.id or g.kind != "background":
            raise HTTPException(404, "이 캠페인의 배경이 아닙니다")
    else:
        from sqlalchemy import select
        g = db.scalar(select(GeneratedImage).where(GeneratedImage.design_id == d.id, GeneratedImage.kind == "background")
                      .order_by(GeneratedImage.id.desc()).limit(1))
        if g is None:
            raise HTTPException(400, "채점할 배경이 없습니다")
    bv, brand = db.get(BrandVersion, d.brand_version_id), db.get(Brand, d.brand_id)
    try:
        return analysis.judge_background(db, d, bv, brand, g)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:      # 키 등 민감 정보는 메시지에 넣지 않는다
        raise HTTPException(502, f"채점에 실패했습니다 ({type(e).__name__})")


def _archived_out(a: ArchivedImage) -> dict:
    lay = a.layout or {}
    return {"id": a.id, "kind": a.kind, "preset": a.preset_name, "width": a.width, "height": a.height,
            "created_at": a.created_at.isoformat(), "prompt": a.prompt,
            "model": lay.get("model"), "quality": lay.get("quality"), "style": lay.get("style"),
            "mode": lay.get("mode"), "template": lay.get("template"), "provider": lay.get("provider")}


@router.get("/designs/{design_id}/analysis/archived")
def archived_images(design_id: int, db: Session = Depends(get_db)):
    """'시안 이미지 초기화'로 사용자 화면에서 지운 이미지의 보관본(개발자 품질 비교용). 초기화 시점(batch)별로 묶는다.
    배경은 같은 파일을 여러 지면이 공유하므로 묶음 안에서 파일 기준으로 하나만 낸다(합성본은 지면마다)."""
    get_design(db, design_id)
    rows = db.scalars(select(ArchivedImage).where(ArchivedImage.design_id == design_id)
                      .order_by(ArchivedImage.batch.desc(), ArchivedImage.id)).all()
    groups: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()
    for a in rows:
        g = groups.setdefault(a.batch, {"batch": a.batch, "archived_at": a.archived_at.isoformat(), "items": []})
        if a.kind == "background":
            if (a.batch, a.file_path) in seen:
                continue
            seen.add((a.batch, a.file_path))
        g["items"].append(_archived_out(a))
    return {"batches": list(groups.values())}


def _archived(db: Session, image_id: int) -> ArchivedImage:
    a = db.get(ArchivedImage, image_id)
    if not a or not storage.exists(a.file_path):
        raise HTTPException(404, "보관된 이미지를 찾을 수 없습니다")
    return a


@router.get("/archived-images/{image_id}/file")
def archived_file(image_id: int, db: Session = Depends(get_db)):
    return storage.response(_archived(db, image_id).file_path, "image/png")


@router.get("/archived-images/{image_id}/thumb")
def archived_thumb(image_id: int, db: Session = Depends(get_db)):
    from .routers_images import THUMB_SIDE, _thumb_key
    a = _archived(db, image_id)
    out = _thumb_key(a.file_path)
    if not storage.exists(out):
        im = Image.open(storage.local(a.file_path)).convert("RGB")
        im.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
        storage.put_image(out, im, "JPEG", quality=82)
    return storage.response(out, "image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
