"""외부 모델 호출 기록. 어느 시안의 어느 단계(배경·변형·아이콘·카피·기능명)에서 몇 번 호출했는지 남긴다.

imagegen 의 일일 카운터(image_usage.json)는 한도 판정용 하루 합계만 세므로, 호출을 줄일 곳을 찾으려면
시안·종류별 내역이 필요하다. api_calls 테이블에 한 행(row=JSON)씩 쌓고 summary 로 집계한다.

시안 id 는 라우터 의존성(track_design)이 경로의 design_id 를 ContextVar 에 넣어 전달한다. 모델 호출 코드는
시안을 몰라도 되고, 스레드로 나가는 호출(icons.render_many)은 copy_context 로 값을 넘긴다.

보낸 프롬프트 전문은 행에 넣기엔 길어 prompt_logs 테이블에 따로 두고 행에는 prompt_id 만 남긴다
(호출자가 timed() 의 info 에 prompt_text={"system","user"} 또는 {"prompt"} 를 넣는다). 분석 화면이 읽는다.
"""
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from fastapi import Request

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from . import db, pricing
from .models import ApiCall, PromptLog

KINDS = ("background", "variant", "product", "icon", "copy", "copy_fit", "icon_names", "icon_redraw", "pin", "judge")
_design: ContextVar[int | None] = ContextVar("calllog_design", default=None)


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
    """호출 1건을 기록한다. 실패해도 남긴다(과금 여부는 ok 로 구분). 기록 실패가 생성 흐름을 막지 않게 DB 오류는 삼킨다."""
    now = datetime.now(timezone.utc)
    prompt_text = extra.pop("prompt_text", None)
    row = {"ts": now.isoformat(timespec="seconds"), "date": now.strftime("%Y-%m-%d"), "design_id": _design.get(),
           "api": api, "kind": kind, "provider": provider, "model": model, "ok": ok, "ms": ms,
           **{k: v for k, v in extra.items() if v is not None}}
    if error:
        row["error"] = error[:200]
    pid = _save_prompt(prompt_text, now)
    if pid:
        row["prompt_id"] = pid
    try:
        with db.SessionLocal() as s:
            s.add(ApiCall(design_id=row["design_id"], date=row["date"], row=row))
            s.commit()
    except SQLAlchemyError:
        pass


def _save_prompt(text: dict | None, now: datetime) -> str | None:
    """보낸 프롬프트 전문을 파일 하나로 저장하고 id 를 돌려준다. 내용이 없거나 저장에 실패하면 None (호출 기록은 그대로 남는다)."""
    if not isinstance(text, dict) or not any(v for v in text.values()):
        return None
    pid = f"{now.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    try:
        with db.SessionLocal() as s:
            s.add(PromptLog(id=pid, text={k: v for k, v in text.items() if v}))
            s.commit()
    except SQLAlchemyError:
        return None
    return pid


def prompt_text(prompt_id: str | None) -> dict | None:
    """저장한 프롬프트 전문 {"system","user"} 또는 {"prompt"}. 없으면 None."""
    if not prompt_id or "/" in prompt_id or ".." in prompt_id:
        return None
    try:
        with db.SessionLocal() as s:
            row = s.get(PromptLog, prompt_id)
    except SQLAlchemyError:
        return None
    return row.text if row is not None and isinstance(row.text, dict) else None


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


def note_image_usage(info: dict, response) -> None:
    """OpenAI 이미지 응답(images.generate/edit)의 토큰 사용량을 기록 dict 에 넣는다. gpt-image 계열은 장당이 아니라
    토큰(텍스트 입력·이미지 입력·이미지 출력)으로 과금되므로, 이 값이 있어야 pricing 이 실제 비용을 계산한다.
    tokens_in = 입력 전체, tokens_out = 이미지 출력, tokens_text_in/tokens_image_in = 입력 내역. usage 가 없으면 기록하지 않는다."""
    u = getattr(response, "usage", None)
    if u is None:
        return
    info["tokens_in"] = getattr(u, "input_tokens", None)
    info["tokens_out"] = getattr(u, "output_tokens", None)
    det = getattr(u, "input_tokens_details", None)
    if det is not None:
        info["tokens_text_in"] = getattr(det, "text_tokens", None)
        info["tokens_image_in"] = getattr(det, "image_tokens", None)


_ALL = object()


def _rows(design_id=_ALL) -> list[dict]:
    """기록 순서대로. design_id 를 주면 그 시안만(None 이면 시안 없는 호출만) DB 에서 걸러 읽는다."""
    q = select(ApiCall.row).order_by(ApiCall.id)
    if design_id is not _ALL:
        q = q.where(ApiCall.design_id.is_(None) if design_id is None else ApiCall.design_id == design_id)
    try:
        with db.SessionLocal() as s:
            return list(s.scalars(q).all())
    except SQLAlchemyError:
        return []


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
    rows = [r for r in (_rows() if design_id is None else _rows(design_id)) if date is None or r.get("date") == date]
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


def stage_summary(design_id: int, stages: dict[str, tuple[str, ...]]) -> dict:
    """{단계: 집계}. stages 는 {단계 이름: (종류, ...)} — 분석 화면이 카피/배경/아이콘/채점으로 묶어 본다."""
    rows = _rows(design_id)
    return {name: _agg([r for r in rows if r.get("kind") in kinds]) for name, kinds in stages.items()}


def recent(design_id: int, limit: int = 50) -> list[dict]:
    rows = _rows(design_id)
    return [_with_cost(r) for r in rows[-limit:][::-1]]


def calls(design_id: int) -> list[dict]:
    """시안의 모든 호출(시간순)에 비용을 붙여 돌려준다. 분석 화면용(최근 n건 제한 없음)."""
    return [_with_cost(r) for r in _rows(design_id)]
