"""규격 라이브러리 '업데이트' 버튼: Meta·Google Ads·카카오 공식 규격 페이지를 다시 가져와 원문 변경 여부를 기록한다.

규격 수치는 AI 추측 금지 원칙에 따라 자동으로 덮어쓰지 않는다. 변경이 감지되면 사람이 원문을 확인한다.
과도한 호출 방지: 전체 업데이트 사이 최소 간격(COOLDOWN_SECONDS)을 서버에서 강제하고, 동시 실행은 하나만 허용한다.
"""
import hashlib
import re
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Placement, Platform, PresetVersion, SourceCheck
from .routers_assets import get_db

router = APIRouter(prefix="/api")

TARGET_PLATFORMS = ["Meta", "Google Ads", "카카오"]
COOLDOWN_SECONDS = 600          # 전체 업데이트 사이 최소 간격
FETCH_TIMEOUT = 15
MAX_BYTES = 3 * 1024 * 1024
_lock = threading.Lock()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _fetch(url: str) -> tuple[int | None, str | None, str | None]:
    """(http_status, content_hash, error). 보이는 텍스트만 해시해 광고·스크립트 변화의 영향을 줄인다."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (spec-update)", "Accept-Language": "ko,en;q=0.8"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            raw = r.read(MAX_BYTES)
            charset = r.headers.get_content_charset() or "utf-8"
            code = r.status
        p = _Text()
        p.feed(raw.decode(charset, "ignore"))
        text = re.sub(r"\s+", " ", " ".join(p.parts)).strip()
        if not text:
            return code, None, "본문 텍스트를 읽지 못했습니다 (스크립트로 그려지는 페이지일 수 있음)"
        return code, hashlib.sha256(text.encode()).hexdigest(), None
    except Exception as e:  # 네트워크·차단·타임아웃 모두 '실패'로 기록
        return getattr(e, "code", None), None, str(e)[:290]


def _sources(db: Session) -> list[tuple[str, str]]:
    rows = db.execute(
        select(Platform.name, PresetVersion.source_urls)
        .join(Placement, Placement.platform_id == Platform.id)
        .join(PresetVersion, PresetVersion.placement_id == Placement.id)
        .where(PresetVersion.active.is_(True), Platform.name.in_(TARGET_PLATFORMS))).all()
    seen: dict[str, str] = {}
    for plat, urls in rows:
        for u in urls or []:
            seen.setdefault(u, plat)
    return [(plat, u) for u, plat in seen.items()]


def _state(db: Session) -> dict:
    checks = {c.url: c for c in db.scalars(select(SourceCheck)).all()}
    last = max((c.last_fetched_at for c in checks.values()), default=None)
    left = 0
    if last:
        left = max(0, COOLDOWN_SECONDS - int((datetime.now() - last).total_seconds()))
    plats = []
    for name in TARGET_PLATFORMS:
        items = [c for c in checks.values() if c.platform == name]
        plats.append({
            "name": name,
            "last_fetched_at": max((c.last_fetched_at for c in items), default=None),
            "sources": [{"url": c.url, "status": c.status, "error": c.error,
                         "changed_at": c.changed_at} for c in items],
        })
    return {"last_run": last, "cooldown_seconds": COOLDOWN_SECONDS, "cooldown_left": left,
            "running": _lock.locked(), "platforms": plats}


@router.get("/presets/update-status")
def update_status(db: Session = Depends(get_db)):
    return _state(db)


@router.post("/presets/update")
def update_specs(db: Session = Depends(get_db)):
    st = _state(db)
    if st["cooldown_left"] > 0:
        return JSONResponse(
            status_code=429, headers={"Retry-After": str(st["cooldown_left"])},
            content={"detail": f"방금 업데이트했습니다. {st['cooldown_left']}초 후 다시 시도할 수 있습니다.",
                     "cooldown_left": st["cooldown_left"]})
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "이미 업데이트가 진행 중입니다")
    try:
        targets = _sources(db)
        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(lambda t: _fetch(t[1]), targets))
        now = datetime.now()
        existing = {c.url: c for c in db.scalars(select(SourceCheck)).all()}
        for (plat, url), (code, digest, err) in zip(targets, results):
            row = existing.get(url)
            if row is None:
                row = SourceCheck(platform=plat, url=url, status="실패")
                db.add(row)
            row.platform, row.last_fetched_at, row.http_status = plat, now, code
            if err or digest is None:
                row.status, row.error = "실패", err      # 이전 해시는 보존해 다음 비교에 쓴다
            elif row.content_hash is None:
                row.status, row.content_hash, row.error = "기준 저장", digest, None
            elif row.content_hash != digest:
                row.status, row.content_hash, row.changed_at, row.error = "변경 감지", digest, now, None
            else:
                row.status, row.error = "변경 없음", None
        db.commit()
    finally:
        _lock.release()
    return _state(db)
