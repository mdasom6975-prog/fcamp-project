"""작은 상태값(KV 테이블) 읽기·쓰기. 예전에는 data/*.json 파일이었다(Vercel 함수는 파일을 보존하지 못한다).
SessionLocal 은 호출 시점에 db 모듈에서 읽는다(테스트가 db.SessionLocal 을 바꿔 끼울 수 있게)."""
from . import db
from .models import KV


def get(key: str, default=None):
    with db.SessionLocal() as s:
        row = s.get(KV, key)
        return row.value if row is not None else default


def put(key: str, value: dict) -> None:
    with db.SessionLocal() as s:
        row = s.get(KV, key)
        if row is None:
            s.add(KV(key=key, value=value))
        else:
            row.value = value
        s.commit()


def update(key: str, fn, default: dict | None = None) -> dict:
    """읽고-고치고-쓰기를 한 트랜잭션에서(Postgres 는 행을 잠근다). fn(value) 이 새 값을 돌려준다."""
    with db.SessionLocal() as s:
        row = s.get(KV, key, with_for_update=True)
        new = fn(dict(row.value) if row is not None else dict(default or {}))
        if row is None:
            s.add(KV(key=key, value=new))
        else:
            row.value = new
        s.commit()
        return new
