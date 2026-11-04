"""Supabase 환경 사이 복제: prod → dev (또는 그 반대). DB 테이블 전부와 Storage 버킷 파일을 그대로 옮긴다.

실행 (저장소 루트에서, 원본은 *_FROM, 대상은 backend/.env 의 값 또는 환경변수):
    export DATABASE_URL_FROM='postgresql://…prod…:6543/postgres'
    export SUPABASE_URL_FROM='https://<prod ref>.supabase.co'
    export SUPABASE_SERVICE_KEY_FROM='…'
    backend/.venv/bin/python tools/sync_env.py --dry-run     # 옮길 양만 센다
    backend/.venv/bin/python tools/sync_env.py               # 대상 DB 가 비어 있어야 한다
    backend/.venv/bin/python tools/sync_env.py --replace     # 대상의 모든 표·파일을 지우고 다시 채운다(dev 를 prod 와 같게 맞출 때)

- 대상이 prod 이면 멈춘다(DATABASE_URL 에 'prod' 표시를 둘 수 없으므로 --allow-prod 를 줘야 한다). 되돌릴 수 없는 일이라 두 번 확인한다.
- 행은 id 를 유지해 복사하고 시퀀스를 최대 id 로 맞춘다(tools/migrate_to_supabase.copy_tables 와 같은 방식). 파일 키는 그대로.
- Storage 는 원본 버킷의 모든 객체(썸네일 캐시 thumbs/ 제외)를 내려받아 대상 버킷에 올린다. 대상 버킷이 없으면 비공개로 만든다.
- 복제 뒤 dev 에서 만든 변경은 prod 에 올라가지 않는다. 같은 데이터로 맞추는 방향은 항상 prod → dev.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import httpx                                                        # noqa: E402
from sqlalchemy import inspect, create_engine, delete, func, insert, select, text   # noqa: E402
from sqlalchemy.orm import Session                                  # noqa: E402
from sqlalchemy.pool import NullPool                                # noqa: E402

from app import db, models, storage                                 # noqa: E402,F401  (models: 테이블 등록)


def _from_env(name: str) -> str:
    v = os.getenv(f"{name}_FROM", "").strip()
    if not v:
        sys.exit(f"{name}_FROM 이 필요합니다(원본 환경의 값).")
    return v


class Bucket:
    """Supabase Storage 버킷 하나(원본·대상 각각). storage.py 는 환경변수 하나(대상)만 보므로 여기서 따로 다룬다."""

    def __init__(self, url: str, key: str, bucket: str):
        self.url, self.key, self.bucket = url.rstrip("/"), key, bucket
        self.http = httpx.Client(timeout=120.0, headers={"Authorization": f"Bearer {key}", "apikey": key})

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        return self.http.request(method, f"{self.url}/storage/v1/{path}", **kw)

    def ensure(self) -> None:
        if self._call("GET", f"bucket/{self.bucket}").status_code == 200:
            return
        r = self._call("POST", "bucket", json={"id": self.bucket, "name": self.bucket, "public": False})
        if r.status_code >= 300:
            sys.exit(f"버킷 만들기 실패({r.status_code}): {r.text[:200]}")
        print(f"버킷 '{self.bucket}' 생성(비공개)")

    def keys(self, prefix: str = "") -> list[str]:
        """폴더를 재귀로 내려가며 객체 키 전부. 폴더 항목은 id 가 없다."""
        out: list[str] = []
        offset = 0
        while True:
            r = self._call("POST", f"object/list/{self.bucket}",
                           json={"prefix": prefix, "limit": 1000, "offset": offset, "sortBy": {"column": "name", "order": "asc"}})
            if r.status_code != 200:
                sys.exit(f"목록 조회 실패({r.status_code}) prefix={prefix!r}: {r.text[:200]}")
            items = r.json()
            for it in items:
                name = f"{prefix}/{it['name']}" if prefix else it["name"]
                if it.get("id") is None:
                    out.extend(self.keys(name))
                else:
                    out.append(name)
            if len(items) < 1000:
                return out
            offset += len(items)

    def get(self, key: str) -> bytes:
        r = self._call("GET", f"object/authenticated/{self.bucket}/{key}")
        if r.status_code != 200:
            sys.exit(f"내려받기 실패({r.status_code}): {key}")
        return r.content

    def put(self, key: str, data: bytes) -> None:
        r = self._call("POST", f"object/{self.bucket}/{key}", content=data, headers={"x-upsert": "true"})
        if r.status_code >= 300:
            sys.exit(f"올리기 실패({r.status_code}): {key} {r.text[:200]}")

    def remove(self, keys: list[str]) -> None:
        for i in range(0, len(keys), 500):
            self._call("DELETE", f"object/{self.bucket}", json={"prefixes": keys[i:i + 500]})


def copy_tables(src, dst, dry: bool, replace: bool) -> dict[str, int]:
    tables = db.Base.metadata.sorted_tables
    with Session(dst) as d:
        if replace and not dry:
            for t in reversed(tables):
                d.execute(delete(t))
            d.commit()
        elif not replace:
            for t in tables:
                if d.scalar(select(func.count()).select_from(t)):
                    sys.exit(f"대상 DB 의 '{t.name}' 에 이미 행이 있습니다. 비우고 다시 채우려면 --replace.")
    counts = {}
    with src.connect() as sconn, Session(dst) as d:
        insp = inspect(src)
        for t in tables:
            if not insp.has_table(t.name):          # 원본이 옛 코드로 돌면 테이블 자체가 없을 수 있다
                counts[t.name] = 0
                continue
            # 원본에 있는 컬럼만 읽는다: 운영이 옛 배포면 나중에 추가된 컬럼(예: knowledge_docs.visual)이 없다. 빈 자리는 모델 기본값(없으면 NULL)
            have = {c["name"] for c in insp.get_columns(t.name)}
            cols = [c for c in t.columns if c.name in have]
            rows = [dict(r._mapping) for r in sconn.execute(select(*cols))]
            counts[t.name] = len(rows)
            if rows and not dry:
                for i in range(0, len(rows), 500):
                    d.execute(insert(t), [{c.name: r.get(c.name) for c in cols} for r in rows[i:i + 500]])
        if not dry:
            d.commit()
            for t in tables:        # 명시 id 로 넣었으므로 시퀀스를 최대 id 로 맞춘다
                if "id" in t.c and t.c.id.primary_key and t.c.id.type.python_type is int:
                    d.execute(text(f"SELECT setval(pg_get_serial_sequence('{t.name}', 'id'), "
                                   f"COALESCE((SELECT MAX(id) FROM {t.name}), 1), (SELECT COUNT(*) FROM {t.name}) > 0)"))
            d.commit()
    return counts


def copy_files(src: Bucket, dst: Bucket, dry: bool, replace: bool) -> int:
    keys = [k for k in src.keys() if not k.startswith("thumbs/")]
    if dry:
        return len(keys)
    dst.ensure()
    if replace:
        old = dst.keys()
        if old:
            dst.remove(old)
    for i, k in enumerate(keys, 1):
        dst.put(k, src.get(k))
        if i % 50 == 0:
            print(f"  파일 {i}/{len(keys)}")
    return len(keys)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="아무것도 쓰지 않고 옮길 양만 센다")
    ap.add_argument("--replace", action="store_true", help="대상의 표·파일을 모두 지우고 다시 채운다")
    ap.add_argument("--allow-prod", action="store_true", help="대상이 운영 프로젝트여도 진행한다(되돌릴 수 없음)")
    ap.add_argument("--bucket-from", default=os.getenv("SUPABASE_BUCKET_FROM", "brand-studio"))
    a = ap.parse_args()
    src_url = _from_env("DATABASE_URL")
    src_bucket = Bucket(_from_env("SUPABASE_URL"), _from_env("SUPABASE_SERVICE_KEY"), a.bucket_from)
    if not os.getenv("DATABASE_URL"):
        sys.exit(db.MISSING_URL)
    cfg = storage._cfg()
    if not cfg:
        sys.exit("대상의 SUPABASE_URL, SUPABASE_SERVICE_KEY 가 필요합니다(backend/.env).")
    if src_url == os.getenv("DATABASE_URL") or cfg[0] == src_bucket.url:
        sys.exit("원본과 대상이 같은 프로젝트입니다.")
    if os.getenv("TARGET_ENV", "dev").strip().lower() == "prod" and not a.allow_prod:
        sys.exit("대상이 prod 로 표시되어 있습니다(TARGET_ENV=prod). 정말 덮어쓰려면 --allow-prod.")
    dst_bucket = Bucket(cfg[0], cfg[1], cfg[2])
    src = create_engine(db.make_engine(src_url).url, poolclass=NullPool, connect_args={"prepare_threshold": None})
    dst = db.make_engine()
    db.Base.metadata.create_all(dst)
    print("테이블:", copy_tables(src, dst, a.dry_run, a.replace))
    print("파일:", copy_files(src_bucket, dst_bucket, a.dry_run, a.replace), "개", "(dry-run)" if a.dry_run else "복제")


if __name__ == "__main__":
    main()
