"""파일 저장소. 로고·생성 이미지·내보내기 ZIP 을 다룬다.

DB 에는 파일 경로 대신 **키**(STORAGE_DIR 기준 상대 경로, 예: bg_ab12.png · exports/exp_1.zip)를 둔다.
- SUPABASE_URL + SUPABASE_SERVICE_KEY 가 있으면: 원본은 Supabase Storage 버킷(SUPABASE_BUCKET, 기본 brand-studio)에 있고,
  STORAGE_DIR 은 읽을 때 내려받아 두는 캐시다(Vercel 에서는 /tmp, 인스턴스가 바뀌면 비어 있을 수 있다).
- 없으면: STORAGE_DIR 이 곧 저장소다(로컬 개발·테스트).

이미지·ZIP 을 내려줄 때 Vercel 함수 응답은 4.5MB 를 넘길 수 없으므로 Supabase 모드에서는 서명 URL 로 리다이렉트한다.
"""
import io
import mimetypes
import os
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi.responses import FileResponse, RedirectResponse, Response

from . import db

SIGN_TTL = 3600
_client: httpx.Client | None = None


def _cfg() -> tuple[str, str, str] | None:
    url, key = os.getenv("SUPABASE_URL", "").strip().rstrip("/"), os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        return None
    return url, key, os.getenv("SUPABASE_BUCKET", "brand-studio").strip() or "brand-studio"


def remote() -> bool:
    return _cfg() is not None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=60.0)
    return _client


def _call(method: str, path: str, **kw) -> httpx.Response:
    url, key, _ = _cfg()
    h = {"Authorization": f"Bearer {key}", "apikey": key, **kw.pop("headers", {})}
    return _http().request(method, f"{url}/storage/v1/{path}", headers=h, **kw)


def key_of(p: str | Path) -> str | None:
    """DB 에 든 값을 키로 바꾼다. 예전 방식의 절대 경로는 STORAGE_DIR 하위일 때만 상대 경로로 바꾼다. 그 밖이면 None."""
    s = str(p)
    if os.path.isabs(s):
        try:
            return Path(s).resolve().relative_to(db.STORAGE_DIR.resolve()).as_posix()
        except ValueError:
            return None
    k = Path(s).as_posix()
    return None if k.startswith("..") or "/../" in k else k


def _obj(key: str) -> str:
    return f"{_cfg()[2]}/{quote(key)}"


def put(key: str, data: bytes, content_type: str | None = None) -> str:
    """로컬 캐시에 쓰고 Supabase 에도 올린다(같은 키는 덮어쓴다). DB 에 넣을 키를 돌려준다."""
    path = db.STORAGE_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if remote():
        r = _call("POST", f"object/{_obj(key)}", content=data,
                  headers={"Content-Type": content_type or mimetypes.guess_type(key)[0] or "application/octet-stream",
                           "x-upsert": "true"})
        if r.status_code >= 300:
            path.unlink(missing_ok=True)
            raise OSError(f"Supabase Storage 업로드 실패({r.status_code}): {r.text[:200]}")
    return key


def put_image(key: str, im, fmt: str | None = None, **save_kw) -> str:
    buf = io.BytesIO()
    im.save(buf, fmt or ("JPEG" if key.lower().endswith((".jpg", ".jpeg")) else "PNG"), **save_kw)
    return put(key, buf.getvalue())


def local(p: str | Path) -> Path:
    """파일이 든 로컬 경로. 캐시에 없으면 Supabase 에서 내려받는다. 어디에도 없으면 FileNotFoundError."""
    key = key_of(p)
    if key is None:
        path = Path(str(p))                 # STORAGE_DIR 밖의 절대 경로: 있으면 그대로 쓴다
        if path.is_file():
            return path
        raise FileNotFoundError(str(p))
    path = db.STORAGE_DIR / key
    if path.is_file():
        return path
    if remote():
        r = _call("GET", f"object/authenticated/{_obj(key)}")
        if r.status_code == 200:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            return path
    raise FileNotFoundError(key)


def exists(p: str | Path | None) -> bool:
    if not p:
        return False
    key = key_of(p)
    if key is None:
        return Path(str(p)).is_file()
    if (db.STORAGE_DIR / key).is_file():
        return True
    return remote() and _call("HEAD", f"object/authenticated/{_obj(key)}").status_code == 200


def read_bytes(p: str | Path) -> bytes:
    return local(p).read_bytes()


def delete(p: str | Path) -> bool:
    """키에 해당하는 파일을 지운다(로컬 캐시와 Supabase). STORAGE_DIR 밖의 경로는 건드리지 않는다. 지웠으면 True."""
    key = key_of(p)
    if key is None:
        return False
    had = exists(key)
    (db.STORAGE_DIR / key).unlink(missing_ok=True)
    if remote():
        _call("DELETE", f"object/{_cfg()[2]}", json={"prefixes": [key]})
    return had


def signed_url(key: str, filename: str | None = None) -> str:
    r = _call("POST", f"object/sign/{_obj(key)}", json={"expiresIn": SIGN_TTL})
    if r.status_code != 200:
        raise FileNotFoundError(key)
    url = f"{_cfg()[0]}/storage/v1{r.json()['signedURL']}"
    return url + (f"&download={quote(filename)}" if filename else "")


def response(p: str | Path, media_type: str, filename: str | None = None, headers: dict | None = None) -> Response:
    """파일 응답. Supabase 모드는 서명 URL 로 리다이렉트(응답 크기 제한 회피), 로컬 모드는 파일을 직접 내려준다."""
    key = key_of(p)
    if remote() and key is not None:
        return RedirectResponse(signed_url(key, filename), status_code=307,
                                headers={"Cache-Control": "private, max-age=300", **(headers or {})})
    return FileResponse(local(p), media_type=media_type, filename=filename, headers=headers)
