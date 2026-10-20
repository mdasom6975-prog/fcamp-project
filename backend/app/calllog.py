"""외부 모델 호출 기록. 어느 시안의 어느 단계(배경·변형·아이콘·카피·기능명)에서 몇 번 호출했는지 남긴다.

imagegen 의 일일 카운터(image_usage.json)는 한도 판정용 하루 합계만 세므로, 호출을 줄일 곳을 찾으려면
시안·종류별 내역이 필요하다. 한 줄 JSON(data/api_calls.jsonl)으로 쌓고 summary 로 집계한다.

시안 id 는 라우터 의존성(track_design)이 경로의 design_id 를 ContextVar 에 넣어 전달한다. 모델 호출 코드는
시안을 몰라도 되고, 스레드로 나가는 호출(icons.render_many)은 copy_context 로 값을 넘긴다.
"""
import json
import threading
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from fastapi import Request

from . import pricing
from .db import BASE_DIR

LOG_FILE = BASE_DIR / "data" / "api_calls.jsonl"
KINDS = ("background", "variant", "icon", "copy", "copy_fit", "icon_names", "icon_redraw")
_design: ContextVar[int | None] = ContextVar("calllog_design", default=None)
_lock = threading.Lock()


async def track_design(request: Request) -> None:
    """라우터 의존성. async 라 이벤트 루프 컨텍스트에서 set 되고, 동기 엔드포인트는 그 복사본을 받아 값을 본다."""
    raw = request.path_params.get("design_id")
    try:
        _design.set(int(raw) if raw is not None else None)
    except (TypeError, ValueError):
        _design.set(None)


def current_design() -> int | None:
    return _design.get()


def record(api: str, kind: str, provider: str, model: str, ok: bool = True, ms: int | None = None,
           error: str | None = None, **extra) -> None:
    """호출 1건을 기록한다. 실패해도 남긴다(과금 여부는 ok 로 구분). 기록 실패가 생성 흐름을 막지 않게 OSError 는 삼킨다."""
    now = datetime.now(timezone.utc)
    row = {"ts": now.isoformat(timespec="seconds"), "date": now.strftime("%Y-%m-%d"), "design_id": _design.get(),
           "api": api, "kind": kind, "provider": provider, "model": model, "ok": ok, "ms": ms,
           **{k: v for k, v in extra.items() if v is not None}}
    if error:
        row["error"] = error[:200]
    try:
        with _lock:
            LOG_FILE.parent.mkdir(exist_ok=True)
            with LOG_FILE.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


@contextmanager
def timed(api: str, kind: str, provider: str, model: str, **extra):
    """with 블록 안의 호출 1건을 소요 시간과 함께 기록한다. 예외는 실패로 기록하고 그대로 올린다.
    yield 하는 dict 에 호출자가 tokens_in/tokens_out 등 응답에서 알게 된 값을 넣으면 함께 기록된다."""
    t0 = time.monotonic()
    info: dict = {}
    try:
        yield info
    except Exception as e:
        record(api, kind, provider, model, ok=False, ms=int((time.monotonic() - t0) * 1000),
               error=f"{type(e).__name__}: {e}", **extra, **info)
        raise
    record(api, kind, provider, model, ms=int((time.monotonic() - t0) * 1000), **extra, **info)


def note_usage(info: dict, response) -> None:
    """OpenAI 채팅 응답의 토큰 사용량을 기록 dict 에 넣는다. usage 가 없으면 아무것도 하지 않는다."""
    u = getattr(response, "usage", None)
    if u is None:
        return
    info["tokens_in"] = getattr(u, "prompt_tokens", None)
    info["tokens_out"] = getattr(u, "completion_tokens", None)


def _rows() -> list[dict]:
    try:
        text = LOG_FILE.read_text()
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _with_cost(r: dict) -> dict:
    usd, est = pricing.cost_of(r)
    return {**r, "cost_usd": usd, "cost_krw": pricing.krw(usd), "estimated": est}


def _agg(rows: list[dict]) -> dict:
    """한 묶음의 집계: 건수·실패·소요·토큰·비용. 비용은 요율을 아는 건만 더하고, 하나라도 추정이면 estimated=True."""
    ok = [r for r in rows if r.get("ok")]
    costs = [pricing.cost_of(r) for r in ok]
    known = [c for c, _ in costs if c is not None]
    usd = sum(known) if known else 0.0
    return {"count": len(ok), "failed": len(rows) - len(ok), "ms_total": sum(r.get("ms") or 0 for r in ok),
            "ms_avg": round(sum(r.get("ms") or 0 for r in ok) / len(ok)) if ok else 0,
            "tokens_in": sum(r.get("tokens_in") or 0 for r in ok), "tokens_out": sum(r.get("tokens_out") or 0 for r in ok),
            "cost_usd": round(usd, 4), "cost_krw": pricing.krw(usd), "unpriced": len(ok) - len(known),
            "estimated": any(e for _, e in costs)}


def summary(design_id: int | None = None, date: str | None = None) -> dict:
    """{total, ok, failed, by_kind: {종류: 집계}, by_api: {...}, images, chats, ms_total, cost_usd, cost_krw, estimated}.
    성공 건만 종류별로 센다. 비용은 pricing 의 추정 요율."""
    rows = [r for r in _rows()
            if (design_id is None or r.get("design_id") == design_id) and (date is None or r.get("date") == date)]
    ok = [r for r in rows if r.get("ok")]
    by_api = Counter(r.get("api") for r in ok)
    kinds = {k: _agg([r for r in rows if r.get("kind") == k]) for k in KINDS if any(r.get("kind") == k for r in rows)}
    total = _agg(rows)
    return {"design_id": design_id, "date": date, "total": len(rows), "ok": len(ok), "failed": len(rows) - len(ok),
            "by_kind": kinds, "by_api": dict(by_api),
            "images": sum(v for k, v in by_api.items() if k.startswith("images")),
            "chats": by_api.get("chat", 0),
            "ms_total": total["ms_total"], "tokens_in": total["tokens_in"], "tokens_out": total["tokens_out"],
            "cost_usd": total["cost_usd"], "cost_krw": total["cost_krw"], "unpriced": total["unpriced"],
            "estimated": total["estimated"], "usd_krw": pricing.USD_KRW}


def recent(design_id: int, limit: int = 50) -> list[dict]:
    rows = [r for r in _rows() if r.get("design_id") == design_id]
    return [_with_cost(r) for r in rows[-limit:][::-1]]
