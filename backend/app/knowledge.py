"""RAG 지식 저장소: 문서를 조각으로 자르고(청킹) 임베딩해 저장하고, 브리프로 층별 관련 조각을 찾는다.

설계: RAG 설계 문서(16, v2.1) 3~5절. 층(layer)은 규제 · 그룹 · 브랜드 · 제품 · 캠페인.
- 다른 브랜드의 조각은 벡터 비교 전에 필터로 뺀다(브랜드 섞임은 유사도가 아니라 필터로 막는다).
- 층마다 따로 상위 k 개를 뽑는다. 한 번에 뽑으면 브랜드 이야기가 규제 조항을 밀어낼 수 있다.
- 임베딩은 JSON 실수 배열로 저장하고 파이썬으로 코사인 유사도를 계산한다(조각 수천 개까지 충분, 추가 의존성 없음).
"""
import math
import os
import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import calllog
from .models import KnowledgeChunk, KnowledgeDoc

LAYERS = ("규제", "그룹", "브랜드", "제품", "캠페인")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
LAYER_K = {"규제": 3, "제품": 2, "브랜드": 2, "그룹": 1}     # 검색할 때 층마다 가져올 개수
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
            r = client().embeddings.create(model=EMBED_MODEL, input=batch)
            info["tokens_in"] = getattr(getattr(r, "usage", None), "prompt_tokens", None)
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
                 functional: list[str] | None = None, embedder=embed) -> KnowledgeDoc:
    """조각을 임베딩해 문서 하나로 저장한다. 같은 source_id 의 '사용' 문서는 '보관' 으로 돌린다(재등록 = 새 버전).
    embedder 는 테스트에서 가짜 임베딩을 넣을 때 바꾼다."""
    if layer not in LAYERS:
        raise ValueError(f"layer 는 {LAYERS} 중 하나: {layer}")
    if not chunks:
        raise ValueError(f"조각이 없습니다: {source_id}")
    for old in db.scalars(select(KnowledgeDoc).where(KnowledgeDoc.source_id == source_id, KnowledgeDoc.status == "사용")):
        old.status = "보관"
    doc = KnowledgeDoc(layer=layer, source_id=source_id, title=title, group_id=group_id, brand_id=brand_id,
                       source_url=source_url, fetched_at=fetched_at or date.today().isoformat())
    db.add(doc)
    db.flush()
    vectors = embedder([text for _, text in chunks])
    for seq, ((section, text), vec) in enumerate(zip(chunks, vectors), 1):
        db.add(KnowledgeChunk(doc_id=doc.id, seq=seq, section=section, text=text, embedding=vec,
                              embed_model=EMBED_MODEL, product_name=product_name, functional=functional or []))
    db.commit()
    return doc


# ── 검색 ──────────────────────────────────────────────────────────────────

def query_text(brief: dict) -> str:
    """브리프 → 검색 질의. 상품명·설명·핵심 메시지·타깃을 이어 붙인다."""
    keys = ("product_name", "description", "key_message", "target")
    return " / ".join(str(brief[k]).strip() for k in keys if str(brief.get(k) or "").strip())


def _candidates(db: Session, layer: str, brand_id: int | None, group_id: int | None) -> list[tuple[KnowledgeChunk, KnowledgeDoc]]:
    q = (select(KnowledgeChunk, KnowledgeDoc).join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
         .where(KnowledgeDoc.layer == layer, KnowledgeDoc.status == "사용", KnowledgeChunk.embed_model == EMBED_MODEL))
    if layer in ("브랜드", "제품", "캠페인"):
        if brand_id is None:
            return []
        q = q.where(KnowledgeDoc.brand_id == brand_id)
    elif layer == "그룹":
        if group_id is None:
            return []
        q = q.where(KnowledgeDoc.group_id == group_id)
    return list(db.execute(q).all())


def search(db: Session, query: str, *, brand_id: int | None, group_id: int | None,
           layer_k: dict[str, int] | None = None, product_name: str | None = None, embedder=embed) -> dict[str, list[dict]]:
    """질의로 층별 상위 조각을 찾는다. 반환: {층: [{chunk_id, doc_title, section, text, score, …}]}.
    규제는 모든 브랜드 공통, 그룹은 group_id, 브랜드·제품은 brand_id 로 거른다.
    product_name 이 제품 조각의 제품명과 같으면 그 조각을 맨 앞에 둔다(이름이 정확히 맞으면 유사도보다 확실하다)."""
    layer_k = layer_k or LAYER_K
    qv = embedder([query])[0]
    out: dict[str, list[dict]] = {}
    for layer, k in layer_k.items():
        scored = []
        for ch, doc in _candidates(db, layer, brand_id, group_id):
            s = cosine(qv, ch.embedding)
            exact = bool(product_name and ch.product_name and ch.product_name.replace(" ", "") == product_name.replace(" ", ""))
            scored.append((exact, s, ch, doc))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        out[layer] = [{"chunk_id": ch.id, "doc_id": doc.id, "source_id": doc.source_id, "doc_title": doc.title,
                       "layer": layer, "section": ch.section, "text": ch.text, "score": round(s, 4),
                       "product_name": ch.product_name, "functional": ch.functional, "exact_product": exact}
                      for exact, s, ch, doc in scored[:k]]
    return out
