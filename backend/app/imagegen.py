"""배경 이미지 생성 (OpenAI 이미지 모델). 텍스트·로고는 그리지 않게 하고 합성은 compose 에서 한다."""
import base64
import io
import os

from PIL import Image

from .copywriter import client  # 같은 .env 키 사용

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1")


def api_size(w: int, h: int) -> str:
    r = w / h
    return "1536x1024" if r >= 1.25 else "1024x1536" if r <= 0.8 else "1024x1024"


def build_prompt(brand_name: str, tone: dict, brief: dict, template: str) -> str:
    space = {"가로형": "왼쪽 40%는 단순하고 비어 있는 차분한 영역으로 둔다",
             "정사각": "아래쪽 40%는 단순하고 비어 있는 차분한 영역으로 둔다",
             "세로형": "아래쪽 45%는 단순하고 비어 있는 차분한 영역으로 둔다"}[template]
    parts = [
        f"브랜드 '{brand_name}'의 광고 배경 이미지.",
        f"상품/서비스: {brief.get('product_name')}. {brief.get('description') or ''}",
        f"전달할 분위기: {brief.get('key_message') or '지정 없음'}. 타깃: {brief.get('target') or '지정 없음'}.",
        f"이미지 톤: {tone.get('description') or '지정 없음'}.",
        f"구도: {space}. 이 영역에 문구가 올라간다.",
        "이미지 안에 글자, 숫자, 로고, 워터마크, 간판 문구를 절대 넣지 않는다.",
        "깨끗한 상업 광고 사진 또는 일러스트, 과장 없는 자연스러운 표현.",
    ]
    return " ".join(p for p in parts if p.strip())


def generate_background(prompt: str, w: int, h: int) -> Image.Image:
    # 이미지 생성은 오래 걸릴 수 있어 제한 시간을 늘리고, 자동 재시도는 꺼서 중복 과금을 막는다
    r = client().with_options(timeout=240.0, max_retries=0).images.generate(
        model=IMAGE_MODEL, prompt=prompt, size=api_size(w, h), n=1)
    data = base64.b64decode(r.data[0].b64_json)
    return Image.open(io.BytesIO(data)).convert("RGB")
