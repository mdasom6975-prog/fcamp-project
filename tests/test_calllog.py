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
    assert {k: v["count"] for k, v in s["by_kind"].items()} == {"icon": 3, "copy": 1}
    assert calllog.summary(design_id=8)["total"] == 0


def test_failed_call_is_recorded_but_not_counted(log):
    with pytest.raises(RuntimeError):
        with calllog.timed("images.edit", "variant", "openai", "m", size="1024x1536"):
            raise RuntimeError("boom")
    calllog.record("images.edit", "variant", "openai", "m")
    s = calllog.summary()
    assert (s["total"], s["ok"], s["failed"]) == (2, 1, 1)
    assert s["by_kind"]["variant"]["count"] == 1 and s["by_kind"]["variant"]["failed"] == 1
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


def test_cost_estimate_and_tokens(log, monkeypatch):
    """이미지는 모델·품질·크기 요율, 채팅은 토큰×요율. 표에 없는 모델은 '*' 요율로 추정 표시. 실패 건은 0, FLUX 는 무료."""
    from app import pricing
    monkeypatch.setattr(pricing, "PRICING_FILE", log.LOG_FILE.parent / "none.json")
    monkeypatch.setattr(pricing, "USD_KRW", 1000.0)
    calllog.record("images.generate", "background", "openai", "gpt-image-1", size="1024x1536", quality="medium")
    calllog.record("images.edit", "variant", "openai", "gpt-image-9", size="1024x1024", quality="low")   # 표에 없는 모델
    calllog.record("images.generate", "background", "cloudflare", "flux")
    calllog.record("images.generate", "icon", "openai", "gpt-image-1", ok=False, size="1024x1024", quality="low")
    calllog.record("chat", "copy", "openai", "gpt-4.1-mini", tokens_in=1_000_000, tokens_out=500_000)
    s = calllog.summary()
    bg = s["by_kind"]["background"]
    assert bg["count"] == 2 and bg["cost_usd"] == 0.063 and bg["cost_krw"] == 63 and not bg["estimated"]
    v = s["by_kind"]["variant"]
    assert v["cost_usd"] == 0.011 and v["estimated"] is True
    assert s["by_kind"]["icon"]["count"] == 0 and s["by_kind"]["icon"]["failed"] == 1 and s["by_kind"]["icon"]["cost_usd"] == 0
    c = s["by_kind"]["copy"]
    assert (c["tokens_in"], c["tokens_out"]) == (1_000_000, 500_000) and c["cost_usd"] == 1.2 and not c["estimated"]
    assert s["cost_usd"] == round(0.063 + 0.011 + 1.2, 4) and s["estimated"] is True and s["unpriced"] == 0
    rec = calllog.recent(None) if False else [r for r in calllog._rows()]
    assert pricing.cost_of(rec[4]) == (1.2, False)


def test_pricing_file_overrides_defaults(log, tmp_path, monkeypatch):
    import json
    from app import pricing
    f = tmp_path / "pricing.json"
    f.write_text(json.dumps({"image": {"gpt-image-1": {"medium": {"1024x1024": 0.1}}}, "chat": {"my-model": {"in": 1, "out": 2}}}))
    monkeypatch.setattr(pricing, "PRICING_FILE", f)
    assert pricing.cost_of({"ok": True, "api": "images.generate", "provider": "openai", "model": "gpt-image-1",
                            "size": "1024x1024", "quality": "medium"}) == (0.1, False)
    assert pricing.cost_of({"ok": True, "api": "chat", "provider": "openai", "model": "my-model",
                            "tokens_in": 1_000_000, "tokens_out": 0}) == (1.0, False)
    assert pricing.table()["chat"]["gpt-4.1-mini"]["in"] == 0.40, "기본 항목은 유지된다"
