"""main.init_app: 배포당 한 번만 초기화, 락 대기, 실패해도 인스턴스가 죽지 않는다."""
import pytest
from sqlalchemy.orm import sessionmaker

from app import db, main, state


@pytest.fixture
def init_env(pg_empty_engine, monkeypatch):
    eng = pg_empty_engine
    Local = sessionmaker(bind=eng, autoflush=False)
    for mod, name in ((main, "engine"), (main, "SessionLocal"), (db, "SessionLocal")):
        monkeypatch.setattr(mod, name, eng if name == "engine" else Local)
    monkeypatch.setattr(main, "_ready", False)
    calls = {"seed": 0}
    real = main.seed
    monkeypatch.setattr(main, "seed", lambda s: (calls.__setitem__("seed", calls["seed"] + 1), real(s))[1])
    return calls


def test_local_runs_every_process_start(init_env, monkeypatch):
    monkeypatch.delenv("VERCEL_GIT_COMMIT_SHA", raising=False)
    main.init_app()
    assert init_env["seed"] == 1 and main._ready
    monkeypatch.setattr(main, "_ready", False)
    main.init_app()
    assert init_env["seed"] == 2, "배포 ID 가 없으면 프로세스마다 확인한다"


def test_vercel_initializes_once_per_deploy(init_env, monkeypatch):
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "abc123")
    main.init_app()
    assert init_env["seed"] == 1 and state.get(main.INIT_KEY) == {"deploy": "abc123"}
    monkeypatch.setattr(main, "_ready", False)       # 같은 배포의 다른 인스턴스(콜드 스타트)
    main.init_app()
    assert init_env["seed"] == 1 and main._ready, "표식이 있으면 테이블 확인·시드를 건너뛴다"
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "def456")   # 새 배포는 다시 한다
    monkeypatch.setattr(main, "_ready", False)
    main.init_app()
    assert init_env["seed"] == 2 and state.get(main.INIT_KEY) == {"deploy": "def456"}


def test_acquire_lock_waits_then_gives_up_or_sees_other_finish(monkeypatch):
    class Conn:
        def __init__(self, answers):
            self.answers = list(answers)
        def execute(self, *a, **k):
            v = self.answers.pop(0)
            return type("R", (), {"scalar": lambda self: v})()

    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    monkeypatch.setattr(main, "_initialized_for_deploy", lambda: False)
    assert main._acquire_lock(Conn([False, False, True])) is True       # 두 번 기다린 뒤 락을 잡는다
    done = iter([False, True])
    monkeypatch.setattr(main, "_initialized_for_deploy", lambda: next(done))
    assert main._acquire_lock(Conn([False, False, False])) is False     # 기다리는 사이 다른 인스턴스가 끝냄
    monkeypatch.setattr(main, "_initialized_for_deploy", lambda: False)
    t = iter([0, 0, 1000])
    monkeypatch.setattr(main.time, "monotonic", lambda: next(t))
    with pytest.raises(TimeoutError):
        main._acquire_lock(Conn([False, False]))


def test_lifespan_and_middleware_survive_init_failure(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "_ready", False)

    def boom():
        raise RuntimeError("DB 연결 실패")
    monkeypatch.setattr(main, "init_app", boom)
    with TestClient(main.app) as c:          # lifespan 이 예외를 삼켜 앱이 뜬다
        assert c.get("/openapi.json").status_code == 200     # 요청도 통과한다(초기화 실패가 앞을 막지 않는다)
