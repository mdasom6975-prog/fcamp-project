"""공개 시연용 접근 제한(APP_PASSWORD Basic 인증)·API 문서 숨김(APP_PUBLIC)·프론트 빌드본 서빙 검증."""
import base64
import importlib

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    """APP_PASSWORD/APP_PUBLIC 을 지운 상태의 앱. /api/health 가 실제 backend/data 를 읽지 않게 임시 DB 로 돌린다."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import main
    from app.db import Base

    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("APP_USER", raising=False)
    monkeypatch.delenv("APP_PUBLIC", raising=False)
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=engine, autoflush=False))
    return TestClient(main.app)


def _basic(user: str, pw: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_no_password_is_open(client):
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_password_requires_basic_auth(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    r = client.get("/openapi.json")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")
    assert client.get("/openapi.json", headers=_basic("demo", "wrong")).status_code == 401
    assert client.get("/openapi.json", headers=_basic("other", "s3cret")).status_code == 401
    assert client.get("/openapi.json", headers={"Authorization": "Bearer s3cret"}).status_code == 401
    assert client.get("/openapi.json", headers={"Authorization": "Basic %%%"}).status_code == 401


def test_correct_credentials_pass(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert client.get("/openapi.json", headers=_basic("demo", "s3cret")).status_code == 200   # 기본 사용자명 demo
    monkeypatch.setenv("APP_USER", "owner")
    assert client.get("/openapi.json", headers=_basic("demo", "s3cret")).status_code == 401
    assert client.get("/openapi.json", headers=_basic("owner", "s3cret")).status_code == 200


def test_health_is_exempt(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_app_public_hides_docs(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("APP_PUBLIC", "1")
    try:
        importlib.reload(main)                     # docs_url 은 앱 생성 시점에 정해지므로 재로드
        main._ready = True                         # 재로드하면 _ready 가 풀려 첫 요청이 실제 backend/data/app.db 를 초기화한다
        c = TestClient(main.app)
        assert c.get("/docs").status_code == 404
        assert c.get("/redoc").status_code == 404
        assert c.get("/openapi.json").status_code == 404
    finally:
        monkeypatch.delenv("APP_PUBLIC", raising=False)
        importlib.reload(main)                     # 다른 테스트가 문서가 켜진 앱을 보게 되돌린다
        main._ready = True
    assert TestClient(main.app).get("/docs").status_code == 200


def test_frontend_dist_served_at_root(client):
    from app import main

    if not (main.DIST_DIR / "index.html").exists():
        pytest.skip("frontend/dist 없음 (npm run build 뒤에 검증)")
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    r = client.get("/some/unknown/page")           # 단일 페이지 앱: 없는 경로도 index.html
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert client.get("/api/no-such-endpoint").status_code == 404   # API 오타는 그대로 404
