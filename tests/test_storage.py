"""storage.py: 로컬 모드와 Supabase Storage 모드(REST 를 모의 서버로 대체)."""
import httpx
import pytest
from PIL import Image

from app import db, storage


@pytest.fixture
def fake_supabase(monkeypatch):
    """버킷 하나를 dict 로 흉내 낸다. 호출 목록(calls)도 남긴다."""
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    bucket: dict[str, bytes] = {}
    calls: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/storage/v1/")
        calls.append((req.method, path))
        assert req.headers["authorization"] == "Bearer service-key"
        if req.method == "POST" and path.startswith("object/sign/brand-studio/"):
            key = path.removeprefix("object/sign/brand-studio/")
            if key not in bucket:
                return httpx.Response(400, json={"error": "not found"})
            return httpx.Response(200, json={"signedURL": f"/object/sign/brand-studio/{key}?token=t"})
        if req.method == "POST" and path.startswith("object/brand-studio/"):
            bucket[path.removeprefix("object/brand-studio/")] = req.content
            return httpx.Response(200, json={})
        if req.method in ("GET", "HEAD") and path.startswith("object/authenticated/brand-studio/"):
            key = path.removeprefix("object/authenticated/brand-studio/")
            return httpx.Response(200, content=bucket[key]) if key in bucket else httpx.Response(404)
        if req.method == "DELETE" and path == "object/brand-studio":
            import json
            for k in json.loads(req.content)["prefixes"]:
                bucket.pop(k, None)
            return httpx.Response(200, json=[])
        return httpx.Response(500)

    monkeypatch.setattr(storage, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    return bucket, calls


def test_local_mode_roundtrip_and_response(tmp_path):
    assert not storage.remote()
    storage.put("a/b.png", b"png-bytes")
    assert storage.exists("a/b.png") and storage.read_bytes("a/b.png") == b"png-bytes"
    assert (db.STORAGE_DIR / "a" / "b.png").read_bytes() == b"png-bytes"
    assert storage.response("a/b.png", "image/png").status_code == 200
    assert storage.delete("a/b.png") is True and not storage.exists("a/b.png")
    with pytest.raises(FileNotFoundError):
        storage.local("a/b.png")


def test_key_of_normalizes_legacy_absolute_paths_and_rejects_outside():
    inside = db.STORAGE_DIR / "bg_1.png"
    assert storage.key_of(inside) == "bg_1.png" and storage.key_of("exports/e.zip") == "exports/e.zip"
    assert storage.key_of("/etc/passwd") is None and storage.key_of("../x") is None
    assert storage.delete("/etc/passwd") is False       # STORAGE_DIR 밖은 지우지 않는다


def test_supabase_mode_uploads_downloads_and_redirects(fake_supabase):
    bucket, calls = fake_supabase
    assert storage.remote()
    im = Image.new("RGB", (4, 4), "red")
    storage.put_image("bg_1.png", im)
    assert "bg_1.png" in bucket and bucket["bg_1.png"][:4] == b"\x89PNG"
    # 다른 인스턴스(로컬 캐시가 비어 있음)에서 읽기: 내려받아 캐시한다
    (db.STORAGE_DIR / "bg_1.png").unlink()
    assert storage.exists("bg_1.png") is True
    assert not (db.STORAGE_DIR / "bg_1.png").exists(), "exists 는 내려받지 않고 확인만 한다"
    assert storage.local("bg_1.png").read_bytes() == bucket["bg_1.png"]
    # 내려줄 때는 리다이렉트(Vercel 함수 응답 4.5MB 제한 회피), 파일 이름은 download 로 전달
    storage.put("exports/e.zip", b"zip", "application/zip")
    r = storage.response("exports/e.zip", "application/zip", filename="검수완료 1.zip")
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://x.supabase.co/storage/v1/object/sign/brand-studio/exports/e.zip?token=t&download=")
    assert "%EA%B2%80" in loc and " " not in loc
    # 삭제는 버킷과 캐시 모두
    assert storage.delete("bg_1.png") is True
    assert "bg_1.png" not in bucket and not storage.exists("bg_1.png")


def test_supabase_upload_failure_raises_and_leaves_no_cache(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    monkeypatch.setattr(storage, "_client", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="denied"))))
    with pytest.raises(OSError, match="403"):
        storage.put("bg_2.png", b"x")
    assert not (db.STORAGE_DIR / "bg_2.png").exists()


def test_missing_in_both_places(fake_supabase):
    assert storage.exists("nope.png") is False
    with pytest.raises(FileNotFoundError):
        storage.local("nope.png")
    with pytest.raises(FileNotFoundError):
        storage.response("nope.png", "image/png")


def test_transient_transport_error_is_retried_once(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    n = {"calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        if n["calls"] == 1:
            raise httpx.ConnectError("[Errno 16] Device or resource busy")
        return httpx.Response(200, json={})

    monkeypatch.setattr(storage, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    storage.put("bg_9.png", b"x")
    assert n["calls"] == 2 and (db.STORAGE_DIR / "bg_9.png").exists()


def test_persistent_transport_error_still_raises(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(storage, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(httpx.ConnectError):
        storage.put("bg_8.png", b"x")
