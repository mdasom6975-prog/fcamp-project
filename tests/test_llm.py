"""텍스트 모델 호출 공급자 전환(llm.py). 실제 API 는 부르지 않는다."""
import json
from types import SimpleNamespace

import pytest

from app import llm


class _FakeBeta:
    def __init__(self, outer): self.outer = outer
    @property
    def messages(self): return self.outer


class _FakeClient:
    """client.beta.messages.create / client.messages.create 를 흉내 낸다. 받은 kwargs 를 기록한다."""
    def __init__(self, text='{"variants": []}', stop_reason="end_turn", model="claude-opus-5-5", fail_beta=False):
        self.calls = []; self.text = text; self.stop_reason = stop_reason; self.model = model; self.fail_beta = fail_beta
        self.messages = self
        self.beta = SimpleNamespace(messages=self)
    def create(self, **kw):
        self.calls.append(kw)
        if "betas" in kw and self.fail_beta:
            import anthropic, httpx2 as httpx
            raise anthropic.BadRequestError("fallbacks is not supported", response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
                                            body={"error": {"message": "fallbacks is not supported"}})
        return SimpleNamespace(stop_reason=self.stop_reason, stop_details=SimpleNamespace(category="cyber"), model=self.model,
                               content=[SimpleNamespace(type="text", text=self.text)],
                               usage=SimpleNamespace(input_tokens=120, output_tokens=40, cache_read_input_tokens=0))


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setattr(llm, "CHAT_PROVIDER", "anthropic")
    monkeypatch.setattr(llm, "CLAUDE_MODEL", "claude-opus-5-5")
    monkeypatch.setattr(llm, "KIND_DEFAULTS", {})      # 종류별 기본(Haiku)은 별도 테스트에서 확인
    monkeypatch.setattr(llm, "CLAUDE_EFFORT", "low")
    fake = _FakeClient(text=json.dumps({"variants": [{"headline": "h", "body": "b", "cta": "c"}], "icons": [{"name": "n", "concept": "o"}]}))
    monkeypatch.setattr(llm, "_anthropic_client", fake)
    return fake


def test_chat_json_uses_schema_fallbacks_and_records_tokens(claude):
    from app import calllog
    data = llm.chat_json("sys", "user", "copy", llm.extras_schema(["광고 제목"]))
    assert data["variants"][0]["headline"] == "h"
    kw = claude.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["system"] == "sys" and kw["messages"] == [{"role": "user", "content": "user"}]
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["output_config"]["effort"] == "low" and kw["output_config"]["format"]["type"] == "json_schema"
    schema = kw["output_config"]["format"]["schema"]
    assert set(schema["required"]) == {"icons", "assets"} and schema["properties"]["assets"]["properties"]["광고 제목"]["type"] == "array"
    assert "thinking" not in kw, "Opus 5.5 는 thinking 을 보내지 않는다(적응형 기본)"
    row = calllog._rows()[-1]
    assert row["provider"] == "anthropic" and row["model"] == "claude-opus-5-5" and row["tokens_in"] == 120 and row["tokens_out"] == 40


def test_chat_json_falls_back_to_plain_create_when_beta_rejected(monkeypatch):
    monkeypatch.setattr(llm, "CHAT_PROVIDER", "anthropic")
    fake = _FakeClient(text='{"icons": []}', fail_beta=True)
    monkeypatch.setattr(llm, "_anthropic_client", fake)
    assert llm.chat_json("s", "u", "icon_names", llm.ICONS_3D_SCHEMA) == {"icons": []}
    assert len(fake.calls) == 2 and "betas" not in fake.calls[1]


def test_chat_json_refusal_raises(monkeypatch):
    monkeypatch.setattr(llm, "CHAT_PROVIDER", "anthropic")
    monkeypatch.setattr(llm, "_anthropic_client", _FakeClient(stop_reason="refusal"))
    with pytest.raises(llm.ChatRefused):
        llm.chat_json("s", "u", "copy", llm.copy_schema())


def test_chat_json_openai_path(monkeypatch):
    from app import copywriter
    monkeypatch.setattr(llm, "CHAT_PROVIDER", "openai")
    class FakeChat:
        def create(self, **kw):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"icons": [{"name": "a"}]}'))],
                                   usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
    monkeypatch.setattr(copywriter, "client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeChat())))
    assert llm.chat_json("s", "u", "icon_names", llm.ICONS_3D_SCHEMA)["icons"][0]["name"] == "a"


def test_copy_and_icons_go_through_llm(monkeypatch):
    """copywriter.generate 와 icons._ask 가 llm.chat_json 을 쓰고, svg 제안은 도형 스키마를 넘긴다."""
    from app import copywriter, icons
    seen = []
    def fake(system, user, kind, schema):
        seen.append((kind, schema))
        return {"variants": [{"headline": "h", "body": "b", "cta": "c"}], "icons": [{"name": "x", "concept": "y", "elements": []}]}
    monkeypatch.setattr(llm, "chat_json", fake)
    vs = copywriter.generate("b", {}, {"product_name": "p"}, {"platform": "x", "name": "y"}, copywriter.limits_for([]))
    assert vs[0]["headline"] == "h" and seen[0][0] == "copy" and "assets" not in seen[0][1]["properties"]
    icons.propose3d("b", {"product_name": "p"}, [])
    assert seen[1] == ("icon_names", llm.ICONS_3D_SCHEMA)
    icons.propose_and_draw("b", {"product_name": "p"}, {"stroke_width": 2, "corner": "둥근", "fill": "윤곽"}, [])
    assert seen[2] == ("icon_names", llm.ICONS_SVG_SCHEMA)
    icons.redraw_one("b", {"product_name": "p"}, "n", "c", {})
    assert seen[3] == ("icon_redraw", llm.ICONS_SVG_SCHEMA)


def test_pricing_knows_claude_models():
    from app import pricing
    usd, est = pricing.cost_of({"ok": True, "api": "chat", "provider": "anthropic", "model": "claude-opus-5-5",
                                "tokens_in": 1_000_000, "tokens_out": 100_000})
    assert usd == 6.0 and est is False
    usd, est = pricing.cost_of({"ok": True, "api": "chat", "provider": "anthropic", "model": "claude-haiku-5-5",
                                "tokens_in": 1_000_000, "tokens_out": 0})
    assert usd == 0.10


def test_model_for_kind_defaults_and_env_override(monkeypatch):
    monkeypatch.setattr(llm, "CHAT_PROVIDER", "anthropic")
    monkeypatch.setattr(llm, "CLAUDE_MODEL", "claude-sonnet-5-5")
    for k in ("COPY", "COPY_FIT", "COPY_EXTRAS", "ICON_NAMES", "JUDGE"):
        monkeypatch.delenv(f"CLAUDE_MODEL_{k}", raising=False)
    assert all(llm.chat_model(k) == "claude-haiku-5-5" for k in ("copy_fit", "copy_extras", "icon_names"))
    assert llm.chat_model("copy") == "claude-sonnet-5-5", "카피는 글의 품질이 결과물이라 Sonnet"
    assert llm.model_for("judge") == "claude-sonnet-5-5" and llm.model_for(None) == "claude-sonnet-5-5"     # 나머지는 기본값
    monkeypatch.setenv("CLAUDE_MODEL_COPY", "claude-opus-5-5")
    assert llm.model_for("copy") == "claude-opus-5-5" and llm.model_for("copy_fit") == "claude-haiku-5-5"
