"""호출 기록: 라우터 의존성이 경로의 design_id 를 동기 엔드포인트·작업 스레드까지 전달하고, 집계가 종류별로 센다."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app import calllog


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setattr(calllog, "LOG_FILE", tmp_path / "api_calls.jsonl")
    return calllog


def test_dependency_passes_design_id_to_sync_endpoint_and_threads(log):
    app = FastAPI()
    r = APIRouter(dependencies=[Depends(calllog.track_design)])

    @r.post("/designs/{design_id}/go")
    def go(design_id: int):
        with calllog.timed("chat", "copy", "openai", "m"):
            pass
        ctx = copy_context()
        with ThreadPoolExecutor(max_workers=2) as ex:
            list(ex.map(lambda i: ctx.copy().run(calllog.record, "images.generate", "icon", "openai", "m"), range(3)))
        return {"design": calllog.current_design()}

    @r.get("/other")
    def other():
        return {"design": calllog.current_design()}

    app.include_router(r)
    c = TestClient(app)
    assert c.post("/designs/7/go").json() == {"design": 7}
    assert c.get("/other").json() == {"design": None}
    s = calllog.summary(design_id=7)
    assert (s["total"], s["chats"], s["images"]) == (4, 1, 3)
    assert s["by_kind"] == {"icon": 3, "copy": 1}
    assert calllog.summary(design_id=8)["total"] == 0


def test_failed_call_is_recorded_but_not_counted(log):
    with pytest.raises(RuntimeError):
        with calllog.timed("images.edit", "variant", "openai", "m", size="1024x1536"):
            raise RuntimeError("boom")
    calllog.record("images.edit", "variant", "openai", "m")
    s = calllog.summary()
    assert (s["total"], s["ok"], s["failed"]) == (2, 1, 1)
    assert s["by_kind"] == {"variant": 1}
    rows = calllog._rows()
    assert rows[0]["ok"] is False and rows[0]["error"].startswith("RuntimeError") and rows[0]["size"] == "1024x1536"
    assert rows[1]["ms"] is None


def test_quality_for_and_is_draft(monkeypatch):
    from app import imagegen
    monkeypatch.setattr(imagegen, "OPENAI_QUALITY", "medium")
    monkeypatch.setattr(imagegen, "DRAFT_QUALITY", "low")
    assert imagegen.quality_for(False) == "low" and imagegen.quality_for(True) == "medium"
    assert imagegen.is_draft("low") and not imagegen.is_draft("medium")
    assert not imagegen.is_draft(None)       # 품질을 기록하지 않던 예전 배경은 초안으로 보지 않는다
    monkeypatch.setattr(imagegen, "DRAFT_QUALITY", "medium")   # 같은 값이면 초안 구분이 없다
    assert not imagegen.is_draft(imagegen.quality_for(False))
