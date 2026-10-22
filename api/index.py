"""Vercel Python 함수 진입점. vercel.json 의 rewrite 가 /api/* 를 모두 여기로 보내고, FastAPI 앱이 경로를 나눈다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.main import app  # noqa: E402,F401  (Vercel 이 ASGI 객체 `app` 을 찾는다)
