"""브랜드 그룹(아모레퍼시픽 → 설화수 등): 그룹 등록·소속 지정, 그룹 없는 기존 브랜드의 동작 유지, 기존 DB 컬럼 추가.
외부 API 는 호출하지 않는다 (임시 DB)."""
import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parent.parent
GROUP_BRANDS = sorted(p.parent.name for p in (ROOT / "brands").glob("*/brand.json")
                      if json.loads(p.read_text(encoding="utf-8")).get("group"))
BRAND = {"name": "테스트", "colors": [{"role": "주조색", "hex": "#123456"}]}


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app import routers_assets
    from app.db import Base
    from app.main import app

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[routers_assets.get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_group_create_is_idempotent_by_name(client):
    a = client.post("/api/brand-groups", json={"name": "아모레퍼시픽", "description": "뷰티 그룹"}).json()
    b = client.post("/api/brand-groups", json={"name": " 아모레퍼시픽 "}).json()
    assert a["id"] == b["id"]
    assert [g["name"] for g in client.get("/api/brand-groups").json()] == ["아모레퍼시픽"]


def test_brand_with_and_without_group(client):
    gid = client.post("/api/brand-groups", json={"name": "아모레퍼시픽"}).json()["id"]
    solo = client.post("/api/brands", json=BRAND).json()
    member = client.post("/api/brands", json={**BRAND, "name": "설화수", "group_id": gid}).json()
    assert solo["group_id"] is None and member["group_name"] == "아모레퍼시픽"
    listed = {b["name"]: (b["group_id"], b["group_name"]) for b in client.get("/api/brands").json()}
    assert listed == {"테스트": (None, None), "설화수": (gid, "아모레퍼시픽")}
    assert client.get(f"/api/brands/{member['id']}").json()["group_id"] == gid


def test_unknown_group_is_rejected(client):
    r = client.post("/api/brands", json={**BRAND, "group_id": 999})
    assert r.status_code == 400


def test_update_keeps_group_unless_sent(client):
    """그룹을 모르는 예전 화면·도구가 PUT 해도 소속이 풀리지 않는다. 보내면(None 포함) 그 값으로 바뀐다."""
    gid = client.post("/api/brand-groups", json={"name": "아모레퍼시픽"}).json()["id"]
    bid = client.post("/api/brands", json={**BRAND, "group_id": gid}).json()["id"]
    assert client.put(f"/api/brands/{bid}", json=BRAND).json()["group_id"] == gid
    assert client.put(f"/api/brands/{bid}", json={**BRAND, "group_id": None}).json()["group_id"] is None


def test_added_column_on_existing_db(tmp_path, monkeypatch):
    """group_id 가 없던 기존 DB(로컬 SQLite·배포된 Postgres)에 컬럼을 추가하고, 다시 불러도 안전하다."""
    from app import main
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE brands (id INTEGER PRIMARY KEY, name VARCHAR(100))"))
        conn.execute(text("INSERT INTO brands (id, name) VALUES (1, '기존')"))
    monkeypatch.setattr(main, "engine", eng)
    main._added_columns()
    main._added_columns()
    assert "group_id" in {c["name"] for c in inspect(eng).get_columns("brands")}
    with eng.connect() as conn:
        assert conn.execute(text("SELECT name, group_id FROM brands")).one() == ("기존", None)


def test_amorepacific_brands_seed_into_one_group(client):
    """brand.json 의 group 으로 아모레퍼시픽 브랜드 4개가 한 그룹에 묶여 등록된다 (재실행해도 그룹은 하나)."""
    assert GROUP_BRANDS == ["aestura", "innisfree", "laneige", "sulwhasoo"]
    spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "tools" / "seed_demo.py")
    s = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s)
    s.seed(client, brand_slugs=set(GROUP_BRANDS), log=lambda m: None)
    s.seed(client, brand_slugs=set(GROUP_BRANDS), log=lambda m: None)
    groups = client.get("/api/brand-groups").json()
    assert [g["name"] for g in groups] == ["아모레퍼시픽"]
    names = sorted(b["name"] for b in client.get("/api/brands").json() if b["group_id"] == groups[0]["id"])
    assert names == ["라네즈", "설화수", "에스트라", "이니스프리"]
