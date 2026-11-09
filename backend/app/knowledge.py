"""RAG 지식 저장소: 문서를 조각으로 자르고(청킹) 임베딩해 저장하고, 브리프로 층별 관련 조각을 찾는다.

설계: RAG 설계 문서(16, v2.1) 3~5절. 층(layer)은 규제 · 그룹 · 브랜드 · 제품 · 캠페인.
- 다른 브랜드의 조각은 벡터 비교 전에 필터로 뺀다(브랜드 섞임은 유사도가 아니라 필터로 막는다).
- 층마다 따로 상위 k 개를 뽑는다. 한 번에 뽑으면 브랜드 이야기가 규제 조항을 밀어낼 수 있다.
- 임베딩은 pgvector 컬럼(vec, HNSW·코사인)에 두고 검색은 SQL `ORDER BY vec <=> 질의 LIMIT k` 로 한다(2단계, 2026-11).
  KNOWLEDGE_SEARCH=python 이면 예전 경로(JSON embedding 을 전부 읽어 파이썬 코사인)로 돌아간다. 두 경로의 결과는 같아야 한다(테스트).
- 모든 층을 한 번의 질의로 가져오고(왕복 1회), 기본은 하이브리드: 벡터 순위 + pg_trgm word_similarity 순위를 RRF 로 합친다(3단계, 60건 81% → 85%).
- 카피 생성(for_copy)의 규제: 업종이 감지되면 그 업종 규제 조각이 LAW_ALL_MAX 이하일 때는 전부 넣고(브리프와 법령 문장은 말이 달라
  유사도로 놓치기 쉽다, 1-a 평가), 넘으면(3-B 법령 전문 등록 뒤 300여 조각) 업종 필터 + 검색 상위 LAW_K 만 넣는다.
"""
import math
import os
import re
from datetime import date

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import Session

from . import calllog
from .models import KnowledgeChunk, KnowledgeDoc

LAYERS = ("규제", "그룹", "브랜드", "제품", "캠페인", "트렌드")   # 트렌드: 화장품 검색 트렌드(app/trends.py, 주 1회, 브랜드·그룹 무관)
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
LAYER_K = {"규제": 3, "제품": 2, "브랜드": 2, "그룹": 1}     # 검색할 때 층마다 가져올 개수
SEARCH_MODE = os.getenv("KNOWLEDGE_SEARCH", "sql").strip().lower()   # sql(pgvector) | python(JSON 코사인, 되돌리기용)
SQL_CANDIDATES = 40      # SQL 경로가 층마다 가져오는 상위 후보 수(skip·제품명 정확 일치를 파이썬에서 처리할 여유)
HYBRID = os.getenv("KNOWLEDGE_HYBRID", "1").strip() == "1"   # 하이브리드(기본 켬): 벡터 순위 + 글자(pg_trgm) 순위를 RRF 로 합친다. 0 이면 벡터만(3단계 측정: 81% → 85%)
RRF_K = 60               # Reciprocal Rank Fusion 상수. 1/(K+순위) 의 합
KW_SIM = os.getenv("KNOWLEDGE_KW_SIM", "word").strip()   # word(기본, 85%) | strict_word(85%) | similarity(83%) — 60건 측정 2026-11-04
MAX_CHARS = 800          # 이보다 긴 문단은 문장 단위로 더 자른다
EMBED_BATCH = 64


# ── 자르기 ────────────────────────────────────────────────────────────────

_ARTICLE = re.compile(r"(제\d+조(?:의\d+)?\([^)]*\))")
_PARA = re.compile(r"(?=[①②③④⑤⑥⑦⑧⑨⑩])")
_ITEM = re.compile(r"\s(?=\d{1,2}\.\s)")     # 호(1. 2. …) 시작
_AMEND = re.compile(r"<(?:개정|신설|삭제)[^>]*>")


def _items(text: str) -> list[str]:
    """긴 항을 호 단위 묶음으로 자른다. 첫 묶음(항의 머리말)은 뒤 묶음마다 앞에 붙여 '무엇의 목록인지' 를 남긴다."""
    parts = _ITEM.split(text)
    if len(parts) < 2:
        return _split_long(text)
    lead, out, cur = parts[0], [], ""
    for p in parts[1:]:
        if cur and len(lead) + len(cur) + len(p) > MAX_CHARS:
            out.append(f"{lead} {cur}")
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    out.append(f"{lead} {cur}")
    return out


def chunk_law(text: str, law: str) -> list[tuple[str, str]]:
    """법령 본문 → [(절 이름, 조각)]. 조 단위로 자르고, 항(①②…)이 있으면 항마다 한 조각, 항이 길면 호 단위로 더 자른다.
    개정 이력 표시(<개정 2018. 3. 13.>)는 뜻이 없어 지운다.
    조각 앞에 '[법령 제N조(제목)]' 을 붙여 항만 떼어 봐도 어느 조문인지 알 수 있게 한다."""
    parts = _ARTICLE.split(_AMEND.sub("", text))
    out = []
    for i in range(1, len(parts) - 1, 2):
        head, body = parts[i], re.sub(r"\s+", " ", parts[i + 1]).strip()
        no = head.split("(")[0]
        paras = [p.strip() for p in _PARA.split(body) if p.strip()] or [""]
        for p in paras:
            mark = p[0] if p and p[0] in "①②③④⑤⑥⑦⑧⑨⑩" else ""
            sec = f"{law} {no}" + (f" 제{'①②③④⑤⑥⑦⑧⑨⑩'.index(mark) + 1}항" if mark else "")
            pieces = _items(p) if len(p) > MAX_CHARS else [p]
            for j, piece in enumerate(pieces, 1):
                out.append((sec + (f" ({j})" if len(pieces) > 1 else ""), f"[{law} {head}] {piece}".strip()))
    return out


def _split_long(text: str, limit: int = MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for s in re.split(r"(?<=[.!?。다요])\s+", text):
        if cur and len(cur) + len(s) + 1 > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def chunk_paragraphs(text: str, title: str) -> list[tuple[str, str]]:
    """일반 문서 → [(절 이름, 조각)]. 빈 줄로 나눈 문단 단위, 긴 문단은 문장 단위로 더 자른다.
    짧은 문단(제목·한 줄)은 다음 문단에 붙인다. 조각 앞에 '[제목]' 을 붙여 문맥을 남긴다."""
    paras, carry = [], ""
    for p in re.split(r"\n\s*\n", text):
        p = re.sub(r"\s+", " ", p).strip()
        if not p:
            continue
        if len(p) < 40:
            carry = f"{carry} {p}".strip()
            continue
        paras.append(f"{carry} {p}".strip() if carry else p)
        carry = ""
    if len(carry) >= 10:     # 끝에 남은 한두 글자(닫기 버튼 'X' 등)는 버린다
        paras.append(carry)
    out = []
    for i, p in enumerate(paras, 1):
        for j, piece in enumerate(_split_long(p), 1):
            out.append((f"{title} {i}" + (f"-{j}" if j > 1 else ""), f"[{title}] {piece}"))
    return out


_ANNEX_ITEM = re.compile(r"^(\d+)\.\s.*?\s([가-하])\.\s")


def chunk_annex(text: str, name: str) -> list[tuple[str, str]]:
    """법령 별표(목록형 규정) → [(절 이름, 조각)]. 빈 줄로 나눈 문단 하나가 한 목이고, 문단이 '2. 머리말 가. …' 이면
    절 이름을 '별표 5 제2호 가목' 처럼 붙인다(번호만으로는 어느 규정인지 알 수 없다). 그 밖의 문단은 '별표 5 제N호'."""
    out = []
    for p in re.split(r"\n\s*\n", text):
        p = re.sub(r"\s+", " ", p).strip()
        if not p:
            continue
        m = _ANNEX_ITEM.match(p)
        lead = re.match(r"^(\d+)\.\s", p)
        sec = f"{name} 제{m.group(1)}호 {m.group(2)}목" if m else (f"{name} 제{lead.group(1)}호" if lead else name)
        out.append((sec, f"[{name}] {p}"))
    return out


# ── 임베딩 ────────────────────────────────────────────────────────────────

def embed(texts: list[str]) -> list[list[float]]:
    """OpenAI 임베딩. 호출마다 calllog 에 토큰과 함께 기록한다(api=embeddings, kind=knowledge)."""
    from .copywriter import client
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i:i + EMBED_BATCH]
        with calllog.timed("embeddings", "knowledge", "openai", EMBED_MODEL, n=len(batch)) as info:
            info["log_input"] = batch
            r = client().embeddings.create(model=EMBED_MODEL, input=batch)
            info["tokens_in"] = getattr(getattr(r, "usage", None), "prompt_tokens", None)
            info["log_output"] = {"vectors": len(r.data), "dims": len(r.data[0].embedding) if r.data else 0}
        out.extend(d.embedding for d in r.data)
    return out


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ── 등록 ──────────────────────────────────────────────────────────────────

def add_document(db: Session, *, layer: str, source_id: str, title: str, chunks: list[tuple[str, str]],
                 group_id: int | None = None, brand_id: int | None = None, source_url: str = "",
                 fetched_at: str | None = None, product_name: str | None = None,
                 functional: list[str] | None = None, industry: str | None = None, aliases: list[str] | None = None,
                 visual: dict | None = None, embedder=embed) -> KnowledgeDoc:
    """조각을 임베딩해 문서 하나로 저장한다. 같은 source_id 의 '사용' 문서는 '보관' 으로 돌린다(재등록 = 새 버전).
    embedder 는 테스트에서 가짜 임베딩을 넣을 때 바꾼다."""
    if layer not in LAYERS:
        raise ValueError(f"layer 는 {LAYERS} 중 하나: {layer}")
    if not chunks:
        raise ValueError(f"조각이 없습니다: {source_id}")
    vectors = embedder([text for _, text in chunks])     # 네트워크 호출이라 쓰기 전에 끝낸다(SQLite 쓰기 잠금을 오래 잡지 않게)
    for old in db.scalars(select(KnowledgeDoc).where(KnowledgeDoc.source_id == source_id, KnowledgeDoc.status == "사용")):
        old.status = "보관"
    doc = KnowledgeDoc(layer=layer, source_id=source_id, title=title, group_id=group_id, brand_id=brand_id, industry=industry, visual=visual,
                       source_url=source_url, fetched_at=fetched_at or date.today().isoformat())
    db.add(doc)
    db.flush()
    for seq,((section, text), vec) in enumerate(zip(chunks, vectors), 1):
        db.add(KnowledgeChunk(doc_id=doc.id, seq=seq, section=section, text=text, embedding=vec, vec=vec,
                              embed_model=EMBED_MODEL, product_name=product_name, functional=functional or [],
                              aliases=aliases or []))
    db.commit()
    return doc


# ── 검색 ──────────────────────────────────────────────────────────────────

def query_text(brief: dict) -> str:
    """브리프 → 검색 질의. 상품명·설명·핵심 메시지·타깃을 이어 붙인다."""
    keys = ("product_name", "description", "key_message", "target")
    return " / ".join(str(brief[k]).strip() for k in keys if str(brief.get(k) or "").strip())


def _industry_ok(industries: list[str] | None):
    """규제 층 업종 필터: 업종을 주면 그 업종으로 표시된 문서(업종 없는 정의 조문 포함)만. 안 주면 전부."""
    if not industries:
        return None
    return or_(KnowledgeDoc.industry.is_(None), KnowledgeDoc.industry.in_(industries))


def _filtered(layer: str, brand_id: int | None, group_id: int | None, industries: list[str] | None = None):
    """층·브랜드·그룹·임베딩 모델 필터가 걸린 (조각, 문서) 질의. 거를 기준이 없는 층(브랜드 없이 브랜드 층)은 None."""
    q = (select(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
         .where(KnowledgeDoc.layer == layer, KnowledgeDoc.status == "사용", KnowledgeChunk.embed_model == EMBED_MODEL))
    if layer == "규제" and _industry_ok(industries) is not None:
        q = q.where(_industry_ok(industries))
    if layer in ("브랜드", "제품", "캠페인"):
        if brand_id is None:
            return None
        q = q.where(KnowledgeDoc.brand_id == brand_id)
    elif layer == "그룹":
        if group_id is None:
            return None
        q = q.where(KnowledgeDoc.group_id == group_id)
    return q


def _candidates(db: Session, layer: str, brand_id: int | None, group_id: int | None,
                industries: list[str] | None = None) -> list[tuple[KnowledgeChunk, KnowledgeDoc]]:
    """python 경로: 필터에 맞는 조각 전부(점수는 파이썬에서)."""
    q = _filtered(layer, brand_id, group_id, industries)
    return list(db.execute(q).all()) if q is not None else []


def _candidates_sql(db: Session, qv: list[float], query: str, brand_id: int | None, group_id: int | None,
                    layers: list[str], limit: int, hybrid: bool,
                    industries: list[str] | None = None) -> dict[str, list[tuple[KnowledgeChunk, KnowledgeDoc, float]]]:
    """sql 경로: 모든 층을 한 번의 질의로(왕복 1회). 층마다 코사인 거리 순위(window) 상위 limit 개.
    hybrid 면 pg_trgm similarity(조각 글, 질의) 순위도 함께 매겨 두 순위 중 하나라도 limit 안이면 가져오고,
    점수는 RRF = 1/(K+벡터순위) + 1/(K+글자순위). 아니면 점수는 유사도(1 − 거리).
    브랜드·제품·캠페인 층은 brand_id, 그룹 층은 group_id 로 거른다(없으면 그 층은 빈 결과)."""
    layer = KnowledgeDoc.layer
    dist = KnowledgeChunk.vec.cosine_distance(qv)
    law_ok = _industry_ok(industries)
    scope = or_(layer == "규제" if law_ok is None else and_(layer == "규제", law_ok),
                layer == "트렌드",       # 검색 트렌드: 브랜드·그룹 무관 공통 층

                and_(layer.in_(("브랜드", "제품", "캠페인")), KnowledgeDoc.brand_id == brand_id) if brand_id is not None else false(),
                and_(layer == "그룹", KnowledgeDoc.group_id == group_id) if group_id is not None else false())
    cols = [KnowledgeChunk.id.label("cid"), dist.label("dist"),
            func.row_number().over(partition_by=layer, order_by=(dist, KnowledgeChunk.id)).label("rn_vec")]
    if hybrid:
        # 글자 유사도 측정: similarity(조각, 질의) 는 긴 글끼리라 희석된다. word_similarity(질의, 조각) 는 질의의 트라이그램이
        # 조각 안에 얼마나 있는지(질의가 짧을수록 선명). KNOWLEDGE_KW_SIM 으로 고른다(3단계 측정용)
        sim = (func.word_similarity(query, KnowledgeChunk.text) if KW_SIM == "word" else
               func.strict_word_similarity(query, KnowledgeChunk.text) if KW_SIM == "strict_word" else
               func.similarity(KnowledgeChunk.text, query))
        cols.append(func.row_number().over(partition_by=layer, order_by=(sim.desc(), KnowledgeChunk.id)).label("rn_kw"))
    sub = (select(*cols).join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
           .where(KnowledgeDoc.status == "사용", KnowledgeChunk.embed_model == EMBED_MODEL, KnowledgeChunk.vec.isnot(None),
                  layer.in_(layers), scope).subquery())
    keep = sub.c.rn_vec <= limit
    extra = [sub.c.rn_vec]
    if hybrid:
        keep = or_(keep, sub.c.rn_kw <= limit)
        extra.append(sub.c.rn_kw)
    q = (select(KnowledgeChunk, KnowledgeDoc, sub.c.dist, *extra).join(sub, sub.c.cid == KnowledgeChunk.id)
         .join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id).where(keep))
    out: dict[str, list] = {l: [] for l in layers}
    for row in db.execute(q).all():
        ch, doc, d = row[0], row[1], float(row[2])
        score = (1.0 / (RRF_K + row[3]) + 1.0 / (RRF_K + row[4])) if hybrid else 1.0 - d
        out[doc.layer].append((ch, doc, score))
    return out


def _same_name(a: str | None, b: str | None) -> bool:
    return bool(a and b) and re.sub(r"[\s™®]", "", a).lower() == re.sub(r"[\s™®]", "", b).lower()


def _is_product(ch: KnowledgeChunk, name: str | None) -> bool:
    """브리프 상품명이 이 제품 조각의 이름이나 별칭과 같은가(띄어쓰기·상표 기호 무시)."""
    return any(_same_name(name, x) for x in [ch.product_name, *(ch.aliases or [])])


def _exact_products(db: Session, brand_id: int | None, name: str | None) -> list[tuple[KnowledgeChunk, KnowledgeDoc]]:
    """제품명이 정확히 맞는 제품 조각(sql 경로에서 상위 후보에 못 들어도 맨 앞에 두기 위해 따로 찾는다). 한 브랜드의 제품은 많지 않다."""
    if not name:
        return []
    q = _filtered("제품", brand_id, None)
    if q is None:
        return []
    return [(ch, doc) for ch, doc in db.execute(q.where(KnowledgeChunk.product_name.isnot(None))).all() if _is_product(ch, name)]


def search(db: Session, query: str, *, brand_id: int | None, group_id: int | None,
           layer_k: dict[str, int] | None = None, product_name: str | None = None, embedder=embed,
           skip=None, mode: str | None = None, hybrid: bool | None = None,
           industries: list[str] | None = None) -> dict[str, list[dict]]:
    """질의로 층별 상위 조각을 찾는다. 반환: {층: [{chunk_id, doc_title, section, text, score, …}]}.
    규제는 모든 브랜드 공통, 그룹은 group_id, 브랜드·제품은 brand_id 로 거른다.
    product_name 이 제품 조각의 제품명·별칭과 같으면 그 조각을 맨 앞에 둔다(이름이 정확히 맞으면 유사도보다 확실하다).
    skip(조각) 이 참인 조각은 후보에서 뺀다.
    mode: "sql"(기본, pgvector 가 상위 후보를 고른다. 모든 층을 한 질의로) | "python"(조각 전부를 읽어 파이썬 코사인). 환경변수 KNOWLEDGE_SEARCH.
    hybrid: 벡터 순위 + 글자 순위 RRF(sql 경로만). 기본은 환경변수 KNOWLEDGE_HYBRID. 점수 의미가 달라진다(RRF 합, 0.03 안팎).
    industries: 규제 층을 이 업종(과 업종 없는 문서)으로 거른다. 규제가 여러 업종으로 늘어난 뒤(3-B) 식품 조문이 화장품 브리프에 섞이지 않게."""
    layer_k = layer_k or LAYER_K
    mode = (mode or SEARCH_MODE)
    hybrid = HYBRID if hybrid is None else hybrid
    qv = embedder([query])[0]
    out: dict[str, list[dict]] = {}
    by_layer = None
    if mode != "python":
        by_layer = _candidates_sql(db, qv, query, brand_id, group_id, list(layer_k), max(SQL_CANDIDATES, *layer_k.values()), hybrid,
                                   industries)
    for layer, k in layer_k.items():
        if by_layer is None:
            scored_raw = [(ch, doc, cosine(qv, ch.embedding)) for ch, doc in _candidates(db, layer, brand_id, group_id, industries)]
        else:
            scored_raw = by_layer[layer]
            if layer == "제품" and product_name:        # 정확 일치 제품이 상위 후보 밖이어도 맨 앞에 두기 위해 따로 찾는다
                seen = {ch.id for ch, _, _ in scored_raw}
                for ch, doc in _exact_products(db, brand_id, product_name):
                    if ch.id not in seen:
                        d = float(db.scalar(select(KnowledgeChunk.vec.cosine_distance(qv)).where(KnowledgeChunk.id == ch.id)))
                        scored_raw.append((ch, doc, (1.0 / (RRF_K + SQL_CANDIDATES + 1)) * 2 if hybrid else 1.0 - d))
        scored = []
        for ch, doc, s in scored_raw:
            if skip and skip(ch):
                continue
            exact = _is_product(ch, product_name)
            scored.append((exact, s, ch, doc))
        scored.sort(key=lambda x: (x[0], round(x[1], 4), -x[2].id), reverse=True)   # 동점이면 먼저 등록된 조각(두 경로가 같은 순서가 되게)
        out[layer] = [{"chunk_id": ch.id, "doc_id": doc.id, "source_id": doc.source_id, "doc_title": doc.title,
                       "layer": layer, "section": ch.section, "text": ch.text, "score": round(s, 4),
                       "product_name": ch.product_name, "functional": ch.functional, "exact_product": exact,
                       "motifs": (doc.visual or {}).get("motifs")}
                      for exact, s, ch, doc in scored[:k]]
    return out


# ── 카피 생성에 붙이기 ────────────────────────────────────────────────────

GUIDE_K = {k: v for k, v in LAYER_K.items() if k != "규제"}    # 카피에서 검색하는 층(규제는 아래 LAW_* 규칙)
TREND_K = int(os.getenv("KNOWLEDGE_TREND_K", "3"))           # 카피에 넣을 이번 주 검색 트렌드 조각 수(요약 조각 제외, 브리프와 가까운 순)
COPY_TREND = os.getenv("COPY_TREND", "1").strip() != "0"     # 0 이면 트렌드를 카피에 넣지 않는다(있음/없음 비교·되돌리기용)
LAW_K = int(os.getenv("KNOWLEDGE_LAW_K", "6"))               # 규제가 많을 때 카피에 넣을 조각 수(검색 상위)
LAW_ALL_MAX = int(os.getenv("KNOWLEDGE_LAW_ALL_MAX", "24"))  # 업종의 규제 조각이 이 수 이하면 검색 없이 전부 넣는다(작을 때는 전부가 더 확실하다)
_TEST_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*%")


def _test_result(ch: KnowledgeChunk) -> bool:
    """시험·임상 수치가 든 그룹·브랜드 조각. 광고에 근거 주석을 함께 못 넣어 쓸 수 없으니 재료로 고르지 않는다(제품 정보 카드는 예외)."""
    return not ch.product_name and bool(_TEST_NUMBER.search(ch.text))


def _row(ch: KnowledgeChunk, doc: KnowledgeDoc, score: float | None = None) -> dict:
    return {"chunk_id": ch.id, "doc_id": doc.id, "source_id": doc.source_id, "doc_title": doc.title, "layer": doc.layer,
            "section": ch.section, "text": ch.text, "score": None if score is None else round(score, 4),
            "product_name": ch.product_name, "functional": ch.functional, "exact_product": False,
            "motifs": (doc.visual or {}).get("motifs")}


# 핵심 규제: 데이터 양과 상관없이 카피 생성에 항상 들어가는 조문(00 정의서 v2 S4). 업종 → sources.json 의 출처 id.
# 광고 표현을 직접 다루는 조문만: 부당 표시·광고 금지(제13조), 별표 5(준수사항), 기능성 범위(시행규칙 2조), 표시·광고 범위(시행규칙 22조)
CORE_LAW: dict[str, tuple[str, ...]] = {
    "화장품": ("law_cosmetics_13", "law_cosmetics_rule_annex5", "law_cosmetics_rule_2", "law_cosmetics_rule_22"),
}


def core_law_rows(db: Session, industries: list[str]) -> list[dict]:
    """감지된 업종의 핵심 규제 조각 전부(출처 id 로 고른다). 검색·임베딩 호출 없음."""
    ids = [sid for ind in industries for sid in CORE_LAW.get(ind, ())]
    if not ids:
        return []
    q = (select(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
         .where(KnowledgeDoc.layer == "규제", KnowledgeDoc.status == "사용", KnowledgeDoc.source_id.in_(ids))
         .order_by(KnowledgeDoc.id, KnowledgeChunk.seq))
    return [_row(ch, doc) for ch, doc in db.execute(q).all()]


def law_rows(db: Session, industries: list[str]) -> list[dict]:
    """감지된 업종으로 표시한 규제 문서의 조각 전부(문서·조각 순서대로). 검색·임베딩 호출 없음."""
    if not industries:
        return []
    q = (select(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
         .where(KnowledgeDoc.layer == "규제", KnowledgeDoc.status == "사용", KnowledgeDoc.industry.in_(industries))
         .order_by(KnowledgeDoc.id, KnowledgeChunk.seq))
    return [_row(ch, doc) for ch, doc in db.execute(q).all()]


def has_guide(db: Session, brand_id: int | None, group_id: int | None) -> bool:
    """이 브랜드(또는 그룹)에 검색할 지식이 있는가. 없으면 임베딩 호출 자체를 하지 않는다(기존 브랜드는 지금과 같게)."""
    conds = [KnowledgeDoc.brand_id == brand_id] if brand_id is not None else []
    if group_id is not None:
        conds.append(KnowledgeDoc.group_id == group_id)
    if not conds:
        return False
    return db.scalar(select(KnowledgeDoc.id).where(KnowledgeDoc.status == "사용", KnowledgeDoc.layer != "규제", or_(*conds))
                     .limit(1)) is not None


def functional_mismatch(guide: list[dict], functional: list[str] | None) -> str | None:
    """브리프의 기능성 입력과 상품명이 같은 제품 자료의 기능성이 다르면 안내 문장. 입력이 없거나 같은 제품 자료가 없으면 None."""
    if functional is None:
        return None
    prod = next((r for r in guide if r["layer"] == "제품" and r["exact_product"]), None)
    if prod is None or set(prod["functional"] or []) == set(functional):
        return None
    known = ", ".join(prod["functional"] or []) or "기능성 표시 없음"
    given = ", ".join(functional) or "기능성 아님"
    return f"입력한 기능성({given})이 등록된 '{prod['product_name']}' 제품 자료({known})와 다릅니다. 제품의 기능성 심사·보고 내용을 확인하세요"


def brand_directions(db: Session, brand_id: int | None) -> list[str]:
    """브랜드 소개 문서에 적어 둔 배경 연출 후보. 없으면 빈 목록(공통 연출을 쓴다). 검색·임베딩 호출 없음."""
    if brand_id is None:
        return []
    docs = db.scalars(select(KnowledgeDoc).where(KnowledgeDoc.brand_id == brand_id, KnowledgeDoc.layer == "브랜드",
                                                 KnowledgeDoc.status == "사용")).all()
    return next(((d.visual or {}).get("directions") for d in docs if (d.visual or {}).get("directions")), [])


TREND_GROUP_PRIORITY = {"무드·질감": 0, "루틴·상황": 0, "효능·상황": 0, "성분": 1, "품목": 2}     # 새 정보가 되는 순서(품목명은 브리프에 이미 있기 쉽다)
TREND_SKIP_GROUPS = {"주의", "품목"}   # 수집은 하되 카피에는 넣지 않는 묶음. 주의 = 효능·질환 표현(위반 위험), 품목 = 다른 제품 이름(소재가 아니다)
TREND_CANDIDATES = 3                                              # 후보 = TREND_K × 이 배수(브리프와 가까운 순)


def pick_trend(rows: list[dict], brief: dict, metas: dict[str, dict], k: int = TREND_K) -> list[dict]:
    """트렌드 조각 선택 규칙(05 비교표의 원인 → 11/06 개선):
    ① 브리프(상품명·설명·핵심 메시지)에 이미 있는 키워드는 뺀다(새 정보가 아니다)
    ② 브리프 유사도가 높은 순(제품과 어울리지 않는 키워드가 묶음 순서 때문에 앞서지 않게), 같으면 쇼핑 클릭도 함께 오른 키워드, 그다음 묶음 우선순위
    ③ 상위 k. 걸러져 k 가 안 되면 있는 만큼. 각 조각에 why(고른 이유)를 붙인다."""
    norm = lambda t: re.sub(r"\s+", "", t or "").lower()
    brief_text = norm(" ".join(str(brief.get(x) or "") for x in ("product_name", "description", "key_message")))
    cands = []
    for r in rows:
        kw = r["section"]
        m = metas.get(kw) or {}
        if kw == "요약" or norm(kw) in brief_text or m.get("group") in TREND_SKIP_GROUPS:
            continue
        shop_up = (m.get("shopping") or 0) > 0
        cands.append((-(r.get("score") or 0), 0 if shop_up else 1, TREND_GROUP_PRIORITY.get(m.get("group"), 3), r, m, shop_up))
    cands.sort(key=lambda x: x[:3])
    out = []
    for pri, _, _, r, m, shop_up in cands[:k]:
        why = [m.get("group") or "묶음 없음"]
        if shop_up:
            why.append(f"쇼핑 클릭도 +{round(m['shopping'] * 100):d}%")
        out.append({**r, "trend_group": m.get("group"), "trend_shopping": m.get("shopping"), "trend_naver": m.get("naver"), "why": " · ".join(why)})
    return out


def chosen_trend(db: Session, angles: list[str]) -> list[dict]:
    """사용자가 '이번 주 각도'로 고른 키워드의 조각을 그대로 돌려준다(자동 선택 규칙·검색 없이). 고른 순서를 지키고 최대 TREND_K 개."""
    rows = {ch.section: _row(ch, doc) for ch, doc in db.execute(
        select(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
        .where(KnowledgeDoc.layer == "트렌드", KnowledgeDoc.status == "사용", KnowledgeChunk.section.in_(angles))).all()}
    return [{**rows[a], "why": "사용자가 고른 각도"} for a in angles if a in rows][:TREND_K]


def trend_metas(db: Session) -> dict[str, dict]:
    """'사용' 트렌드 문서의 키워드별 메타(visual.rows)."""
    out: dict[str, dict] = {}
    for doc in db.scalars(select(KnowledgeDoc).where(KnowledgeDoc.layer == "트렌드", KnowledgeDoc.status == "사용")):
        out.update(((doc.visual or {}).get("rows") or {}))
    return out


def has_trend(db: Session) -> bool:
    """'사용' 상태의 트렌드 문서가 있는가(없으면 트렌드 검색을 건너뛴다)."""
    return db.scalar(select(func.count()).select_from(KnowledgeDoc).where(KnowledgeDoc.layer == "트렌드", KnowledgeDoc.status == "사용")) > 0


def trend_on(db: Session, brief: dict) -> bool:
    """브리프 use_trend(기본 켬)와 COPY_TREND 가 둘 다 켜져 있고 트렌드 문서가 있을 때."""
    return COPY_TREND and bool((brief or {}).get("use_trend", True)) and has_trend(db)


def for_copy(db: Session, brand_id: int | None, group_id: int | None, brief: dict, embedder=embed) -> dict:
    """카피 프롬프트에 붙일 지식. 반환: {industries, law: [조각], guide: [조각], error, functional_mismatch, law_mode}.
    - group_id 가 None(그룹 없는 타 브랜드 = 대조군)이면 규제까지 포함해 아무 지식도 넣지 않는다(law_mode=off)
    - law: 품목 글에서 감지한 업종의 규제 조각(핵심은 항상, 나머지는 검색)
    - guide: 제품 2 · 브랜드 2 · 그룹 1 검색 결과. 이 브랜드·그룹에 지식이 없으면 빈 목록(임베딩 호출 없음)
    - 임베딩 호출이 실패하면 guide 없이 진행하고 error 에 오류 이름만 남긴다(카피 생성을 막지 않는다)"""
    from . import legal
    industries = legal.detect(legal.source_text(brief))
    if group_id is None and not (brief or {}).get("smallbiz"):
        # 타 브랜드(그룹 없음) = 대조군: 규제를 포함한 지식을 전혀 넣지 않는다(00 정의서 v2.2, 2026-11-05 결정).
        # 소상공인 캠페인(brief["smallbiz"])은 그룹이 없어도 규제와 트렌드를 넣는다(2026-11-06 방향 전환)
        # 검수의 법적 표현 사전(legal.violations)은 공통이라 대조군 카피가 거기서 잡히는 것이 S3 비교다
        return {"industries": industries, "law": [], "guide": [], "trend": [], "trend_mode": "off", "error": None,
                "functional_mismatch": functional_mismatch([], brief.get("functional")),
                "product_note": None, "motifs": None, "directions": [], "law_mode": "off", "law_total": 0, "law_core": 0}
    law_all = law_rows(db, industries)
    # 규제: 업종의 조각이 LAW_ALL_MAX 이하면 전부(작을 때는 검색보다 확실). 그보다 많으면(3-B 법령 전문) 핵심 규제(CORE_LAW)는 **항상** 넣고,
    # 나머지 조문에서 검색 상위 LAW_K 만 보충한다(S4: 핵심 조문은 데이터 양과 상관없이 들어간다)
    law_search = len(law_all) > LAW_ALL_MAX
    core = core_law_rows(db, industries) if law_search else []
    core_docs = {r["doc_id"] for r in core}
    use_trend = trend_on(db, brief)
    out = {"industries": industries, "law": core if law_search else law_all, "guide": [], "trend": [], "trend_mode": "on" if use_trend else "off",
           "error": None, "functional_mismatch": None,
           "product_note": None, "motifs": None, "directions": brand_directions(db, brand_id),
           "law_mode": "core+search" if law_search else "all", "law_total": len(law_all), "law_core": len(core)}
    if has_guide(db, brand_id, group_id) or law_search or use_trend:
        try:
            # 트렌드는 요약 조각을 빼고 TREND_K 개: 요약이 섞여 올 수 있어 하나 더 받는다
            layer_k = {**GUIDE_K, **({"규제": LAW_K} if law_search else {}), **({"트렌드": TREND_K * TREND_CANDIDATES + 1} if use_trend else {})}
            # 트렌드 조각은 수치(+27%)가 들어 있어 _test_result(시험 수치 조각 제외)에 걸리므로 트렌드 문서는 그 검사에서 뺀다
            trend_docs = set(db.scalars(select(KnowledgeDoc.id).where(KnowledgeDoc.layer == "트렌드", KnowledgeDoc.status == "사용"))) if use_trend else set()
            skip = lambda ch: ch.doc_id in core_docs or (ch.doc_id not in trend_docs and _test_result(ch))
            with db.begin_nested():     # SQL 이 실패해도(차원 불일치 등) 트랜잭션이 깨지지 않게 SAVEPOINT 안에서 검색한다
                res = search(db, query_text(brief), brand_id=brand_id, group_id=group_id, layer_k=layer_k,
                             product_name=brief.get("product_name"), embedder=embedder, skip=skip, industries=industries or None)
            if law_search:
                out["law"] = core + res.get("규제", [])
            products = res.get("제품", [])
            if any(r["exact_product"] for r in products):      # 제품을 특정했으면 비슷한 다른 제품 이야기를 섞지 않는다
                products = [r for r in products if r["exact_product"]]
                out["motifs"] = products[0]["motifs"]
            elif products:
                out["product_note"] = (f"'{brief.get('product_name')}' 과 이름이 같은 제품 자료가 없어 비슷한 자료({products[0]['product_name']})를 "
                                       "참고했습니다. 같은 제품이면 sources.json 의 aliases 에 이 이름을 추가하세요")
            out["guide"] = products + [r for layer in ("브랜드", "그룹") for r in res.get(layer, [])]
            if use_trend:
                out["trend"] = (chosen_trend(db, brief["trend_angles"]) if brief.get("trend_angles")
                                else pick_trend(res.get("트렌드", []), brief, trend_metas(db)))
        except Exception as e:      # 키·네트워크 오류 등. 메시지에 민감 정보가 있을 수 있어 이름만 남긴다
            out["error"] = type(e).__name__
    out["functional_mismatch"] = functional_mismatch(out["guide"], brief.get("functional"))
    return out


def refs(k: dict) -> dict:
    """캠페인 입력(brief)에 남길 기록: 실제로 넣은 조각 id 와 요약. 분석 화면이 이것으로 '참고한 자료' 를 보여 주고 프롬프트를 재구성한다."""
    return {"law_chunk_ids": [r["chunk_id"] for r in k["law"]], "guide": [
        {x: r[x] for x in ("chunk_id", "source_id", "layer", "section", "score")} for r in k["guide"]],
            "industries": k["industries"], "error": k["error"], "functional_mismatch": k["functional_mismatch"],
            "product_note": k.get("product_note"), "embed_model": EMBED_MODEL,
            "motifs": k.get("motifs"), "directions": k.get("directions") or [],
            "law_mode": k.get("law_mode"), "law_total": k.get("law_total"), "law_core": k.get("law_core"),
            "trend": [{x: r.get(x) for x in ("chunk_id", "source_id", "section", "score", "why")} for r in k.get("trend", [])],
            "trend_mode": k.get("trend_mode")}


def from_refs(db: Session, saved: dict | None) -> dict | None:
    """refs() 로 남긴 기록 → for_copy 와 같은 모양(임베딩 호출 없음). 지워진 조각은 빠진다. 기록이 없으면 None."""
    if not saved:
        return None
    ids = saved.get("law_chunk_ids", []) + [g["chunk_id"] for g in saved.get("guide", [])] + [t["chunk_id"] for t in saved.get("trend", [])]
    rows = {ch.id: _row(ch, doc) for ch, doc in db.execute(
        select(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
        .where(KnowledgeChunk.id.in_(ids))).all()} if ids else {}
    guide = []
    for g in saved.get("guide", []):
        if g["chunk_id"] in rows:
            guide.append({**rows[g["chunk_id"]], "score": g.get("score")})
    trend = [{**rows[t["chunk_id"]], "score": t.get("score"), "why": t.get("why")} for t in saved.get("trend", []) if t["chunk_id"] in rows]
    return {"industries": saved.get("industries", []), "law": [rows[i] for i in saved.get("law_chunk_ids", []) if i in rows],
            "guide": guide, "trend": trend, "trend_mode": saved.get("trend_mode"),
            "error": saved.get("error"), "functional_mismatch": saved.get("functional_mismatch"),
            "product_note": saved.get("product_note"), "motifs": saved.get("motifs"), "directions": saved.get("directions") or []}
