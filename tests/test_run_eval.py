"""eval/run_eval.py — 임시 DB 에 시안·검수 결과를 넣고 보고서 집계와 출력 파일을 확인한다. 외부 API 없음."""
import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def _load_run_eval():
    spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_eval"] = mod
    spec.loader.exec_module(mod)
    return mod


def _seed(tmp_path):
    """브랜드 1 · 지면 2(피드=통과, 스토리=수정 필요 차단) · 아이콘 없음 · 호출 기록 2건."""
    from app import db
    from app.models import (ApiCall, Brand, BrandVersion, Design, GeneratedImage, Placement, Platform,
                            PresetVersion, ReviewResult)
    storage = tmp_path / "storage"
    storage.mkdir(exist_ok=True)
    with db.SessionLocal() as s:
        b = Brand(name="테스트브랜드"); s.add(b); s.flush()
        bv = BrandVersion(brand_id=b.id, version_no=1); s.add(bv); s.flush()
        b.current_version_id = bv.id
        plat = Platform(name="Meta"); s.add(plat); s.flush()
        pvs = []
        for name, size in (("피드", (1440, 1800)), ("스토리", (1440, 2560))):
            pl = Placement(platform_id=plat.id, ad_type="단일 이미지", name=name, content_kind="완성형"); s.add(pl); s.flush()
            pv = PresetVersion(placement_id=pl.id, version_no=1, active=True, production_width=size[0],
                               production_height=size[1], status="확인됨", conditions=[], text_limits=[], source_urls=[])
            s.add(pv); s.flush(); pvs.append(pv)
        d = Design(brand_id=b.id, brand_version_id=bv.id, preset_version_ids=[pvs[0].id, pvs[1].id],
                   brief={"product_name": "테스트 상품"}, kind="광고 이미지", status="미검수")
        s.add(d); s.flush()
        for pv in pvs:
            p = storage / f"cmp_{pv.id}.png"
            Image.new("RGB", (pv.production_width, pv.production_height), "white").save(p)
            s.add(GeneratedImage(design_id=d.id, preset_version_id=pv.id, kind="composite", file_path=str(p),
                                 width=pv.production_width, height=pv.production_height, layout={}))
        s.add(ReviewResult(design_id=d.id, preset_version_id=pvs[0].id, category="규격", item="이미지 크기", status="통과"))
        s.add(ReviewResult(design_id=d.id, preset_version_id=pvs[1].id, category="규격", item="이미지 크기", status="통과"))
        s.add(ReviewResult(design_id=d.id, preset_version_id=pvs[1].id, category="브랜드", item="금지 표현",
                           status="수정 필요", level="필수", message="'최저가' 포함", blocking=True))
        s.add(ReviewResult(design_id=d.id, preset_version_id=pvs[1].id, category="브랜드", item="이미지 톤",
                           status="확인 필요", needs_human=True))
        s.add(ApiCall(design_id=d.id, date="2026-10-23", row={"api": "chat", "kind": "copy", "provider": "openai",
                      "model": "gpt-5.4-mini", "ok": True, "ms": 1000, "tokens_in": 1000, "tokens_out": 500}))
        s.add(ApiCall(design_id=d.id, date="2026-10-23", row={"api": "images.generate", "kind": "background",
                      "provider": "openai", "model": "gpt-image-1", "ok": True, "ms": 9000, "quality": "low", "size": "1024x1024"}))
        s.commit()
        return d.id


def test_report_aggregates_and_files(tmp_path):
    from app import db
    run_eval = _load_run_eval()
    did = _seed(tmp_path)
    data = run_eval.build_report(db.SessionLocal)
    S = data["summary"]
    assert S["designs"] == 1 and S["presets"] == 2 and S["presets_reviewed"] == 2
    assert S["presets_ready"] == 1 and S["pass_rate"] == 0.5          # 피드 통과, 스토리 차단
    assert S["status_counts"] == {"통과": 2, "경고": 0, "수정 필요": 1, "확인 필요": 1}
    assert S["blocking_items"] == [{"category": "브랜드", "item": "금지 표현", "count": 1}]
    assert S["cost"]["calls"] == 2 and S["cost"]["cost_krw"] > 0 and S["cost"]["ms_total"] == 10000
    assert [m["model"] for m in S["models"]["image"]] == ["gpt-image-1"]
    assert S["models"]["copy"][0]["pass_rate"] == 0.5
    d = data["designs"][0]
    assert d["id"] == did and not d["export_ready"] and d["brand"]["name"] == "테스트브랜드"
    assert all(g["thumb"] and g["thumb"].startswith("data:image/jpeg") for g in d["groups"])
    story = next(g for g in d["groups"] if g["name"].endswith("스토리"))
    assert story["problems"][0]["blocking"] and not story["export_ready"]

    out = run_eval.write_outputs(data, tmp_path / "report")
    html = out["report"].read_text(encoding="utf-8")
    assert "금지 표현" in html and "data:image/jpeg" in html and "50%" in html
    js = json.loads(out["json"].read_text(encoding="utf-8"))
    assert "thumb" not in js["designs"][0]["groups"][0]        # JSON 에는 썸네일을 넣지 않는다
    rows = out["csv"].read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) == 3 and rows[0].startswith("시안,브랜드")


def test_unreviewed_design_is_counted_but_not_in_pass_rate(tmp_path):
    from app import db
    from app.models import ReviewResult
    run_eval = _load_run_eval()
    _seed(tmp_path)
    with db.SessionLocal() as s:
        for r in s.query(ReviewResult).all():
            r.valid = False
        s.commit()
    S = run_eval.build_report(db.SessionLocal)["summary"]
    assert S["designs"] == 1 and S["designs_unreviewed"] == 1
    assert S["presets_reviewed"] == 0 and S["pass_rate"] is None


def test_select_by_design_id_and_brand(tmp_path):
    from app import db
    run_eval = _load_run_eval()
    did = _seed(tmp_path)
    assert [d["id"] for d in run_eval.build_report(db.SessionLocal, design_ids=[did])["designs"]] == [did]
    assert run_eval.build_report(db.SessionLocal, design_ids=[did + 99])["designs"] == []
    assert run_eval.build_report(db.SessionLocal, brand="없는브랜드")["designs"] == []
    assert len(run_eval.build_report(db.SessionLocal, brand="테스트브랜드")["designs"]) == 1


def test_cross_review_with_other_brand_rules(tmp_path):
    """--as-brand: 같은 시안을 다른 브랜드 규칙(필수 표현 '쿠챠', 금지 '최저가')으로 판정하면 결과가 달라진다. DB 는 바뀌지 않는다."""
    from app import db
    from app.models import ReviewResult
    run_eval = _load_run_eval()
    _seed(tmp_path)
    rules_file = tmp_path / "brand.json"
    rules_file.write_text(json.dumps({"brand": {
        "name": "쿠챠", "colors": [{"role": "주조색", "hex": "#4C69FE"}, {"role": "텍스트색", "hex": "#1B1F2A"}],
        "fonts": [], "required_phrases": ["쿠챠"], "forbidden_phrases": ["최저가"],
        "levels": {"required_phrases": "필수", "forbidden_phrases": "필수", "logo": "필수", "colors": "필수"}},
        "meta": {"logo": {"file": "logo.svg", "aspect": 3.42}}}, ensure_ascii=False), encoding="utf-8")
    with db.SessionLocal() as s:
        before = s.query(ReviewResult).count()
    data = run_eval.build_report(db.SessionLocal, as_brand=rules_file)
    with db.SessionLocal() as s:
        assert s.query(ReviewResult).count() == before          # 저장된 검수 결과는 그대로
    X = data["cross"]
    assert X["brand"] == "쿠챠" and X["logo_aspect"] == 3.42 and X["colors"] == ["#4C69FE", "#1B1F2A"]
    XS = X["summary"]
    assert XS["reviewed"] == 2 and XS["orig_ready"] == 1 and XS["cross_ready"] == 0
    items = {(c["category"], c["item"]) for c in XS["changes"]}
    assert ("문구", "사용할 카피") in items                  # 시드에는 선택한 카피가 없어 교차 검수에서 차단된다
    cmp = data["designs"][0]["cross"]["compare"]
    assert len(cmp) == 2 and all(not g["cross_ready"] for g in cmp)
    assert all("items_full" not in g for g in data["designs"][0]["groups"])
    html = run_eval.render_html(data)
    assert "교차 검수" in html and "쿠챠" in html
