"""brands/* 브랜드 키트가 앱 스키마·규격 라이브러리와 맞고, tools/seed_demo.py 로 실제로 들어가는지 검사한다.
프로젝트에는 시연용 더미 브랜드·캠페인을 두지 않는다. 캠페인 시딩은 임시 폴더에 만든 캠페인으로 확인한다.
외부 API 는 호출하지 않는다 (임시 DB·임시 저장 폴더)."""
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BRANDS_DIR = ROOT / "brands"
BRAND_DIRS = sorted(p.parent for p in BRANDS_DIR.glob("*/brand.json"))
PRESETS = json.loads((ROOT / "specs" / "presets.json").read_text(encoding="utf-8"))["presets"]
PLACEMENT_NAMES = {p["placement"] for p in PRESETS}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_module():
    spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "tools" / "seed_demo.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_primary_colors_are_unique_across_brands():
    """주조색이 같으면 색 혼용을 검수로 가려낼 수 없다 (배경·텍스트의 흰색·검정 같은 중립색은 겹쳐도 된다)."""
    seen: dict[str, str] = {}
    for d in BRAND_DIRS:
        main = next(c["hex"].upper() for c in _load(d / "brand.json")["brand"]["colors"] if c["role"] == "주조색")
        assert main not in seen, f"주조색 {main} 이 {seen[main]} 와 {d.name} 에서 겹친다"
        seen[main] = d.name


@pytest.mark.parametrize("d", BRAND_DIRS, ids=lambda p: p.name)
def test_logo_files_fit_app_limits(d):
    for name, square in (("logo.png", False), ("logo_square.png", True)):
        f = d / name
        if not f.exists():
            continue
        assert f.stat().st_size <= 300 * 1024, "로고는 300KB 이하 (앱 업로드 한도)"
        w, h = Image.open(f).size
        if square:
            assert abs(w / h - 1) <= 0.05 and min(w, h) >= 128, f"정사각 로고는 1:1·128px 이상 ({w}×{h})"


@pytest.fixture
def client(tmp_path, monkeypatch, pg_engine):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app import routers_assets, seed
    from app.db import Base
    from app.main import app

    engine = pg_engine
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False)
    with Local() as db:
        seed.seed(db)
    # 저장소 폴더는 conftest 의 _isolated_state 가 임시 폴더로 바꿔 둔다

    def override():
        with Local() as db:
            yield db
    app.dependency_overrides[routers_assets.get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_seed_brands_without_campaigns_is_idempotent(client):
    """demo/campaigns 가 없어도(기본 상태) 브랜드만 등록되고, 다시 실행해도 새로 만들지 않는다."""
    s = _seed_module()
    logs: list[str] = []
    made = s.seed(client, log=logs.append)
    assert not [m for m in logs if "실패" in m or "찾지 못함" in m or "건너뜀" in m], logs
    brands = client.get("/api/brands").json()
    assert len(brands) == len(BRAND_DIRS)

    again = s.seed(client, log=lambda m: None)
    assert again == made, "두 번째 실행은 새로 만들지 않고 같은 id 를 돌려줘야 한다"
    assert len(client.get("/api/brands").json()) == len(brands)


def test_seed_campaign_from_json(client, tmp_path, monkeypatch):
    """캠페인 JSON(임시 폴더)이 앱 스키마와 규격 이름에 맞게 들어가고, 재실행해도 중복되지 않는다."""
    from app.routers_designs import BriefIn
    s = _seed_module()
    brand = BRAND_DIRS[0].name
    placement = sorted(PLACEMENT_NAMES)[0]
    camp = {"slug": "tmp_campaign", "brand": brand, "placements": [placement],
            "brief": {"product_name": "임시 상품", "description": "", "key_message": "임시 메시지", "target": "임시 타깃",
                      "image_style": "자동", "required_phrases": [], "forbidden_phrases": []}}
    BriefIn.model_validate(camp["brief"])
    cdir = tmp_path / "campaigns"
    cdir.mkdir()
    (cdir / "tmp_campaign.json").write_text(json.dumps(camp, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(s, "CAMPAIGNS_DIR", cdir)

    logs: list[str] = []
    made = s.seed(client, log=logs.append)
    assert not [m for m in logs if "실패" in m or "찾지 못함" in m or "건너뜀" in m], logs
    assert "tmp_campaign" in made and len(client.get("/api/designs").json()) == 1
    assert s.seed(client, log=lambda m: None) == made
    assert len(client.get("/api/designs").json()) == 1
