"""브랜드 공식 사이트에서 제품 정보·이미지를 가져와 제품 층 지식과 제품 이미지 자산으로 등록한다 (UI 개편 2차 · 2026-11).

사용자 입력을 줄이기 위한 흐름: 지식 브라우저 제품 층 → "제품 가져오기" → (목록 발견 → 몇 개씩 가져오기) → 제품 조각에서 바로 캠페인 만들기.
- 사이트마다 추출 규칙이 다르다. SITES 에 브랜드 이름(DB 의 Brand.name) → 목록 주소·규칙 이름을 두고, 규칙은 이 파일의 함수 하나다.
  구조가 바뀌면 그 브랜드만 깨지고, 실패는 제품 단위로 결과에 남는다(어느 제품에서 무엇을 못 읽었는지).
- 아모레퍼시픽 계열 공식 사이트만, 요청 사이 SLEEP 초, 원문은 저장하지 않는다(사이트 안내문을 요약한 텍스트만 지식으로).
- 목록 썸네일은 핫링크 차단이라 쓰지 않는다. 상세 페이지의 첫 제품 사진(큰 판)을 받아 긴 변 PRODUCT_LONG_SIDE 로 줄여 WEBP 로 저장한다.
- 같은 제품(source_id)을 다시 가져오면 이전 문서는 '보관', 이미지 자산은 새로 만든다(기존 시안이 참조하는 자산은 지우지 않는다).
- 기능성 화장품 표시 여부는 페이지에 없어 채우지 않는다(functional=None → 검수가 보수적으로 본다).
"""
import io
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from html import unescape

import httpx
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import knowledge, storage
from .models import Asset, Brand, KnowledgeDoc

UA = "Mozilla/5.0 (brand-studio RAG study; contact: local)"
SLEEP = 1.0
PRODUCT_LONG_SIDE = 1024
TEXT_MAX = 900            # 조각 본문 상한(글자). 전성분은 앞부분만

# 브랜드 이름(DB) → 사이트 규칙. list_url 은 사용자가 입력하지 않는다
SITES: dict[str, dict] = {
    "에스트라": {"site": "aestura", "list_url": "https://www.aestura.com/web/product/list.do?pCateCd=PC02",
                "detail_url": "https://www.aestura.com/web/product/view.do?prdSeq={seq}", "source_prefix": "prod_aestura_site"},
}


@dataclass
class Found:
    seq: str
    name: str
    line: str = ""
    price: int | None = None


@dataclass
class Product:
    seq: str
    name: str
    intro: str = ""
    description: str = ""
    quantity: str = ""
    ingredients: str = ""
    image_url: str | None = None
    url: str = ""
    line: str = ""
    price: int | None = None
    problems: list[str] = field(default_factory=list)


def _client() -> httpx.Client:
    return httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": UA})


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


# ---- 에스트라 규칙 ----

def aestura_list(html: str) -> list[Found]:
    """목록 카드의 속성(prdNm·prdSeq·salPrc·catCd2ndNm)에서 제품을 읽는다. 서버 HTML 이라 스크립트 실행이 필요 없다."""
    out = []
    for m in re.finditer(r'prdNm=\s*"([^"]+)"\s*prdSeq="(\d+)"\s*salPrc="(\d*)"\s*catCd1stNm="[^"]*"\s*catCd2ndNm="([^"]*)"', html):
        name, seq, price, line = m.groups()
        out.append(Found(seq=seq, name=unescape(name).strip(), line=unescape(line).strip(), price=int(price) if price else None))
    return out


def aestura_detail(html: str, p: Product) -> Product:
    """상세 페이지의 기본 제품 정보 블록(pdtName·intro·desc·quantity)과 전성분 토글, 첫 제품 사진(큰 판)."""
    def grab(cls: str) -> str:
        m = re.search(rf'<p class="{cls}">(.*?)</p>', html, flags=re.S)
        return _text(m.group(1)) if m else ""
    p.name = grab("pdtName") or p.name
    p.intro = grab("intro")
    p.description = grab("desc")
    p.quantity = grab("quantity")
    m = re.search(r'전성분</a>\s*<div class="txtBox"[^>]*>(.*?)</div>', html, flags=re.S)
    if m:
        p.ingredients = _text(m.group(1))
    m = re.search(r'<source srcset="([^"]+_DSPIMG_S\.webp[^"]*)"', html)
    if m:
        p.image_url = m.group(1).replace("_DSPIMG_S.webp", "_DSPIMG_L.webp")      # 썸네일 → 같은 사진의 큰 판
    for f, label in (("intro", "한 줄 소개"), ("description", "설명"), ("image_url", "제품 사진")):
        if not getattr(p, f):
            p.problems.append(f"{label} 없음")
    return p


RULES = {"aestura": (aestura_list, aestura_detail)}


# ---- 공통 ----

def discover(brand_name: str, client: httpx.Client | None = None) -> list[Found]:
    cfg = SITES.get(brand_name)
    if not cfg:
        raise ValueError(f"'{brand_name}' 의 제품 사이트 규칙이 없습니다 (products_import.SITES)")
    c = client or _client()
    try:
        r = c.get(cfg["list_url"])
        r.raise_for_status()
    finally:
        if client is None:          # 넘겨받은 클라이언트는 호출자가 닫는다
            c.close()
    return RULES[cfg["site"]][0](r.text)


def fetch_product(brand_name: str, found: Found, client: httpx.Client) -> Product:
    cfg = SITES[brand_name]
    p = Product(seq=found.seq, name=found.name, line=found.line, price=found.price, url=cfg["detail_url"].format(seq=found.seq))
    r = client.get(p.url)
    r.raise_for_status()
    return RULES[cfg["site"]][1](r.text, p)


def clean_name(name: str, brand_name: str) -> str:
    """'에스트라 아토베리어365 하이드로 수딩크림' → '아토베리어365 하이드로 수딩크림'. 브리프의 상품명과 맞추기 위해 브랜드 접두를 뗀다."""
    n = re.sub(rf"^{re.escape(brand_name)}\s*", "", name).strip()
    return n or name


def chunk_text(p: Product, brand_name: str) -> str:
    parts = [f"{brand_name} {clean_name(p.name, brand_name)}" + (f" {p.quantity}" if p.quantity else "") + "."]
    if p.intro:
        parts.append(p.intro + ".")
    if p.description and p.description != p.intro:
        parts.append(p.description + ".")
    if p.line:
        parts.append(f"라인: {p.line}.")
    if p.ingredients:
        parts.append("주요 성분·설명: " + p.ingredients[:400] + ("…" if len(p.ingredients) > 400 else ""))
    parts.append("기능성 화장품 표시 여부는 공식 사이트 제품 정보에서 확인하지 못했다(기능성 효능은 표현하지 않는다).")
    return " ".join(parts)[:TEXT_MAX]


def save_image(client: httpx.Client, url: str, brand_id: int, db: Session) -> Asset:
    r = client.get(url)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content))
    im.load()
    im.thumbnail((PRODUCT_LONG_SIDE, PRODUCT_LONG_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    (im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB")).save(buf, format="WEBP", quality=88, method=4)
    data = buf.getvalue()
    key = storage.put(f"product_{uuid.uuid4().hex[:10]}.webp", data, "image/webp")
    a = Asset(brand_id=brand_id, kind="product", file_path=key, mime="image/webp", bytes=len(data), width=im.width, height=im.height)
    db.add(a)
    db.flush()
    return a


def import_products(db: Session, brand: Brand, seqs: list[str], embedder=knowledge.embed, client: httpx.Client | None = None,
                    sleep: float = SLEEP) -> list[dict]:
    """seqs 의 제품을 가져와 등록한다. 반환: 제품마다 {seq, name, ok, doc_id, asset_id, problems}. 한 제품의 실패가 다른 제품을 막지 않는다."""
    cfg = SITES.get(brand.name)
    if not cfg:
        raise ValueError(f"'{brand.name}' 의 제품 사이트 규칙이 없습니다")
    found = {f.seq: f for f in discover(brand.name, client) if f.seq in set(seqs)}
    out = []
    own = client is None
    c = client or _client()
    try:
        for seq in seqs:
            f = found.get(seq)
            if not f:
                out.append({"seq": seq, "name": "", "ok": False, "problems": ["목록에 없는 제품"]})
                continue
            try:
                time.sleep(sleep)
                p = fetch_product(brand.name, f, c)
                asset = None
                if p.image_url:
                    try:
                        time.sleep(sleep)
                        asset = save_image(c, p.image_url, brand.id, db)
                    except Exception as e:
                        db.rollback()           # flush 가 실패했으면 세션을 되돌려야 다음 저장이 PendingRollbackError 로 막히지 않는다
                        p.problems.append(f"제품 사진 저장 실패: {type(e).__name__}: {str(e)[:120]}")
                name = clean_name(p.name, brand.name)
                visual = {"image_asset_id": asset.id if asset else None, "price": p.price, "line": p.line, "quantity": p.quantity}
                doc = knowledge.add_document(
                    db, layer="제품", source_id=f"{cfg['source_prefix']}_{seq}", title=f"{name} 제품 정보",
                    chunks=[(name, chunk_text(p, brand.name))], brand_id=brand.id, source_url=p.url,
                    fetched_at=date.today().isoformat(), product_name=name, functional=None,
                    aliases=_aliases(name), visual=visual, embedder=embedder)
                out.append({"seq": seq, "name": name, "ok": True, "doc_id": doc.id, "asset_id": asset.id if asset else None, "problems": p.problems})
            except Exception as e:
                db.rollback()
                out.append({"seq": seq, "name": f.name, "ok": False, "problems": [f"{type(e).__name__}: {str(e)[:120]}"]})
    finally:
        if own:
            c.close()
    return out


def _aliases(name: str) -> list[str]:
    """'아토베리어365 하이드로 수딩크림' → ['아토베리어 365 하이드로 수딩크림', '하이드로 수딩크림']: 숫자 띄어쓰기 변형과 라인명을 뗀 이름."""
    out = []
    spaced = re.sub(r"([가-힣A-Za-z])(\d)", r"\1 \2", name)
    if spaced != name:
        out.append(spaced)
    words = name.split()
    if len(words) >= 3:
        out.append(" ".join(words[1:]))
    return out


def imported(db: Session, brand_id: int) -> dict[str, int]:
    """이 브랜드에서 사이트로 가져온 제품: source_id → doc id (사용 중인 것만)."""
    rows = db.execute(select(KnowledgeDoc.source_id, KnowledgeDoc.id).where(
        KnowledgeDoc.brand_id == brand_id, KnowledgeDoc.layer == "제품", KnowledgeDoc.status == "사용",
        KnowledgeDoc.source_id.like("prod_%_site_%"))).all()
    return {sid: did for sid, did in rows}
