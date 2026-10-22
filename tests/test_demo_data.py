"""시연·QA 더미 데이터(brands/*, demo/campaigns/*)가 앱 스키마·규격 라이브러리와 맞고, tools/seed_demo.py 로 실제로 들어가는지 검사한다.
외부 API 는 호출하지 않는다 (임시 DB·임시 저장 폴더)."""
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BRANDS_DIR = ROOT / "brands"
CAMPAIGN_FILES = sorted((ROOT / "demo" / "campaigns").glob("*.json"))
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


ALL_STYLES = {"자동", "실사 인물", "실사 제품·공간", "플랫 일러스트", "3D 렌더", "그래픽 패턴"}


def test_demo_has_enough_variety():
    """브랜드 폴더를 3개(kucha·greentable·learnmate)로 줄였다: 브랜드 3개 이상, 캠페인 4개 이상, 배경 형태 3종 이상."""
    assert len(BRAND_DIRS) >= 3
    assert len(CAMPAIGN_FILES) >= 4
    styles = {_load(f)["brief"]["image_style"] for f in CAMPAIGN_FILES}
    assert styles <= ALL_STYLES and len(styles) >= 3


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
            assert d.name == "kucha", f"{d.name}: {name} 가 없다 (쿠챠만 로고 없이 둔다)"
            continue
        assert f.stat().st_size <= 300 * 1024, "로고는 300KB 이하 (앱 업로드 한도)"
        w, h = Image.open(f).size
        if square:
            assert abs(w / h - 1) <= 0.05 and min(w, h) >= 128, f"정사각 로고는 1:1·128px 이상 ({w}×{h})"


@pytest.mark.parametrize("f", CAMPAIGN_FILES, ids=lambda p: p.stem)
def test_campaign_matches_app_schema(f):
    from app.routers_designs import BriefIn
    c = _load(f)
    assert c["slug"] == f.stem and (BRANDS_DIR / c["brand"] / "brand.json").exists(), "brand 폴더가 있어야 한다"
    brief = BriefIn.model_validate(c["brief"])          # 앱이 거절하면 여기서 실패
    unknown = [p for p in c["placements"] if p not in PLACEMENT_NAMES]
    assert not unknown, f"규격 라이브러리에 없는 이름: {unknown}"
    assert len(set(c["placements"])) == len(c["placements"]), "같은 규격 중복"
    assert brief.key_message and brief.target
    if c.get("product_image"):
        assert (BRANDS_DIR / c["brand"] / c["product_image"]).exists()


def test_banned_phrase_campaigns_really_conflict():
    """'검수 차단 확인'용 캠페인은 핵심 메시지·상품명·설명에 실제로 금지 표현이 들어 있어야 한다."""
    must = {"auroralab_wrinkle": "주름", "mobilgo_refund": "100% 환불", "sonicwave_bestclaim": "세계 최초",
            "ecoloop_claim": "100% 친환경", "ohneulhanjan_stress": "스트레스", "fitday_banned": "무조건"}
    must = {k: v for k, v in must.items() if (ROOT / "demo" / "campaigns" / f"{k}.json").exists()}
    if not must:
        pytest.skip("검수 차단 확인용 캠페인이 데모 세트에 없다 (브랜드 3개로 줄임)")
    for slug, word in must.items():
        c = _load(ROOT / "demo" / "campaigns" / f"{slug}.json")
        text = " ".join([c["brief"]["product_name"], c["brief"]["description"], c["brief"]["key_message"]])
        assert word in text, f"{slug}: 핵심 문구에 '{word}' 가 없다"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import routers_assets, seed
    from app.db import Base
    from app.main import app

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
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


def test_seed_loads_everything_and_is_idempotent(client):
    s = _seed_module()
    logs: list[str] = []
    made = s.seed(client, log=logs.append)
    assert not [m for m in logs if "실패" in m or "찾지 못함" in m or "건너뜀" in m], logs
    brands = client.get("/api/brands").json()
    designs = client.get("/api/designs").json()
    assert len(brands) == len(BRAND_DIRS)
    assert len(designs) == len(CAMPAIGN_FILES)

    again = s.seed(client, log=lambda m: None)
    assert again == made, "두 번째 실행은 새로 만들지 않고 같은 id 를 돌려줘야 한다"
    assert len(client.get("/api/brands").json()) == len(brands)
    assert len(client.get("/api/designs").json()) == len(designs)


def test_seed_campaign_phrases(client):
    """캠페인 입력의 필수·금지 표현이 브랜드 값을 대체하고, 비어 있으면 브랜드 값을 쓴다 (그린테이블 캠페인 2개로 확인)."""
    s = _seed_module()
    made = s.seed(client, log=lambda m: None)
    assert set(made) >= {"greentable", "greentable_fruit", "greentable_morning", "learnmate", "learnmate_15min", "learnmate_typo"}
    d = client.get(f"/api/designs/{made['greentable_fruit']}").json()
    assert d["effective_phrases"]["forbidden_source"] == "캠페인" and "당도 보장" in d["effective_phrases"]["forbidden_phrases"]
    d = client.get(f"/api/designs/{made['greentable_morning']}").json()
    assert d["effective_phrases"]["forbidden_source"] == "브랜드", "캠페인 표현을 비우면 브랜드 값을 쓴다"
