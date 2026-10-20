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


def _assets_items(assets, limits=None):
    from app import review
    conds = [{"name": "문구 수량", "value": "광고 제목 3~15개, 설명 2~5개", "level": "강제", "status": "확인됨"}]
    pv = _pv(1200, 1200, conds)
    pv.text_limits = limits if limits is not None else [
        {"field": "광고 제목", "max_chars": 10, "count_method": "글자 수"}, {"field": "설명", "max_chars": 90, "count_method": "글자 수"}]
    return {i["item"]: i for i in review.review_text_assets(conds[0]["value"], "강제", assets, pv.text_limits)}


def test_text_assets_count_review():
    it = _assets_items(None)
    assert it["문구 수량"]["status"] == "수정 필요" and it["문구 수량"]["blocking"] and "추가 문구 자산 생성" in it["문구 수량"]["message"]
    it = _assets_items({"광고 제목": ["하나", "둘", "셋"], "설명": ["설명 하나", "설명 둘"]})
    assert it["문구 수량"]["status"] == "통과" and "광고 제목 3개" in it["문구 수량"]["message"]
    # 글자 수를 넘는 문구는 세지 않고 경고로 따로 적는다 → 제목이 2개뿐이라 수정 필요
    it = _assets_items({"광고 제목": ["하나", "둘", "이건 열 글자를 넘는 제목입니다"], "설명": ["a", "b"]})
    assert it["문구 수량"]["status"] == "수정 필요" and "광고 제목 2개/최소 3개" in it["문구 수량"]["message"]
    assert it["추가 문구 글자 수"]["status"] == "경고"
    it = _assets_items({"광고 제목": ["a"] * 16, "설명": ["a", "b"]})
    assert "최대 15개" in it["문구 수량"]["message"]


def test_square_logo_review_prefers_square_asset(tmp_path):
    from types import SimpleNamespace
    from PIL import Image
    from app import review
    p = tmp_path / "img.png"; Image.new("RGB", (500, 500), "white").save(p)
    conds = [{"name": "정사각 로고", "value": "1:1, 최소 128x128", "level": "강제", "status": "확인됨"}]
    def run(brand):
        items = review.review_preset(_pv(500, 500, conds), SimpleNamespace(file_path=str(p)), {"headline": "제목", "body": "", "cta": ""},
                                     None, None, None, None, brand, None, True)
        return {i["item"]: i for i in items}["정사각 로고"]
    base = {"levels": {}, "forbidden_phrases": [], "required_phrases": [], "colors": [], "fonts": [], "logo_asset_id": 1}
    r = run({**base, "logo_dims": (1200, 360)})
    assert r["status"] == "수정 필요" and "정사각 로고" in r["message"] and "따로 등록" in r["message"]
    r = run({**base, "logo_dims": (1200, 360), "logo_square_dims": (1200, 1200)})
    assert r["status"] == "통과" and "정사각 로고 자산" in r["message"]
    r = run({**base, "logo_dims": (1200, 360), "logo_square_dims": (100, 100)})
    assert r["status"] == "수정 필요" and "128" in r["message"]


def test_square_logo_hint_when_latest_brand_version_has_it(tmp_path):
    from types import SimpleNamespace
    from PIL import Image
    from app import review
    p = tmp_path / "img.png"; Image.new("RGB", (500, 500), "white").save(p)
    conds = [{"name": "정사각 로고", "value": "1:1", "level": "강제", "status": "확인됨"}]
    brand = {"levels": {}, "forbidden_phrases": [], "required_phrases": [], "colors": [], "fonts": [], "logo_asset_id": 1,
             "logo_dims": (1200, 360), "latest_has_square_logo": True}
    items = {i["item"]: i for i in review.review_preset(_pv(500, 500, conds), SimpleNamespace(file_path=str(p)),
                                                       {"headline": "제목", "body": "", "cta": ""}, None, None, None, None, brand, None, True)}
    assert items["정사각 로고"]["status"] == "수정 필요" and "최신 규칙 적용" in items["정사각 로고"]["message"]


def test_conflict_ratio_passes_when_all_readings_satisfied():
    """출처별 표기가 다른(충돌) 비율 조건: 실제 비율이 모든 표기를 만족하면 통과, 하나라도 어긋나면 사람이 확인하는 항목."""
    from app import review
    conds = [{"name": "허용 비율 범위", "value": "4:5 (본문 기준)", "level": "권장", "status": "충돌",
              "note": "검색 요약은 1.91:1~4:5 범위로 표기. 본문에는 4:5만 있음. 범위 미확정"},
             {"name": "비율 허용 오차", "value": "3%", "level": "강제", "status": "확인됨"}]
    ok, msg = review.conflict_satisfied("허용 비율 범위", conds[0]["value"], conds[0]["note"], 1440, 1800, conds)
    assert ok is True and "4:5" in msg and "1.91:1~4:5" in msg
    ok, msg = review.conflict_satisfied("허용 비율 범위", conds[0]["value"], conds[0]["note"], 1200, 628, conds)
    assert ok is False and "4:5" in msg
    assert review.conflict_satisfied("안전영역", "확인 불가", None, 100, 100, []) == (None, "출처마다 표기가 달라 자동 판정하지 않습니다 — 사람이 확인")
    assert review.ratio_readings("4:5 (본문)", "1.91:1~4:5") == [("4:5", 0.8, 0.8), ("1.91:1~4:5", 0.8, 1.91)]


def test_conflict_condition_in_review_preset(tmp_path):
    from types import SimpleNamespace
    from PIL import Image
    from app import review
    conds = [{"name": "허용 비율 범위", "value": "4:5 (본문 기준)", "level": "권장", "status": "충돌",
              "note": "검색 요약은 1.91:1~4:5 범위로 표기. 본문에는 4:5만 있음"},
             {"name": "비율 허용 오차", "value": "3%", "level": "강제", "status": "확인됨"},
             {"name": "안전영역", "value": "확인 불가", "level": "강제", "status": "충돌", "note": "출처마다 다름"}]
    brand = {"levels": {}, "forbidden_phrases": [], "required_phrases": [], "colors": [], "fonts": [], "logo_asset_id": None}
    def run(w, h):
        p = tmp_path / f"{w}x{h}.png"; Image.new("RGB", (w, h), "white").save(p)
        items = review.review_preset(_pv(w, h, conds), SimpleNamespace(file_path=str(p)), {"headline": "제목", "body": "", "cta": ""},
                                     None, None, None, None, brand, None, True)
        return {i["item"]: i for i in items}
    it = run(1440, 1800)
    assert it["허용 비율 범위"]["status"] == "통과" and not it["허용 비율 범위"]["blocking"]
    assert it["안전영역"]["status"] == "확인 필요" and it["안전영역"]["needs_human"] and it["안전영역"]["blocking"], "자동 판정 불가 충돌은 사람이 확인"
    it = run(1200, 628)
    assert it["허용 비율 범위"]["status"] == "확인 필요" and it["허용 비율 범위"]["needs_human"] and not it["허용 비율 범위"]["blocking"]
