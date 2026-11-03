"""디버깅용 모델 호출 입출력 로그. 호출(카피·검수·채점 LLM, 이미지 생성·편집, 임베딩) 1건마다 JSON 한 줄을 파일에 쓴다.

calllog 가 DB 에 남기는 기록은 집계용이라 출력이 없다. 여기서는 보낸 입력 전문과 받은 출력을 그대로 남겨,
"모델에 무엇을 보냈고 무엇이 돌아왔나"를 파일 하나로 따라가게 한다. calllog.record 가 기록할 때 함께 부른다.

- 위치: backend/logs/model_calls_YYYY-MM-DD.jsonl (MODEL_LOG_DIR 로 바꾼다). 날짜는 서버 현지 시각
- 켜기/끄기: MODEL_LOG=0 이면 쓰지 않는다. Vercel 에서는 파일이 남지 않으므로 기본으로 끈다
- 이미지·벡터는 크기가 커서 본문 대신 요약(바이트 수, 개수·차원)만 남긴다
- 쓰기 실패는 삼킨다(로그가 생성 흐름을 막지 않게)
"""
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from .db import BASE_DIR

ENABLED = os.getenv("MODEL_LOG", "0" if os.getenv("VERCEL") else "1").strip() not in ("0", "false", "off", "")
LOG_DIR = Path(os.getenv("MODEL_LOG_DIR") or BASE_DIR / "logs")
_lock = threading.Lock()        # 아이콘 렌더는 스레드 여러 개가 동시에 기록한다


def path_for(now: datetime | None = None) -> Path:
    return LOG_DIR / f"model_calls_{(now or datetime.now()).strftime('%Y-%m-%d')}.jsonl"


def write(row: dict, input=None, output=None) -> None:
    """row: calllog 의 호출 행(시각·시안·종류·모델·소요·토큰·오류). input/output 을 붙여 한 줄로 쓴다."""
    if not ENABLED:
        return
    entry = {**row, "input": input, "output": output}
    try:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _lock:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with path_for().open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except (OSError, TypeError, ValueError):
        pass
