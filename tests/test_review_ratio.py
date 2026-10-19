"""Meta '최소/최대 화면 비율'(픽셀 쌍 표기) 조건을 검수가 자동 판정하는지. 외부 API 없음, 임시 이미지 사용."""
from types import SimpleNamespace

from PIL import Image


def _pv(w, h, conds):
    return SimpleNamespace(production_width=w, production_height=h, ratio=None, conditions=conds, safe_area=None,
                           text_limits=[], required_assets=[], placement=SimpleNamespace(content_kind="완성형", name="피드"))


def _run(tmp_path, w, h, conds):
    from app import review
    p = tmp_path / "img.png"
    Image.new("RGB", (w, h), "white").save(p)
    img_row = SimpleNamespace(file_path=str(p))
    brand = {"levels": {}, "forbidden_phrases": [], "required_phrases": [], "colors": [], "fonts": [], "logo_asset_id": None}
    items = review.review_preset(_pv(w, h, conds), img_row, {"headline": "제목", "body": "", "cta": ""}, None, None,
                                 None, None, brand, None, True)
    return {i["item"]: i for i in items}


CONDS = [{"name": "비율 허용 오차", "value": "1%", "level": "강제", "status": "확인됨"},
         {"name": "최소/최대 화면 비율", "value": "400x500 / 191x100", "level": "강제", "status": "확인됨",
          "note": "Meta 표기"}]


def test_ratio_range_passes_for_4_5_and_1_91(tmp_path):
    r = _run(tmp_path, 1440, 1800, CONDS)["화면 비율 범위"]
    assert r["status"] == "통과" and not r["blocking"], r
    r = _run(tmp_path, 1200, 628, CONDS)["화면 비율 범위"]
    assert r["status"] == "통과", r


def test_ratio_range_blocks_outside(tmp_path):
    r = _run(tmp_path, 1440, 2560, CONDS)["화면 비율 범위"]       # 9:16 은 4:5 보다 세로로 길다
    assert r["status"] == "수정 필요" and r["blocking"], r


def test_unverified_condition_still_blocks(tmp_path):
    conds = [dict(CONDS[1], status="확인 필요", note="모호함")]
    r = _run(tmp_path, 1440, 1800, conds)["최소/최대 화면 비율"]
    assert r["status"] == "확인 필요" and r["blocking"]
