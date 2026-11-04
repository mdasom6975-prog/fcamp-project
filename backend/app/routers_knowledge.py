"""지식 열람 API — 아모레퍼시픽 입장 첫 화면(지식 브라우저). 과금 없는 조회만, 검색 미리보기(/search)만 임베딩 1회.

- GET  /api/knowledge/summary?group_id=    층별 문서·조각 수, 그룹 소속 브랜드별 문서 수, 마지막 등록일
- GET  /api/knowledge/docs?layer=&group_id=&brand_id=   한 층의 사용 중 문서 목록(조각 수·보관된 이전 판 수·쓰인 카피 수)
- GET  /api/knowledge/docs/{id}/chunks      문서의 조각 전부(본문·제품 메타) + 조각마다 '이 조각이 쓰인 캠페인·카피'
- POST /api/knowledge/search               브리프로 층별 검색 미리보기(knowledge.search 그대로, 임베딩 1회 과금)
'쓰인 카피' 는 카피 생성이 브리프에 남긴 refs(brief.knowledge.law_chunk_ids·guide[].chunk_id)를 거꾸로 찾는다. 시안 수가 많지 않아
그룹 소속 브랜드의 시안을 한 번 읽어 파이썬에서 모은다(요청마다 한 번).
"""
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import knowledge
from .models import Brand, BrandGroup, CopyVariant, Design, KnowledgeChunk, KnowledgeDoc
from .routers_assets import get_db

router = APIRouter(prefix="/api/knowledge")

LAYER_ORDER = list(knowledge.LAYERS)      # 규제 · 그룹 · 브랜드 · 제품 · 캠페인


def _group_brands(db: Session, group_id: int | None) -> list[Brand]:
    if group_id is None:
        return []
    return list(db.scalars(select(Brand).where(Brand.group_id == group_id).order_by(Brand.id)))


def _usage(db: Session, brand_ids: list[int]) -> dict[int, list[dict]]:
    """조각 id → 그 조각을 근거로 쓴 캠페인(시안)들. 시안의 brief.knowledge 에 남은 refs 를 거꾸로 읽는다."""
    out: dict[int, list[dict]] = defaultdict(list)
    if not brand_ids:
        return out
    brands = {b.id: b.name for b in db.scalars(select(Brand).where(Brand.id.in_(brand_ids)))}
    designs = list(db.scalars(select(Design).where(Design.brand_id.in_(brand_ids)).order_by(Design.id.desc())))
    if not designs:
        return out
    # 시안별 대표 카피(가장 최근 차수의 1번 시안)와 위반 수
    copies: dict[int, dict] = {}
    for cv in db.scalars(select(CopyVariant).where(CopyVariant.design_id.in_([d.id for d in designs]))
                         .order_by(CopyVariant.design_id, CopyVariant.batch_no.desc(), CopyVariant.variant_no)):
        cur = copies.get(cv.design_id)
        if cur is None:
            copies[cv.design_id] = {"headline": cv.headline, "violations": len(cv.violations or []), "count": 1,
                                    "blocked": any((v.get("severity") in ("필수", "수정 필요")) for v in (cv.violations or []))}
        else:
            cur["count"] += 1
    for d in designs:
        k = (d.brief or {}).get("knowledge") or {}
        if not k:
            continue
        info = {"design_id": d.id, "brand": brands.get(d.brand_id, ""), "product_name": (d.brief or {}).get("product_name") or "",
                "status": d.status, "created_at": d.created_at.isoformat(timespec="minutes") if d.created_at else None,
                **(copies.get(d.id) or {"headline": "", "violations": 0, "count": 0, "blocked": False})}
        for cid in k.get("law_chunk_ids", []):
            out[cid].append({**info, "role": "규제"})
        for g in k.get("guide", []):
            out[g["chunk_id"]].append({**info, "role": "근거", "score": g.get("score")})
    return out


@router.get("/summary")
def summary(group_id: int | None = None, db: Session = Depends(get_db)):
    group = db.get(BrandGroup, group_id) if group_id is not None else None
    brands = _group_brands(db, group_id)
    bids = [b.id for b in brands]
    # 이 그룹이 보는 문서: 규제(공통) + 그룹 자료 + 소속 브랜드의 브랜드·제품·캠페인
    docs = list(db.scalars(select(KnowledgeDoc).where(KnowledgeDoc.status == "사용")))
    docs = [d for d in docs if d.layer == "규제" or (d.layer == "그룹" and d.group_id == group_id) or (d.brand_id in bids)]
    chunk_counts = dict(db.execute(select(KnowledgeChunk.doc_id, func.count()).where(
        KnowledgeChunk.doc_id.in_([d.id for d in docs]) if docs else False).group_by(KnowledgeChunk.doc_id)).all())
    layers = []
    for layer in LAYER_ORDER:
        ds = [d for d in docs if d.layer == layer]
        layers.append({"layer": layer, "docs": len(ds), "chunks": sum(chunk_counts.get(d.id, 0) for d in ds)})
    per_brand = [{"id": b.id, "name": b.name, "docs": sum(1 for d in docs if d.brand_id == b.id)} for b in brands]
    last = max((d.created_at for d in docs), default=None)
    return {"group": {"id": group.id, "name": group.name} if group else None, "brands": per_brand, "layers": layers,
            "docs": len(docs), "chunks": sum(chunk_counts.values()), "embed_model": knowledge.EMBED_MODEL,
            "last_registered": last.date().isoformat() if last else None}


@router.get("/docs")
def docs(layer: str, group_id: int | None = None, brand_id: int | None = None, db: Session = Depends(get_db)):
    if layer not in LAYER_ORDER:
        raise HTTPException(400, f"layer 는 {LAYER_ORDER} 중 하나")
    brands = _group_brands(db, group_id)
    bids = [b.id for b in brands]
    names = {b.id: b.name for b in brands}
    q = select(KnowledgeDoc).where(KnowledgeDoc.layer == layer).order_by(KnowledgeDoc.id)
    if layer == "그룹":
        q = q.where(KnowledgeDoc.group_id == group_id)
    elif layer != "규제":
        q = q.where(KnowledgeDoc.brand_id.in_(bids) if bids else False)
        if brand_id is not None:
            q = q.where(KnowledgeDoc.brand_id == brand_id)
    rows = list(db.scalars(q))
    active = [d for d in rows if d.status == "사용"]
    archived = defaultdict(int)
    for d in rows:
        if d.status != "사용":
            archived[d.source_id] += 1
    ids = [d.id for d in active]
    chunk_counts = dict(db.execute(select(KnowledgeChunk.doc_id, func.count()).where(KnowledgeChunk.doc_id.in_(ids) if ids else False)
                                   .group_by(KnowledgeChunk.doc_id)).all())
    usage = _usage(db, bids)
    chunk_docs = dict(db.execute(select(KnowledgeChunk.id, KnowledgeChunk.doc_id).where(KnowledgeChunk.doc_id.in_(ids) if ids else False)).all())
    used = defaultdict(set)
    for cid, uses in usage.items():
        if cid in chunk_docs:
            used[chunk_docs[cid]].update(u["design_id"] for u in uses)
    return [{"id": d.id, "source_id": d.source_id, "title": d.title, "layer": d.layer, "brand_id": d.brand_id,
             "brand": names.get(d.brand_id), "industry": d.industry, "status": d.status, "source_url": d.source_url,
             "fetched_at": d.fetched_at, "created_at": d.created_at.date().isoformat() if d.created_at else None,
             "chunks": chunk_counts.get(d.id, 0), "archived": archived.get(d.source_id, 0), "used_designs": len(used.get(d.id, ()))}
            for d in active]


@router.get("/docs/{doc_id}/chunks")
def chunks(doc_id: int, group_id: int | None = None, db: Session = Depends(get_db)):
    doc = db.get(KnowledgeDoc, doc_id)
    if not doc:
        raise HTTPException(404, "문서가 없습니다")
    brands = _group_brands(db, group_id)
    usage = _usage(db, [b.id for b in brands])
    rows = list(db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id).order_by(KnowledgeChunk.seq)))
    visual = doc.visual or {}
    return {"doc": {"id": doc.id, "title": doc.title, "layer": doc.layer, "source_id": doc.source_id, "source_url": doc.source_url,
                    "fetched_at": doc.fetched_at, "industry": doc.industry, "status": doc.status,
                    "created_at": doc.created_at.date().isoformat() if doc.created_at else None,
                    "motifs": visual.get("motifs"), "directions": visual.get("directions") or []},
            "chunks": [{"id": c.id, "seq": c.seq, "section": c.section, "text": c.text, "chars": len(c.text),
                        "product_name": c.product_name, "aliases": c.aliases or [], "functional": c.functional or [],
                        "embed_model": c.embed_model, "has_vector": c.vec is not None,
                        "used": usage.get(c.id, [])} for c in rows]}


class SearchIn(BaseModel):
    group_id: int | None = None
    brand_id: int | None = None
    product_name: str | None = None
    description: str = ""
    key_message: str = ""
    target: str = ""


@router.post("/search")
def search(body: SearchIn, db: Session = Depends(get_db)):
    """검색 미리보기: 브리프를 넣으면 카피 생성이 보는 것과 같은 층별 검색 결과(규제는 업종 감지 + 검색 둘 다 보여 준다)."""
    brief = {"product_name": body.product_name or "", "description": body.description, "key_message": body.key_message, "target": body.target}
    q = knowledge.query_text(brief)
    if not q:
        raise HTTPException(400, "브리프가 비어 있습니다")
    from . import legal
    industries = legal.detect(legal.source_text(brief))
    res = knowledge.search(db, q, brand_id=body.brand_id, group_id=body.group_id, product_name=body.product_name or None,
                           embedder=knowledge.embed)      # 호출 시점의 embed(테스트가 바꿔 끼운다)
    return {"query": q, "industries": industries, "law_all": [r["chunk_id"] for r in knowledge.law_rows(db, industries)],
            "layers": [{"layer": layer, "k": knowledge.LAYER_K.get(layer), "rows": [
                {k: r[k] for k in ("chunk_id", "doc_id", "source_id", "doc_title", "section", "score", "exact_product", "product_name")}
                for r in rows]} for layer, rows in res.items()],
            "mode": knowledge.SEARCH_MODE, "hybrid": knowledge.HYBRID}
