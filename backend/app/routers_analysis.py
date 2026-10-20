"""분석 화면(학습·개발용): 한 캠페인의 입력↔결과 대조, 보낸 프롬프트 전문, 단계별 비용을 한 번에 돌려준다. 검수와 무관."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from . import analysis, calllog
from .models import Brand, BrandVersion, Design, GeneratedImage
from .routers_assets import get_db

router = APIRouter(prefix="/api", dependencies=[Depends(calllog.track_design)])   # 채점 호출 기록에 시안 id


def _design(db: Session, design_id: int) -> Design:
    d = db.get(Design, design_id)
    if not d:
        raise HTTPException(404, "캠페인을 찾을 수 없습니다")
    return d


@router.get("/designs/{design_id}/analysis")
def get_analysis(design_id: int, db: Session = Depends(get_db)):
    return analysis.build(db, _design(db, design_id))


@router.post("/designs/{design_id}/analysis/judge")
def judge(design_id: int, background_id: int | None = Body(None, embed=True), db: Session = Depends(get_db)):
    """배경 1장(기본: 가장 최근 배경)을 Claude 가 보고 브랜드 톤·브리프와의 일치를 항목별로 채점한다. 채팅 1회 과금."""
    d = _design(db, design_id)
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
