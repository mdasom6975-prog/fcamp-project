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


# 배치 토큰(backend/app/layouts.json): 서버가 뜰 때 읽고 검사한다. 잘못된 값은 합성 중이 아니라 시작 때 드러나야 한다.
def _layouts_file(tmp_path, change):
    import json
    from app import compose
    raw = json.loads(compose.LAYOUTS_PATH.read_text(encoding="utf-8"))
    change(raw)
    p = tmp_path / "layouts.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return p


def test_layout_tokens_merge_common_with_template():
    from app import compose
    v, h = compose.layout_tokens(compose.VERTICAL), compose.layout_tokens(compose.HORIZONTAL)
    assert v["margin"] == h["margin"]                     # common 값
    assert v["text_column"] == 1.0 and h["text_column"] == 0.55   # 비율군 값이 덮어쓴다
    assert h["scrim"]["side"] == "left" and v["scrim"]["side"] == "bottom"
    assert compose.layout_tokens(compose.SQUARE, "없는 레이아웃") is compose.layout_tokens(compose.SQUARE)
    assert compose.PRODUCT_SLOT[compose.VERTICAL] == tuple(v["product_slot"])


def test_layouts_reject_bad_values(tmp_path):
    import pytest
    from app import compose
    def missing_tpl(raw):
        del raw["기본"]["정사각"]
    def bad_slot(raw):
        raw["기본"]["세로형"]["product_slot"] = [0.8, 0.1, 0.2, 0.6]
    def bad_corner(raw):
        raw["기본"]["가로형"]["logo_corners"] = ["가운데"]
    def bad_fraction(raw):
        raw["기본"]["common"]["margin"] = 6
    for change, msg in ((missing_tpl, "정사각"), (bad_slot, "product_slot"), (bad_corner, "logo_corners"), (bad_fraction, "margin")):
        with pytest.raises(ValueError, match=msg):
            compose.load_layouts(_layouts_file(tmp_path, change))
