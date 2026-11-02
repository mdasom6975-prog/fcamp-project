"""RAG 진화 시연 API: 저장된 데이터를 그대로 돌려주고, 데이터가 없으면 404. 파일 이름으로 폴더 밖을 읽지 못한다."""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import routers_evolution
    from app.main import app
    monkeypatch.setattr(routers_evolution, "DATA_DIR", tmp_path)
    return TestClient(app), tmp_path


def test_missing_data_is_404(client):
    c, _ = client
    assert c.get("/api/evolution").status_code == 404


def test_returns_saved_data_and_images(client):
    c, d = client
    (d / "data.json").write_text(json.dumps({"title": "시연", "stages": []}, ensure_ascii=False), encoding="utf-8")
    (d / "after.jpg").write_bytes(b"jpg")
    assert c.get("/api/evolution").json()["title"] == "시연"
    assert c.get("/api/evolution/image/after.jpg").content == b"jpg"


@pytest.mark.parametrize("name", ["data.json", "..%2Fsecret.jpg", "none.jpg"])
def test_image_rejects_other_files(client, name):
    c, d = client
    (d / "data.json").write_text("{}", encoding="utf-8")
    assert c.get(f"/api/evolution/image/{name}").status_code == 404
