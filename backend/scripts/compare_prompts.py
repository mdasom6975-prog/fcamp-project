"""프롬프트 개선 비교: 같은 모델·품질(gpt-image-1.5 low)로 프롬프트만 바꿔 생성한다. 호출당 과금(low 약 14원).
사용: .venv/bin/python scripts/compare_prompts.py [design_id] [quality]   (결과: storage/compare/prompts/index.html)

10/16 결과: v2(구조화 + 촬영 용어 + 여백 반복 강조)를 채택해 prompts/image/background.md 1.1 과
composition/*.md 1.1 에 반영했다. 아래 v1/v2 는 그 비교 기록이며, 현재 운영 프롬프트는 imagegen.build_prompt 가 만든다.
새 변형을 시험할 때는 v2 를 기준(base)으로 두고 v3 를 추가한다."""
import base64
import html
import io
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import compose, imagegen  # noqa: E402
from app.copywriter import client  # noqa: E402
from app.db import SessionLocal, STORAGE_DIR  # noqa: E402
from app.models import Brand, BrandVersion, Design, PresetVersion  # noqa: E402

OUT = STORAGE_DIR / "compare" / "prompts"
NO_TEXT = "글자, 숫자, 로고, 워터마크, 간판, 포장지 문구, 어떤 언어의 문자도 화면에 절대 없음."
LAYOUT = {
    "세로형": "화면을 위아래로 나눈다. 인물과 상품은 위쪽 55% 안에만, 가로 중앙 50% 띠 안에 배치한다. 아래쪽 45%는 아무것도 없는 매끈한 단색 면(테이블 상판 또는 벽)으로 둔다.",
    "정사각": "화면을 위아래로 나눈다. 인물과 상품은 위쪽 60% 안에만, 좌우 가장자리 10%를 비우고 배치한다. 아래쪽 40%는 아무것도 없는 매끈한 단색 면(테이블 상판 또는 벽)으로 둔다.",
    "가로형": "화면을 좌우로 나눈다. 인물과 상품은 오른쪽 절반, 세로 정중앙 40% 띠 안에만 배치한다. 왼쪽 40%는 아무것도 없는 매끈한 단색 면(벽)으로 둔다.",
}


def v1(brand: str, tone: dict, brief: dict, tpl: str) -> str:
    """구조화 + 촬영 용어: 구도를 맨 앞에, 퍼센트 대신 '카메라를 뒤로 물린 와이드 샷'과 크기 상한을 준다."""
    return "\n".join([
        "[촬영 방식] 광고 배경용 와이드 샷 사진. 카메라를 뒤로 물려 피사체를 멀리서 찍는다. 인물은 허리 위까지만, 얼굴 크기는 화면 높이의 15% 이내.",
        f"[구도] {LAYOUT[tpl]} 아래쪽 빈 면은 문구가 올라갈 자리다. 좌상단·우상단 모서리는 로고 자리라 비워 둔다.",
        f"[내용] 상품/서비스: {brief.get('product_name')}. {brief.get('description') or ''} "
        f"분위기: {brief.get('key_message') or '지정 없음'}. 등장 인물은 타깃({brief.get('target') or '지정 없음'})에 맞춘다.",
        f"[톤] {tone.get('description') or '지정 없음'}. 깨끗한 상업 광고 사진.",
        f"[규칙] 소품은 3개 이내, 배경은 단순하게. {NO_TEXT}",
    ])


def v2(brand: str, tone: dict, brief: dict, tpl: str) -> str:
    """v1 + 여백 지시를 끝에 한 번 더 반복하고 '카피 스페이스'를 명시해 이행률을 높인다."""
    base = v1(brand, tone, brief, tpl)
    return (base + "\n[반복 확인] 이 사진은 문구를 올릴 카피 스페이스가 핵심이다. 지정한 빈 영역에는 사람·물건·그림자·무늬를 "
            "하나도 넣지 않는다. 피사체를 키우지 말고 작게 둔다.")


def gen(prompt: str, quality: str) -> Image.Image:
    r = client().with_options(timeout=300.0, max_retries=0).images.generate(
        model="gpt-image-1.5", prompt=prompt, size="1024x1024", quality=quality, n=1)
    return Image.open(io.BytesIO(base64.b64decode(r.data[0].b64_json))).convert("RGB")


def main() -> None:
    did = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    quality = sys.argv[2] if len(sys.argv) > 2 else "low"
    db = SessionLocal()
    d = db.get(Design, did)
    pv = next(db.get(PresetVersion, p) for p in d.preset_version_ids if db.get(PresetVersion, p).production_width)
    w, h = pv.production_width, pv.production_height
    tpl = compose.template_for(w, h)
    bv, brand = db.get(BrandVersion, d.brand_version_id), db.get(Brand, d.brand_id)
    OUT.mkdir(parents=True, exist_ok=True)
    variants = [("v1 구조화+촬영용어", v1), ("v2 v1+반복강조", v2)]
    cards = []
    for name, fn in variants:
        prompt = fn(brand.name, bv.image_tone or {}, d.brief, tpl)
        im = gen(prompt, quality)
        slug = name.split()[0]
        im.save(OUT / f"{slug}_{quality}.png")
        cards.append(f"<div class=c><h3>{html.escape(name)} ({quality})</h3><img src='{slug}_{quality}.png'>"
                     f"<details><summary>프롬프트</summary><pre>{html.escape(prompt)}</pre></details></div>")
        print("ok", name)
    (OUT / "index.html").write_text("<meta charset=utf-8><title>프롬프트 비교</title><style>.c{display:inline-block;"
        "vertical-align:top;width:48%;margin:1%}img{width:100%}pre{white-space:pre-wrap;font-size:12px}</style>" + "".join(cards),
        encoding="utf-8")


if __name__ == "__main__":
    main()
