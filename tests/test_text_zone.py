"""문구 자리: 피사체 아래가 좁아 본문이 빠질 상황이면 피사체 위쪽 자리를 쓴다(본문을 지키는 쪽이 낫다)."""
import random

from PIL import Image, ImageDraw

from tests.test_image_fit import FONT

BODY = "주말에만 만나는 쿠챠 한정 캐릭터 굿즈, 어떤 캐릭터일지 기대해 보세요."
DROPPED = "본문을 이미지에서 뺐습니다"


def _mid_low_subject() -> Image.Image:
    """질감 많은 피사체가 중하단에 있고 위쪽은 비어 있는 배경(아래 자리는 좁고 위 자리는 넉넉하다)."""
    rnd = random.Random(1)
    im = Image.new("RGB", (1024, 1536), (236, 220, 196))
    d = ImageDraw.Draw(im)
    for _ in range(1500):
        x, y = rnd.randint(180, 840), rnd.randint(620, 1250)
        d.ellipse([x, y, x + rnd.randint(10, 60), y + rnd.randint(10, 60)], fill=tuple(rnd.randint(120, 255) for _ in range(3)))
    return im


def _compose(cta):
    from app import compose
    return compose.compose(_mid_low_subject(), 1440, 1800, "이번 주말, 누구일까", FONT, None, None, None, None, None,
                           body=BODY, cta=cta, accent_hex="#3B5BFF")


def test_cta_moves_text_above_subject_instead_of_dropping_body():
    _, rep = _compose("쿠챠에서 만나기")
    assert rep.get("text_zone") == "위"
    assert rep["scrim"].get("position") == "위"
    assert not any(DROPPED in w for w in rep["warnings"])
    types = {e["type"] for e in rep["elements"]}
    assert {"text", "body", "cta"} <= types      # 제목·본문·CTA 가 모두 남는다


def test_stays_below_when_it_fits():
    _, rep = _compose(None)
    assert rep.get("text_zone") is None
    assert "position" not in rep["scrim"]
