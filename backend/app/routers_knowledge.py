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

from . import knowledge, products_import
from .models import Asset, Brand, BrandGroup, CopyVariant, Design, KnowledgeChunk, KnowledgeDoc
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
    docs = [d for d in docs if d.layer in ("규제", "트렌드") or (d.layer == "그룹" and d.group_id == group_id) or (d.brand_id in bids)]
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
    elif layer not in ("규제", "트렌드"):      # 규제·트렌드는 브랜드·그룹과 무관한 공통 층
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
             "chunks": chunk_counts.get(d.id, 0), "archived": archived.get(d.source_id, 0), "used_designs": len(used.get(d.id, ())),
             # 제품 층: 브랜드 탭 "제품에서 시작" 카드가 쓰는 메타(제품 이미지·라인·용량)
             "image_asset_id": (d.visual or {}).get("image_asset_id"), "line": (d.visual or {}).get("line"), "quantity": (d.visual or {}).get("quantity")}
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
                    "motifs": visual.get("motifs"), "directions": visual.get("directions") or [],
                    "image_asset_id": visual.get("image_asset_id"), "price": visual.get("price"), "quantity": visual.get("quantity"),
                    "line": visual.get("line"), "brand_id": doc.brand_id},
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
                           embedder=knowledge.embed, industries=industries or None)      # 호출 시점의 embed(테스트가 바꿔 끼운다)
    return {"query": q, "industries": industries, "law_all": [r["chunk_id"] for r in knowledge.law_rows(db, industries)],
            "layers": [{"layer": layer, "k": knowledge.LAYER_K.get(layer), "rows": [
                {k: r[k] for k in ("chunk_id", "doc_id", "source_id", "doc_title", "section", "score", "exact_product", "product_name")}
                for r in rows]} for layer, rows in res.items()],
            "mode": knowledge.SEARCH_MODE, "hybrid": knowledge.HYBRID}


# ---- 제품 가져오기 (공식 사이트 → 제품 지식 + 제품 이미지) ----

@router.get("/products/discover")
def products_discover(brand_id: int, db: Session = Depends(get_db)):
    """브랜드 사이트의 제품 목록(과금 없음). 이미 가져온 제품은 doc_id 가 붙는다. 사이트 규칙이 없는 브랜드는 supported=false."""
    brand = db.get(Brand, brand_id)
    if not brand:
        raise HTTPException(404, "브랜드가 없습니다")
    cfg = products_import.SITES.get(brand.name)
    if not cfg:
        return {"supported": False, "brand": brand.name, "products": []}
    try:
        found = products_import.discover(brand.name)
    except Exception as e:
        raise HTTPException(502, f"제품 목록을 읽지 못했습니다: {type(e).__name__}")
    have = products_import.imported(db, brand.id)
    return {"supported": True, "brand": brand.name, "list_url": cfg["list_url"],
            "products": [{"seq": f.seq, "name": f.name, "line": f.line, "price": f.price,
                          "doc_id": have.get(f"{cfg['source_prefix']}_{f.seq}")} for f in found]}


class ImportIn(BaseModel):
    brand_id: int
    seqs: list[str]          # 한 번에 몇 개씩(서버리스 시간 제한). 프론트가 나눠 부른다


@router.post("/products/import")
def products_import_run(body: ImportIn, db: Session = Depends(get_db)):
    """seqs 의 제품을 가져와 제품 지식(조각 1개)과 제품 이미지 자산으로 등록한다(제품당 임베딩 1회). 제품별 성공·문제를 돌려준다."""
    brand = db.get(Brand, body.brand_id)
    if not brand:
        raise HTTPException(404, "브랜드가 없습니다")
    if brand.name not in products_import.SITES:
        raise HTTPException(400, f"'{brand.name}' 은 제품 가져오기를 지원하지 않습니다")
    if not body.seqs or len(body.seqs) > 10:
        raise HTTPException(400, "한 번에 1~10개")
    return {"results": products_import.import_products(db, brand, body.seqs, embedder=knowledge.embed)}


class CampaignFromProductIn(BaseModel):
    doc_id: int
    key_message: str = ""
    target: str = ""


def _product_doc(db: Session, doc_id: int) -> tuple[KnowledgeDoc, Brand]:
    doc = db.get(KnowledgeDoc, doc_id)
    if not doc or doc.layer != "제품" or doc.brand_id is None:
        raise HTTPException(404, "제품 지식이 아닙니다")
    brand = db.get(Brand, doc.brand_id)
    if not brand or not brand.current_version_id:
        raise HTTPException(400, "브랜드를 찾을 수 없습니다")
    return doc, brand


def _product_brief(db: Session, doc: KnowledgeDoc, key_message: str = "", target: str = "") -> dict:
    """제품 조각 하나로 브리프를 채운다: 상품명·설명·제품 이미지는 제품 지식에서, 핵심 메시지·타깃은 받은 값."""
    ch = db.scalar(select(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc.id).order_by(KnowledgeChunk.seq))
    visual = doc.visual or {}
    asset_id = visual.get("image_asset_id")
    if asset_id is not None:
        a = db.get(Asset, asset_id)
        if not a or a.kind != "product":
            asset_id = None
    text = (ch.text if ch else "").replace(f"[{doc.title}] ", "")
    description = text.split("기능성 화장품 표시 여부")[0].strip()[:1000]
    return {"product_name": (ch.product_name if ch else None) or doc.title.replace(" 제품 정보", ""),
            "description": description, "key_message": key_message[:200], "target": target[:300],
            "product_image_asset_id": asset_id, "image_style": "자동", "product_scale": "크게", "layout": "기본",
            "required_phrases": [], "forbidden_phrases": [], "functional": None,
            "from_product_doc": doc.id}


# 시연 기본 세트(05 시연 시나리오의 지면 6개): 캠페인이 하나도 없을 때 지면 이름으로 찾아 채운다
DEFAULT_PLACEMENTS = ("Instagram 피드", "Instagram 스토리", "Facebook 피드", "디스플레이 네이티브 2:1", "디스플레이 네이티브 1:1", "PMax 가로 1.91:1")


def _demo_set(db: Session) -> list[int]:
    from .models import Placement, PresetVersion
    ids = []
    for name in DEFAULT_PLACEMENTS:
        pl = db.scalar(select(Placement).where(Placement.name == name).order_by(Placement.id))
        if not pl:
            continue
        vs = [v for v in pl.versions if v.active and v.status != "확인 필요"]
        if vs:
            ids.append(max(vs, key=lambda x: x.version_no).id)
    return ids


def _default_presets(db: Session, brand: Brand) -> tuple[list[int], str]:
    """제품에서 시작할 때의 지면: 이 브랜드의 마지막 캠페인 지면 → 같은 그룹 브랜드의 마지막 캠페인 지면 → 시연 기본 세트(DEFAULT_PLACEMENTS) → 없음.
    반환: (id 목록, 출처 설명)."""
    from .models import PresetVersion
    def pick(brand_ids: list[int]) -> list[int]:
        for d in db.scalars(select(Design).where(Design.brand_id.in_(brand_ids), Design.kind == "광고 이미지").order_by(Design.id.desc())):
            ids = [pid for pid in (d.preset_version_ids or []) if (pv := db.get(PresetVersion, pid)) and pv.active and pv.status != "확인 필요"]
            if ids:
                return ids
        return []
    ids = pick([brand.id])
    if ids:
        return ids, "이 브랜드의 마지막 캠페인 지면"
    if brand.group_id is not None:
        ids = pick([b.id for b in _group_brands(db, brand.group_id)])
        if ids:
            return ids, "같은 그룹 브랜드의 마지막 캠페인 지면"
    ids = _demo_set(db)
    if ids:
        return ids, f"시연 기본 세트 {len(ids)}개"
    return [], "없음"


def _brand_intro(db: Session, brand: Brand) -> str:
    rows = db.execute(select(KnowledgeChunk.text).join(KnowledgeDoc, KnowledgeDoc.id == KnowledgeChunk.doc_id)
                      .where(KnowledgeDoc.brand_id == brand.id, KnowledgeDoc.layer == "브랜드", KnowledgeDoc.status == "사용")
                      .order_by(KnowledgeChunk.id)).all()
    return " ".join(r[0] for r in rows)[:1200]


class QuickstartPreviewIn(BaseModel):
    doc_id: int


@router.post("/products/quickstart/preview")
def quickstart_preview(body: QuickstartPreviewIn, db: Session = Depends(get_db)):
    """제품에서 시작 · 확인 시트의 내용: 제품 자료로 채운 브리프 + 핵심 메시지·타깃 제안(Haiku 1회, 실패하면 빈 값) + 기본 지면.
    캠페인은 아직 만들지 않는다."""
    from . import llm, prompts
    from .models import PresetVersion
    doc, brand = _product_doc(db, body.doc_id)
    brief = _product_brief(db, doc)
    suggested, error = False, None
    try:
        data = llm.chat_json(prompts.render("copy/brief_system"),
                             prompts.render("copy/brief_user", brand_name=brand.name, brand_intro=_brand_intro(db, brand) or "(브랜드 소개 없음)",
                                            product_name=brief["product_name"], product_text=brief["description"]),
                             "copy_brief", llm._obj({"key_message": llm.STR, "target": llm.STR}))
        brief["key_message"] = str(data.get("key_message") or "").strip()[:200]
        brief["target"] = str(data.get("target") or "").strip()[:300]
        suggested = bool(brief["key_message"])
    except Exception as e:
        error = f"제안 실패: {type(e).__name__}"
    ids, source = _default_presets(db, brand)
    presets = []
    for pid in ids:
        pv = db.get(PresetVersion, pid)
        presets.append({"id": pv.id, "platform": pv.placement.platform.name, "name": pv.placement.name,
                        "width": pv.production_width, "height": pv.production_height})
    return {"doc_id": doc.id, "brand_id": brand.id, "brand_name": brand.name, "product_name": brief["product_name"],
            "description": brief["description"], "product_image_asset_id": brief["product_image_asset_id"],
            "key_message": brief["key_message"], "target": brief["target"], "suggested": suggested, "suggest_error": error,
            "suggest_model": llm.chat_model("copy_brief"), "presets": presets, "presets_source": source}


class QuickstartIn(BaseModel):
    doc_id: int
    key_message: str = ""
    target: str = ""
    preset_version_ids: list[int] = []


@router.post("/products/quickstart")
def quickstart(body: QuickstartIn, db: Session = Depends(get_db)):
    """제품에서 시작: 캠페인 생성(제품 브리프 + 지면) → 카피 3안 생성(기존 create_copy) → 1안을 모든 지면에 선택.
    이미지는 프론트가 기존 ④ 흐름("모든 지면 이미지 생성")으로 이어서 만든다. 반환: 시안 id 와 카피 결과."""
    from .routers_copy import create_copy, select_variant
    from .routers_designs import _check_presets
    doc, brand = _product_doc(db, body.doc_id)
    ids = list(dict.fromkeys(body.preset_version_ids))
    if not ids:
        raise HTTPException(400, "지면이 없습니다. 규격·메시지·시안 탭에서 지면을 고른 캠페인이 하나 있으면 그 지면을 기본으로 씁니다")
    _check_presets(ids, db)
    brief = {**_product_brief(db, doc, body.key_message, body.target), "quickstart": True}
    d = Design(brand_id=brand.id, brand_version_id=brand.current_version_id, preset_version_ids=ids, brief=brief, kind="광고 이미지", status="미검수")
    db.add(d)
    db.commit()
    copy = create_copy(d.id, db)
    has = any(g.get("variants") for g in copy.get("groups", []))
    selected = False
    if has:
        select_variant(d.id, 1, db)
        selected = True
    return {"design_id": d.id, "brand_id": brand.id, "product_name": brief["product_name"], "preset_version_ids": ids,
            "copy_failures": copy.get("failures", []), "has_copy": has, "selected_variant": 1 if selected else None,
            "knowledge": copy.get("knowledge")}


@router.post("/products/campaign")
def campaign_from_product(body: CampaignFromProductIn, db: Session = Depends(get_db)):
    """제품 조각 하나로 캠페인(광고 이미지 시안)을 만든다. 상품명·설명·제품 이미지·별칭은 제품 지식에서 채우고,
    핵심 메시지·타깃만 받는다(비워도 된다). 반환: 새 시안 id 와 브랜드 id (프론트가 캠페인 화면으로 연다)."""
    doc, brand = _product_doc(db, body.doc_id)
    brief = _product_brief(db, doc, body.key_message, body.target)
    asset_id = brief["product_image_asset_id"]
    d = Design(brand_id=brand.id, brand_version_id=brand.current_version_id, preset_version_ids=[], brief=brief, kind="광고 이미지", status="미검수")
    db.add(d)
    db.commit()
    return {"design_id": d.id, "brand_id": brand.id, "product_name": brief["product_name"], "product_image_asset_id": asset_id}
