"""brands/<그룹>/sources.json 과 raw/<id>.txt 를 잘라 임베딩하고 DB 의 지식 저장소에 넣는다 (RAG 1-a 등록, 임베딩 과금).

실행: backend/.venv/bin/python tools/register_knowledge.py brands/amorepacific/sources.json [--only id1,id2] [--dry-run]
- 먼저 tools/fetch_sources.py 로 raw/ 를 채운다. raw/<id>.txt 가 없는 출처는 건너뛰고 알려 준다
- --dry-run: 임베딩·저장 없이 어떻게 잘리는지만 보여 준다(과금 없음). 청킹을 바꿔 볼 때 먼저 이것으로 확인한다
- DB 는 앱과 같다(backend/.env 의 DATABASE_URL — dev 프로젝트. prod 에 넣을 때는 DATABASE_URL 을 prod 값으로 바꿔 실행). 브랜드가 아직 없으면 brands/<slug>/brand.json 으로 먼저 등록한다(과금 없음)
- 같은 출처 id 로 다시 등록하면 이전 문서는 '보관' 으로 바뀌고 새 문서가 쓰인다
- 자르기: 규제(법령)는 조·항 단위, 별표("annex")는 목 단위, 제품은 제품 하나가 조각 하나, 나머지는 문단 단위(800자 넘으면 문장으로 더 자름)
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def chunks_for(s: dict, text: str) -> list[tuple[str, str]]:
    from app import knowledge
    if s["layer"] == "규제" and s.get("law"):
        return knowledge.chunk_law(text, s["law"])
    if s["layer"] == "규제" and s.get("annex"):
        return knowledge.chunk_annex(text, s["annex"])
    if s["layer"] == "제품":
        return [(s["product_name"], f"[{s['title']}] " + " ".join(text.split()))]
    return knowledge.chunk_paragraphs(text, s["title"])


def _ensure_brands(slugs: set[str]) -> dict[str, int]:
    """brand slug → DB brand id. 없는 브랜드는 seed_demo 로 등록한다(앱 API 를 같은 프로세스에서 호출)."""
    import importlib.util

    from fastapi.testclient import TestClient

    from app.main import app
    spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "tools" / "seed_demo.py")
    seed_demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_demo)
    with TestClient(app) as client:
        return seed_demo.seed(client, brand_slugs=slugs, log=lambda m: print("  " + m))


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    src = Path(args[0])
    only = set(args[args.index("--only") + 1].split(",")) if "--only" in args else None
    dry = "--dry-run" in args
    raw = src.parent / "raw"
    doc = json.loads(src.read_text(encoding="utf-8"))
    todo = []
    for s in doc["sources"]:
        if only and s["id"] not in only:
            continue
        f = raw / f"{s['id']}.txt"
        if not f.exists():
            print(f"[없음] {s['id']}: {f} 가 없습니다 ({'fetch_sources.py 로 받기' if s['kind'] != 'manual' else s.get('how', '직접 준비')})")
            continue
        todo.append((s, f, chunks_for(s, f.read_text(encoding="utf-8"))))

    if dry:
        for s, _, chunks in todo:
            print(f"\n== {s['id']} [{s['layer']}] {len(chunks)}조각")
            for sec, text in chunks:
                print(f"  - {sec} ({len(text)}자): {text[:90]}…")
        return 0

    from sqlalchemy import select

    from app import db, knowledge
    from app.main import init_app
    from app.models import BrandGroup
    init_app()
    brand_ids = _ensure_brands({s["brand"] for s, _, _ in todo if s.get("brand")})
    with db.SessionLocal() as session:
        group = session.scalar(select(BrandGroup).where(BrandGroup.name == doc["group"]))
        if not group:
            print(f"그룹 '{doc['group']}' 이 없습니다. brand.json 의 group 으로 브랜드를 먼저 등록하세요")
            return 2
        total = 0
        for s, f, chunks in todo:
            d = knowledge.add_document(
                session, layer=s["layer"], source_id=s["id"], title=s["title"], chunks=chunks,
                group_id=group.id if s["layer"] != "규제" else None,
                brand_id=brand_ids.get(s["brand"]) if s.get("brand") else None,
                source_url=s.get("url", ""), fetched_at=date.fromtimestamp(f.stat().st_mtime).isoformat(),
                product_name=s.get("product_name"), functional=s.get("functional"), industry=s.get("industry"), aliases=s.get("aliases"),
                visual={k: s[k] for k in ("motifs", "directions") if s.get(k)} or None)
            total += len(chunks)
            print(f"[등록] {s['id']} → 문서 {d.id}, {len(chunks)}조각")
        print(f"합계 {len(todo)}개 문서, {total}조각 (임베딩 모델 {knowledge.EMBED_MODEL})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
