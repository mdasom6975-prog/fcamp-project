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
    assert h["text"]["side"] == "left" and v["text"]["side"] == "bottom"
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
    def bad_side(raw):
        raw["기본"]["가로형"]["text"] = {"side": "top", "align": "left"}     # 가로형은 left/right 만
    def bad_align(raw):
        raw["기본"]["common"]["text"] = {"side": "bottom", "align": "justify"}
    for change, msg in ((missing_tpl, "정사각"), (bad_slot, "product_slot"), (bad_corner, "logo_corners"), (bad_fraction, "margin"),
                        (bad_side, "text.side"), (bad_align, "text.align")):
        with pytest.raises(ValueError, match=msg):
            compose.load_layouts(_layouts_file(tmp_path, change))


# 문구 방향·정렬(text.side·align): 기본 레이아웃 값을 바꾼 시험용 레이아웃으로 합성해 본다
def _textured(size, box) -> Image.Image:
    """단색 바탕의 box 안에만 질감 많은 피사체가 있는 배경."""
    rnd = random.Random(2)
    im = Image.new("RGB", size, (236, 220, 196))
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = box
    for _ in range(1500):
        x, y = rnd.randint(x0, x1 - 60), rnd.randint(y0, y1 - 60)
        d.ellipse([x, y, x + rnd.randint(10, 60), y + rnd.randint(10, 60)], fill=tuple(rnd.randint(120, 255) for _ in range(3)))
    return im


def _with_layout(tmp_path, monkeypatch, tpl, **text):
    from app import compose
    def change(raw):
        raw["기본"][tpl]["text"] = {**raw["기본"]["common"]["text"], **raw["기본"][tpl].get("text", {}), **text}
        if text.get("side") == "right":
            raw["기본"][tpl]["logo_corners"] = ["우상단", "좌상단", "좌하단"]
    monkeypatch.setattr(compose, "LAYOUTS", compose.load_layouts(_layouts_file(tmp_path, change)))
    return compose


def _logo():
    im = Image.new("RGBA", (300, 120), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle([10, 10, 290, 110], fill=(20, 60, 200, 255))
    return im


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def test_text_top_center_sits_above_subject(tmp_path, monkeypatch):
    compose = _with_layout(tmp_path, monkeypatch, "세로형", side="top", align="center")
    _, rep = compose.compose(_textured((1024, 1536), (150, 820, 870, 1450)), 1080, 1350, "이번 주말, 누구일까", FONT, _logo(),
                             None, None, None, None, body=BODY, cta="쿠챠에서 만나기", accent_hex="#3B5BFF")
    els = {e["type"]: e for e in rep["elements"]}
    subj = rep["crop"]["content_box_canvas"]
    assert rep.get("text_zone") == "위" and rep["scrim"]["side"] == "top"
    assert els["cta"]["box"][3] <= subj[1]                       # 문구 블록 전체가 피사체 위에
    assert els["text"]["align"] == "가운데" and els["body"]["align"] == "가운데"
    tx0, _, tx1, _ = els["text"]["box"]
    c0, _, c1, _ = els["cta"]["box"]
    assert abs((c0 - tx0) - (tx1 - c1)) <= 1                     # CTA 버튼도 가운데
    assert not _overlap(els["logo"]["box"], els["text"]["box"])


def test_text_right_column_avoids_left_subject(tmp_path, monkeypatch):
    compose = _with_layout(tmp_path, monkeypatch, "가로형", side="right")
    _, rep = compose.compose(_textured((1536, 1024), (80, 120, 760, 900)), 1200, 628, "이번 주말, 누구일까", FONT, _logo(),
                             None, None, None, None, body=BODY, cta="쿠챠에서 만나기", accent_hex="#3B5BFF")
    els = {e["type"]: e for e in rep["elements"]}
    subj = rep["crop"]["content_box_canvas"]
    cl, ct, cr, cb = rep["content_box"]
    assert rep["scrim"]["side"] == "right"
    assert els["text"]["box"][0] >= subj[2]                      # 피사체 오른쪽 열
    assert els["text"]["box"][2] <= cr and els["cta"]["box"][2] <= cr
    assert not _overlap(els["logo"]["box"], els["text"]["box"])


def test_editing_keeps_scrim_side_and_align(tmp_path, monkeypatch):
    import pytest
    from app import editing
    compose = _with_layout(tmp_path, monkeypatch, "가로형", side="right", align="center")
    _, rep = compose.compose(_textured((1536, 1024), (80, 120, 760, 900)), 1200, 628, "이번 주말", FONT, None,
                             None, None, None, None)
    font = {"source": "provided", "name": "Pretendard"}
    els = {e["type"]: e for e in editing.default_elements(rep, font, None)}
    assert els["shape"]["props"]["side"] == "right" and els["shape"]["props"]["extent"] == rep["scrim"]["extent"]
    assert els["text"]["props"]["align"] == "가운데"
    assert editing.validate_props("shape", els["shape"]["props"], (1200, 628))["side"] == "right"
    with pytest.raises(ValueError):
        editing.validate_props("shape", {**els["shape"]["props"], "side": "가운데"}, (1200, 628))
    # 예전 기록: 문구를 위로 옮긴 합성은 position '위' 만 남아 있다 → 편집에서도 위쪽 스크림
    old = {"template": "세로형", "scrim": {"mode": "세로형", "color": "#000000", "opacity": 0.62, "position": "위"}, "elements": []}
    assert editing.default_elements(old, font, None)[1]["props"]["side"] == "top"


# ---- 레이아웃 3종(상단 문구·중앙 정렬·좌우 반전): layouts.json 의 실제 정의로 확인한다 ----
def test_all_layouts_keep_product_slot_out_of_text_zone():
    """제품 구역은 구도 프롬프트가 문구 자리로 비우라고 한 띠(composition.subject 의 나머지)와 겹치지 않는다."""
    from app import compose
    assert {"기본", "상단 문구", "중앙 정렬", "좌우 반전"} <= set(compose.LAYOUTS)
    for name, tpls in compose.LAYOUTS.items():
        for tpl, t in tpls.items():
            x0, y0, x1, y1 = t["product_slot"]
            k = t["composition"]["subject"]
            side = t["text"]["side"]
            edge = {"bottom": y1 <= k, "top": y0 >= 1 - k, "left": x0 >= 1 - k, "right": x1 <= k}[side]
            assert edge, (name, tpl, side, t["product_slot"])


def test_composition_vars_follow_text_side():
    from app import imagegen
    assert imagegen.composition_vars("세로형") == {"subject_where": "위쪽", "subject_pct": 70, "text_where": "아래쪽",
                                                  "text_pct": 30, "text_surface": "바닥·배경"}
    top = imagegen.composition_vars("세로형", "상단 문구")
    assert (top["subject_where"], top["text_where"], top["text_surface"]) == ("아래쪽", "위쪽", "벽·배경")
    right = imagegen.composition_vars("가로형", "좌우 반전")
    assert (right["subject_where"], right["subject_pct"], right["text_where"]) == ("왼쪽", 65, "오른쪽")
    assert imagegen.product_slot_vars("세로형", layout="상단 문구")["slot"] == "가로 14~86%, 세로 34~94%"


def test_top_text_layout_crops_subject_low_and_puts_text_above():
    from app import compose
    _, rep = compose.compose(_textured((1024, 1536), (300, 900, 720, 1400)), 1080, 1350, "이번 주말, 누구일까", FONT, _logo(),
                             None, None, None, None, body=BODY, cta="쿠챠에서 만나기", accent_hex="#3B5BFF", layout="상단 문구")
    els = {e["type"]: e for e in rep["elements"]}
    subj = rep["crop"]["content_box_canvas"]
    assert rep["layout"] == {"name": "상단 문구", "version": "1.0"}
    assert rep["crop"]["zoom"] > 1 and subj[3] > 1350 * 0.8          # 확대해도 피사체는 아래쪽에 남는다
    assert rep.get("text_zone") == "위" and els["cta"]["box"][3] <= subj[1]
    assert els["logo"]["box"][1] < els["text"]["box"][1]              # 로고는 위 모서리, 문구는 그 아래


def test_mirrored_layout_puts_horizontal_text_right():
    from app import compose
    _, rep = compose.compose(_textured((1536, 1024), (500, 250, 900, 800)), 1200, 628, "이번 주말, 누구일까", FONT, _logo(),
                             None, None, None, None, body=BODY, layout="좌우 반전")
    els = {e["type"]: e for e in rep["elements"]}
    subj = rep["crop"]["content_box_canvas"]
    assert rep["scrim"]["side"] == "right" and els["text"]["box"][0] >= subj[2]
    assert rep["logo_placement"]["corner"] in ("우상단", "좌상단")
    assert not _overlap(els["logo"]["box"], els["text"]["box"])
    p = compose.product_box(1536, 1024, Image.new("RGBA", (400, 800), (0, 0, 0, 255)), "가로형", layout="좌우 반전")
    assert p["box"][2] <= 1536 * 0.6 + 1                              # 제품도 왼쪽 구역


def test_center_layout_aligns_every_template():
    from app import compose
    for size, gen, box in (((1080, 1350), (1024, 1536), (250, 120, 780, 800)), ((1200, 628), (1536, 1024), (800, 150, 1400, 900))):
        _, rep = compose.compose(_textured(gen, box), *size, "이번 주말, 누구일까", FONT, None, None, None, None, None,
                                 cta="쿠챠에서 만나기", accent_hex="#3B5BFF", layout="중앙 정렬")
        els = {e["type"]: e for e in rep["elements"]}
        tx0, _, tx1, _ = els["text"]["box"]
        c0, _, c1, _ = els["cta"]["box"]
        assert els["text"]["align"] == "가운데" and abs((c0 - tx0) - (tx1 - c1)) <= 1


def test_layout_descriptions_are_user_facing():
    """description 은 캠페인 입력 화면에 그대로 보인다. 버전 이력·코드 이름은 history 에 둔다."""
    from app import compose
    for name, tpls in compose.LAYOUTS.items():
        desc = next(iter(tpls.values()))["description"]
        assert desc and not any(w in desc for w in ("1.0 —", "compose", "토큰", "_")), (name, desc)
