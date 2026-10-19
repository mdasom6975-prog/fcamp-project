"""이미지 모델 비교: 같은 프롬프트로 여러 모델을 한 번씩 호출해 결과·소요 시간을 한 페이지에 모은다.
사용: .venv/bin/python scripts/compare_models.py [design_id] [모델이름 일부 ...]   (결과: storage/compare/index.html)
예) scripts/compare_models.py 1 1.5 nano    → 이름에 "1.5" 또는 "nano" 가 들어간 모델만 실행
호출마다 실제 과금이 발생한다(모델당 1장). 키가 없는 모델은 건너뛴다."""
import base64
import html
import io
import os
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import compose, imagegen  # noqa: E402
from app.copywriter import client  # noqa: E402
from app.db import SessionLocal, STORAGE_DIR  # noqa: E402
from app.models import Brand, BrandVersion, Design, PresetVersion  # noqa: E402

OUT = STORAGE_DIR / "compare"
OPENAI_QUALITY = os.getenv("COMPARE_QUALITY", "medium")


def openai_gen(model: str, quality: str | None = None):
    def run(prompt: str) -> Image.Image:
        r = client().with_options(timeout=300.0, max_retries=0).images.generate(
            model=model, prompt=prompt, size="1024x1024", quality=quality or OPENAI_QUALITY, n=1)
        return Image.open(io.BytesIO(base64.b64decode(r.data[0].b64_json))).convert("RGB")
    return run


def cloudflare_gen(prompt: str) -> Image.Image:
    a, t = os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_API_TOKEN"]
    r = httpx.post(f"https://api.cloudflare.com/client/v4/accounts/{a}/ai/run/@cf/black-forest-labs/flux-1-schnell",
                   headers={"Authorization": f"Bearer {t}"}, json={"prompt": prompt, "steps": 4}, timeout=120)
    r.raise_for_status()
    return Image.open(io.BytesIO(base64.b64decode(r.json()["result"]["image"]))).convert("RGB")


ASPECTS = {"1:1": 1.0, "4:5": 0.8, "5:4": 1.25, "3:4": 0.75, "4:3": 1.333, "2:3": 0.667, "3:2": 1.5,
           "9:16": 0.5625, "16:9": 1.778, "21:9": 2.333}
_target = {"ratio": 1.0}   # main() 이 규격 비율로 설정


def gemini_gen(model: str):
    """Nano Banana(Gemini 이미지). 규격과 가장 가까운 지원 비율로 직접 생성한다(크롭 최소화)."""
    def run(prompt: str) -> Image.Image:
        ar = min(ASPECTS, key=lambda k: abs(ASPECTS[k] - _target["ratio"]))
        r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                       headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, timeout=240,
                       json={"contents": [{"parts": [{"text": prompt}]}],
                             "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": ar}}})
        r.raise_for_status()
        for part in r.json()["candidates"][0]["content"]["parts"]:
            data = (part.get("inlineData") or part.get("inline_data") or {}).get("data")
            if data:
                return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
        raise RuntimeError("응답에 이미지가 없습니다")
    return run


MODELS = [  # (표시 이름, 필요한 환경변수, 호출 함수)
    ("gpt-image-1-mini", "OPENAI_API_KEY", openai_gen("gpt-image-1-mini")),
    ("gpt-image-1.5 low", "OPENAI_API_KEY", openai_gen("gpt-image-1.5", "low")),
    ("gpt-image-1.5 medium", "OPENAI_API_KEY", openai_gen("gpt-image-1.5", "medium")),
    ("gpt-image-2", "OPENAI_API_KEY", openai_gen("gpt-image-2")),
    ("FLUX.1 schnell (Cloudflare)", "CLOUDFLARE_API_TOKEN", cloudflare_gen),
    ("nano-banana-2.1 (Google)", "GEMINI_API_KEY", gemini_gen(os.getenv("NANO_BANANA_MODEL", "gemini-nano-banana-2.1"))),
    ("nano-banana-2 (Google)", "GEMINI_API_KEY", gemini_gen("gemini-3.1-flash-image")),
]


def main() -> None:
    did = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    only = [x.lower() for x in sys.argv[2:]]
    db = SessionLocal()
    d = db.get(Design, did)
    pv = next(db.get(PresetVersion, p) for p in d.preset_version_ids
              if db.get(PresetVersion, p).production_width)
    w, h = pv.production_width, pv.production_height
    _target["ratio"] = w / h
    tpl = compose.template_for(w, h)
    bv, brand = db.get(BrandVersion, d.brand_version_id), db.get(Brand, d.brand_id)
    prompt = imagegen.build_prompt(brand.name, bv.image_tone or {}, d.brief, tpl)
    OUT.mkdir(parents=True, exist_ok=True)
    cards = []
    for name, env, fn in MODELS:
        if only and not any(o in name.lower() for o in only):
            continue
        slug = "".join(c if c.isalnum() else "_" for c in name)
        if not os.getenv(env):
            cards.append(f"<div class=c><h3>{html.escape(name)}</h3><p>건너뜀: {env} 없음</p></div>")
            print("skip", name)
            continue
        t0 = time.time()
        try:
            im = fn(prompt)
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            cards.append(f"<div class=c><h3>{html.escape(name)}</h3><p>실패: {html.escape(msg)}</p></div>")
            print("fail", name, msg)
            continue
        sec = time.time() - t0
        im.save(OUT / f"{slug}.png")
        compose.cover_crop(im, w, h).resize((w // 3, h // 3)).save(OUT / f"{slug}_crop.png")
        cards.append(f"<div class=c><h3>{html.escape(name)}</h3><p>{im.width}×{im.height} · {sec:.0f}초</p>"
                     f"<div class=r><figure><img src='{slug}.png'><figcaption>원본</figcaption></figure>"
                     f"<figure><img src='{slug}_crop.png'><figcaption>{w}×{h} 크롭</figcaption></figure></div></div>")
        print("ok", name, f"{sec:.0f}s")
    (OUT / "index.html").write_text(
        "<meta charset=utf-8><title>이미지 모델 비교</title><style>body{font:14px sans-serif;margin:20px}"
        ".c{border:1px solid #ddd;border-radius:8px;padding:12px;margin:0 0 16px}.r{display:flex;gap:12px;align-items:flex-start}"
        "img{max-height:420px;border-radius:4px}figure{margin:0}figcaption{color:#666;font-size:12px}</style>"
        f"<h2>이미지 모델 비교</h2><p>품질={OPENAI_QUALITY} (OpenAI 모델) · 규격 {w}×{h} ({tpl})</p>"
        f"<details><summary>프롬프트</summary><p>{html.escape(prompt)}</p></details>" + "".join(cards), encoding="utf-8")
    print("->", OUT / "index.html")


if __name__ == "__main__":
    main()
