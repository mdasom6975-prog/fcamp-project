"""소상공인용 광고 이미지: 스타일 프리셋과 업종 공통 금지어.

소상공인은 브랜드 규칙이 없을 수 있으므로 '스타일 프리셋' 하나가 브랜드 규칙 한 벌(색·글꼴·이미지 톤·어조·금지어)을 대신한다.
프리셋은 실제 광고에서 자주 쓰이는 구도·색 수·문구량을 사람이 정리해 옮긴 것이다(특정 광고 이미지를 복제하지 않는다).
프리셋마다 숨은 브랜드 한 건("소상공인 · <이름>")을 만들어 기존 카피·이미지·검수·내보내기 흐름을 그대로 쓴다.
"""
from .schemas import BrandIn

BRAND_PREFIX = "소상공인 · "

# 업종과 무관하게 소상공인 광고에서 문제가 되기 쉬운 표현: 최상급·허위 비교, 효능 단정, 의료 표현
COMMON_FORBIDDEN = ["최고", "최저가", "1등", "1위", "100%", "무조건", "완치", "치료", "효과 보장", "부작용 없음",
                    "국내 유일", "세계 최초", "기적", "만병통치"]

# 기본 게시 규격(규격 라이브러리의 지면 이름)
DEFAULT_PLACEMENTS = ["Instagram 피드", "Instagram 스토리", "채널 메시지 와이드이미지형 800×600"]

LEVELS = {"logo": "수정가능", "colors": "권장", "fonts": "권장", "icon_style": "수정가능", "image_tone": "권장",
          "voice": "권장", "required_phrases": "수정가능", "forbidden_phrases": "필수"}

PRESETS = [
    {"id": "today_special", "name": "오늘의 특가", "desc": "제품을 크게, 한 줄 문구가 먼저 보이는 판매 광고",
     "tags": ["특가", "할인", "오늘", "한정", "반찬", "치킨", "분식", "빵", "과일", "정육", "도시락", "마트", "야채", "생선"],
     "colors": ["#E8412C", "#FFB703", "#1D3557", "#FFF8EF", "#2B2118"], "image_style": "실사 제품·공간", "headline_max": 18,
     "tone": "따뜻한 조명 아래 제품이 화면 가운데에 크게 놓인 판매 광고 사진. 선명한 색, 가까이서 크게 담은 구도, 약간의 김·물방울·재료 같은 신선함 단서. 배경은 단순하게.",
     "voice": "짧고 힘 있는 한 줄. 지금 사야 할 이유를 한 문장으로. 가격·할인율은 사용자가 쓴 것만 쓴다."},
    {"id": "cafe_mood", "name": "감성 카페", "desc": "자연광과 여백, 차분한 색의 분위기 광고",
     "tags": ["카페", "커피", "디저트", "케이크", "베이커리", "브런치", "차", "꽃", "소품", "공방", "책", "티"],
     "colors": ["#8B5E3C", "#D9C5B2", "#C97B63", "#FAF5EF", "#3B2F2A"], "image_style": "실사 제품·공간", "headline_max": 20,
     "tone": "창가의 부드러운 자연광, 크림·베이지·우드 톤, 여백이 넉넉한 감성 사진. 얕은 피사계 심도, 따뜻하고 차분한 분위기. 제품은 한두 개만.",
     "voice": "차분하고 감성적인 한두 문장. 과장 없이 분위기를 전한다."},
    {"id": "sale_event", "name": "세일·이벤트", "desc": "강한 색 면과 큰 글자의 눈에 띄는 이벤트 광고",
     "tags": ["세일", "이벤트", "행사", "증정", "쿠폰", "할인", "기념", "오픈 기념", "1+1", "사은품", "리뉴얼"],
     "colors": ["#D7263D", "#F9C80E", "#2E294E", "#FFFFFF", "#1B1B1E"], "image_style": "그래픽 패턴", "headline_max": 16,
     "tone": "강렬한 빨강·노랑 면과 굵은 도형으로 구성한 이벤트 포스터 느낌의 그래픽. 평평하고 선명한 색, 에너지 있는 분위기. 제품 사진이 있으면 중앙에 크게.",
     "voice": "신나고 간결한 구어체 존댓말. 기간·혜택은 사용자가 쓴 것만 쓴다."},
    {"id": "premium_single", "name": "프리미엄 단품", "desc": "어두운 배경에 제품 하나만 돋보이게",
     "tags": ["프리미엄", "선물", "수제", "공방", "향", "주얼리", "한우", "와인", "초콜릿", "도자기", "가죽", "고급", "명품"],
     "colors": ["#1C1C1E", "#C9A24D", "#8E8E93", "#121212", "#F5F1E8"], "image_style": "실사 제품·공간", "headline_max": 16,
     "tone": "어두운 무광 배경 위에 제품 하나를 스튜디오 조명으로 비춘 고급 제품 사진. 금빛 포인트 조명, 은은한 반사, 넓은 여백. 소품 없음.",
     "voice": "절제된 짧은 문장. 품질과 정성을 담담하게 전한다. 과장하지 않는다."},
    {"id": "trust_review", "name": "후기·신뢰", "desc": "따뜻한 톤으로 믿음을 주는 동네 가게 광고",
     "tags": ["미용", "네일", "학원", "병원", "약국", "세탁", "수리", "인테리어", "청소", "필라테스", "요가", "헬스", "펜션", "동네", "단골"],
     "colors": ["#2F6F6D", "#F2C14E", "#E07A5F", "#F7F4EC", "#26322F"], "image_style": "실사 인물", "headline_max": 20,
     "tone": "밝고 따뜻한 실내 조명, 편안하게 웃는 사람과 작업 공간이 함께 보이는 사진. 정돈된 가게 분위기, 눈높이 구도. 과한 보정 없음.",
     "voice": "친근하고 믿음직한 존댓말. 경력·후기·보증처럼 확인할 수 없는 내용은 쓰지 않는다."},
    {"id": "grand_open", "name": "신규 오픈", "desc": "새 가게 소식을 밝고 경쾌하게 알리는 광고",
     "tags": ["오픈", "개업", "신규", "새로", "오픈 예정", "그랜드", "리오픈", "새 단장", "첫"],
     "colors": ["#2A9D8F", "#E9C46A", "#F4A261", "#FFFDF7", "#264653"], "image_style": "플랫 일러스트", "headline_max": 16,
     "tone": "밝은 민트·노랑·주황의 플랫 일러스트. 가게 외관·깃발·풍선 같은 축하 소품, 둥근 형태, 경쾌하고 환영하는 분위기.",
     "voice": "경쾌하고 반가운 존댓말. 위치·날짜는 사용자가 쓴 것만 쓴다."},
]
_BY_ID = {p["id"]: p for p in PRESETS}


def get(preset_id: str) -> dict | None:
    return _BY_ID.get(preset_id)


def public(p: dict) -> dict:
    return {"id": p["id"], "name": p["name"], "desc": p["desc"], "colors": p["colors"], "image_style": p["image_style"],
            "tags": p["tags"][:6]}


def recommend(text: str, limit: int = 3) -> list[str]:
    """입력 문장에 들어 있는 업종·상황 단어로 프리셋 점수를 매긴다(가중: 긴 단어일수록 높게). 일치가 없으면 기본 순서."""
    t = (text or "").replace(" ", "")
    score = {p["id"]: sum(len(k) for k in p["tags"] if k.replace(" ", "") in t) for p in PRESETS}
    ranked = sorted(PRESETS, key=lambda p: (-score[p["id"]], PRESETS.index(p)))
    return [p["id"] for p in ranked][:limit]


def brand_in(p: dict) -> BrandIn:
    """프리셋 → 브랜드 규칙 입력(로고 없음, 금지어는 업종 공통 금지어)."""
    roles = ["주조색", "보조색", "강조색", "배경색", "텍스트색"]
    return BrandIn(
        name=BRAND_PREFIX + p["name"], description=p["desc"], logo_position="자동",
        colors=[{"role": r, "hex": h} for r, h in zip(roles, p["colors"])],
        fonts=[{"role": "제목", "source": "provided", "name": "Pretendard-Bold"},
               {"role": "본문", "source": "provided", "name": "Pretendard-Regular"}],
        image_tone={"description": p["tone"], "reference_asset_ids": []},
        voice={"description": p["voice"], "headline_max_chars": p["headline_max"], "polite": True},
        required_phrases=[], forbidden_phrases=list(COMMON_FORBIDDEN), levels=dict(LEVELS))
