"""기존 로컬 데이터(SQLite + backend/storage + backend/data 의 로그)를 Supabase(Postgres + Storage)로 옮긴다.

실행: DATABASE_URL, SUPABASE_URL, SUPABASE_SERVICE_KEY 를 환경변수로 주고
      backend/.venv/bin/python tools/migrate_to_supabase.py [--sqlite backend/data/app.db] [--dry-run]

- **앱이 이 DB 에 처음 접속하기 전에** 실행한다. 앱은 첫 요청에서 규격 프리셋을 시드하므로, 그 뒤에 옮기면 id 가 겹친다.
  (대상 테이블에 이미 행이 있으면 아무것도 하지 않고 멈춘다.)
- 테이블 행은 그대로 복사하고(id 유지), 파일 경로 컬럼은 STORAGE_DIR 기준 키로 바꾼다. 시퀀스는 최대 id 로 맞춘다.
- backend/storage 의 파일(썸네일 캐시 제외)을 버킷에 올린다. 버킷이 없으면 비공개로 만든다.
- 호출 기록(api_calls.jsonl)·프롬프트 전문(prompt_log/)·채점 결과(analysis/)는 DB 테이블로 옮긴다.
  일일 이미지 카운터(image_usage.json)는 옮기지 않는다(하루 단위라 의미가 없다).
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine, func, insert, select, text   # noqa: E402
from sqlalchemy.orm import Session                                  # noqa: E402

from app import db, models, storage                                # noqa: E402,F401  (models: 테이블 등록)

PATH_COLUMNS = {"assets": ["file_path"], "generated_images": ["file_path"], "icon_assets": ["png_path", "source_path"],
                "exports": ["zip_path"]}
FROM_FILES = {"api_calls", "prompt_logs", "judgments", "kv"}    # 파일에서 채우는 테이블(SQLite 에는 없다)


def table_key(p: str | None) -> str | None:
    """SQLite 에 든 경로(절대 경로 또는 키)를 저장소 키로. STORAGE_DIR 밖의 경로는 그대로 두고 알린다."""
    if not p:
        return p
    k = storage.key_of(p)
    if k is None:
        # 다른 PC 의 절대 경로일 수 있다: .../storage/<키> 형태면 storage/ 뒤를 키로 쓴다
        parts = Path(p).parts
        if "storage" in parts:
            return "/".join(parts[parts.index("storage") + 1:])
        print(f"  ! 저장소 밖 경로라 그대로 둡니다: {p}")
        return p
    return k


def ensure_bucket() -> None:
    cfg = storage._cfg()
    r = storage._call("GET", f"bucket/{cfg[2]}")
    if r.status_code == 200:
        return
    r = storage._call("POST", "bucket", json={"id": cfg[2], "name": cfg[2], "public": False})
    if r.status_code >= 300:
        sys.exit(f"버킷 만들기 실패({r.status_code}): {r.text[:200]}")
    print(f"버킷 '{cfg[2]}' 생성(비공개)")


def copy_tables(src, dst, dry: bool) -> dict[str, int]:
    counts = {}
    with Session(dst) as d:
        for t in db.Base.metadata.sorted_tables:
            if t.name in FROM_FILES:
                continue
            if d.scalar(select(func.count()).select_from(t)):
                sys.exit(f"대상 DB 의 '{t.name}' 에 이미 행이 있습니다. 앱이 먼저 접속해 시드했다면 비어 있는 새 DB 에서 다시 하세요.")
    with src.connect() as sconn, Session(dst) as d:
        for t in db.Base.metadata.sorted_tables:
            if t.name in FROM_FILES:
                continue
            cols = [c.name for c in t.columns]
            rows = [dict(r._mapping) for r in sconn.execute(select(t))]
            for r in rows:
                for c in PATH_COLUMNS.get(t.name, []):
                    r[c] = table_key(r.get(c))
            counts[t.name] = len(rows)
            if rows and not dry:
                d.execute(insert(t), [{c: r.get(c) for c in cols} for r in rows])
        if not dry:
            d.commit()
            if dst.dialect.name == "postgresql":      # 명시 id 로 넣었으므로 시퀀스를 최대 id 로 맞춘다
                for t in db.Base.metadata.sorted_tables:
                    if "id" in t.c and t.c.id.primary_key and t.c.id.type.python_type is int:
                        d.execute(text(f"SELECT setval(pg_get_serial_sequence('{t.name}', 'id'), "
                                       f"COALESCE((SELECT MAX(id) FROM {t.name}), 1), (SELECT COUNT(*) FROM {t.name}) > 0)"))
                d.commit()
    return counts


def copy_logs(dst, data_dir: Path, dry: bool) -> dict[str, int]:
    out = {"api_calls": 0, "prompt_logs": 0, "judgments": 0, "kv": 0}
    with Session(dst) as d:
        f = data_dir / "api_calls.jsonl"
        for line in (f.read_text().splitlines() if f.exists() else []):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            out["api_calls"] += 1
            d.add(models.ApiCall(design_id=row.get("design_id"), date=row.get("date") or "", row=row))
        for p in sorted((data_dir / "prompt_log").glob("*.json")):
            try:
                text_ = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue
            out["prompt_logs"] += 1
            d.add(models.PromptLog(id=p.stem, text=text_))
        for p in sorted((data_dir / "analysis").glob("judge_*.jsonl")):
            design_id = int(p.stem.split("_", 1)[1])
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    d.add(models.Judgment(design_id=design_id, result=json.loads(line)))
                    out["judgments"] += 1
                except ValueError:
                    continue
        caps = data_dir / "model_caps.json"
        if caps.exists():
            out["kv"] += 1
            d.add(models.KV(key="model_caps", value=json.loads(caps.read_text())))
        if not dry:
            d.commit()
    return out


def upload_files(storage_dir: Path, dry: bool) -> int:
    n = 0
    for p in sorted(storage_dir.rglob("*")):
        rel = p.relative_to(storage_dir).as_posix()
        if not p.is_file() or rel.startswith("thumbs/"):
            continue
        n += 1
        if not dry:
            storage.put(rel, p.read_bytes())
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sqlite", default=str(ROOT / "backend" / "data" / "app.db"))
    ap.add_argument("--storage", default=str(ROOT / "backend" / "storage"))
    ap.add_argument("--data", default=str(ROOT / "backend" / "data"))
    ap.add_argument("--dry-run", action="store_true", help="아무것도 쓰지 않고 옮길 양만 센다")
    a = ap.parse_args()
    if not os.getenv("DATABASE_URL"):
        sys.exit("DATABASE_URL 이 필요합니다 (Supabase 풀러 연결 문자열).")
    if not storage.remote():
        sys.exit("SUPABASE_URL, SUPABASE_SERVICE_KEY 가 필요합니다.")
    if not Path(a.sqlite).exists():
        sys.exit(f"SQLite 파일이 없습니다: {a.sqlite}")
    # 로컬 캐시 폴더가 원본 storage 와 겹치지 않게 임시 폴더를 쓴다(put 이 캐시에도 쓴다)
    db.STORAGE_DIR = Path(os.getenv("TMPDIR", "/tmp")) / "migrate_storage_cache"
    src = create_engine(f"sqlite:///{a.sqlite}")
    dst = db.make_engine()
    db.Base.metadata.create_all(dst)
    if not a.dry_run:
        ensure_bucket()
    print("테이블:", copy_tables(src, dst, a.dry_run))
    print("로그:", copy_logs(dst, Path(a.data), a.dry_run))
    print("파일:", upload_files(Path(a.storage), a.dry_run), "개", "(dry-run)" if a.dry_run else "업로드")


if __name__ == "__main__":
    main()
