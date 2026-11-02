"""RAG 진화 시연: demo/evolution 의 저장된 단계별 결과를 그대로 돌려준다(모델 호출 없음, 과금 없음).
데이터에 실제 브랜드 이미지가 있어 저장소·배포에 올리지 않는다(.gitignore). 폴더가 없으면 404 이고 첫 화면의 입구도 숨는다."""
import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .db import BASE_DIR

router = APIRouter(prefix="/api/evolution")
DATA_DIR = BASE_DIR.parent / "demo" / "evolution"
_IMAGE = re.compile(r"^[\w-]+\.(?:jpg|png)$")


@router.get("")
def evolution():
    f = DATA_DIR / "data.json"
    if not f.exists():
        raise HTTPException(404, "시연 데이터가 없습니다")
    return json.loads(f.read_text(encoding="utf-8"))


@router.get("/image/{name}")
def evolution_image(name: str):
    f = DATA_DIR / name
    if not _IMAGE.match(name) or not f.exists():
        raise HTTPException(404, "이미지를 찾을 수 없습니다")
    return FileResponse(f)
