"""규격 프리셋 시드. docs/02_매체_프리셋_확정.md 의 확인된 값만 넣는다. 확인일 2026-10-14.

값이 없는 칸은 None/'확인 불가' 로 둔다 (추측 금지).
level: 강제 | 권장 | 제작기본값 | 미표기(공식 문서가 구분을 적지 않음)
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Placement, Platform, PresetVersion

CHECKED = "2026-10-14"
META = "https://www.facebook.com/business/ads-guide/update/image"
G_RDA_A = "https://support.google.com/google-ads/answer/17090561"
G_RDA_B = "https://support.google.com/google-ads/answer/7005917"
G_PMAX = "https://support.google.com/google-ads/answer/17091269"
K_DISPLAY = "https://kakaobusiness.gitbook.io/main/ad/moment/performance/displayad/content-guide"
K_BIZBOARD = "https://kakaobusiness.gitbook.io/main/ad/moment/performance/talkboard/content-guide"
K_CHANNEL = "https://kakaobusiness.gitbook.io/main/ad/moment/messagead/channelmessage/content-guide"
K_API = "http://developers.kakao.com/docs/ko/kakaomoment/creatives-display"
G_PMAX_SPEC = "https://support.google.com/google-ads/answer/17091269"
G_PMAX_OVERVIEW = "https://support.google.com/google-ads/answer/13676244"
G_UPLOADED = "https://support.google.com/google-ads/answer/1722096"


def c(name, value, level, status="확인됨", note=None):
    return {"name": name, "value": value, "level": level, "status": status, "note": note}


def t(field, max_chars=None, level="권장", min_chars=None, max_px=None, min_px=None,
      count_method="글자 수", required=None, note=None):
    return {"field": field, "min_chars": min_chars, "max_chars": max_chars, "min_px": min_px,
            "max_px": max_px, "count_method": count_method, "required": required,
            "level": level, "note": note}


SAFE_STORY = {"top_pct": 14, "bottom_pct": 35, "left_pct": 6, "right_pct": 6, "level": "권장",
              "note": "텍스트·로고·핵심 요소 배치 지양 영역"}

PRESETS = [
    # ---------------- Meta ----------------
    dict(platform="Meta", ad_type="단일 이미지", name="Instagram 피드", kind="완성형",
         size=(1440, 1800), ratio="4:5", status="확인됨", urls=[f"{META}/instagram-feed"],
         conditions=[
             c("파일 형식", "JPG, PNG", "강제"), c("최대 용량", "30MB", "강제"),
             c("최소 너비", "500px", "강제"),
             c("비율 허용 오차", "1%", "강제"),
             # Meta 는 비율을 픽셀 쌍으로 적는다: 400x500 = 4:5(가장 세로로 긴 비율), 191x100 = 1.91:1(가장 가로로 긴 비율).
             # 공식 페이지 '최소 화면 비율 / 최대 화면 비율' 표기 그대로이며 2026-10-19 재확인. review 가 W/H 가 이 범위 안인지 검사한다
             c("최소/최대 화면 비율", "400x500 / 191x100", "강제", "확인됨",
               "Meta 표기: 최소 400x500(=4:5) ~ 최대 191x100(=1.91:1). 2026-10-19 공식 페이지 재확인"),
             c("권장 해상도", "1440x1800", "권장"),
         ],
         texts=[t("기본 문구", 125), t("제목", 40), t("해시태그", 30, note="최대 개수")],
         safe=None, note="안전영역: 확인 불가"),
    dict(platform="Meta", ad_type="단일 이미지", name="Instagram 스토리", kind="완성형",
         size=(1440, 2560), ratio="9:16", status="확인됨", urls=[f"{META}/instagram-story"],
         conditions=[
             c("파일 형식", "JPG, PNG", "강제"), c("최대 용량", "30MB", "강제"),
             c("최소 너비", "500px", "강제"), c("비율 허용 오차", "1%", "강제"),
             c("권장 해상도", "1440x2560", "권장"),
         ],
         texts=[t("기본 문구", 125)], safe=SAFE_STORY, note=None),
    dict(platform="Meta", ad_type="단일 이미지", name="Facebook 피드", kind="완성형",
         size=(1440, 1800), ratio="4:5", status="확인됨",
         urls=[f"{META}/facebook-feed/link-clicks"],
         conditions=[
             c("파일 형식", "JPG, PNG", "강제"), c("최대 용량", "30MB", "강제"),
             c("최소 너비", "600px", "강제"), c("최소 높이", "750px (4:5 기준)", "강제"),
             c("비율 허용 오차", "3%", "강제"),
             c("권장 해상도", "1440x1800", "권장"),
             c("허용 비율 범위", "4:5", "권장", "확인됨",
               "공식 페이지 재확인(2026-10-29): 4:5만 표기. 1.91:1~4:5 범위는 제3자 자료에만 있어 채택하지 않음"),
         ],
         texts=[t("기본 문구", 150, min_chars=50), t("제목", 27)],
         safe=None, note="안전영역: 확인 불가"),
    dict(platform="카카오", ad_type="디스플레이 광고(이미지 네이티브)", name="디스플레이 네이티브 2:1",
         kind="완성형", size=(1200, 600), ratio="2:1", status="확인됨", urls=[K_DISPLAY, K_API],
         conditions=[
             c("최소 크기", "1200x600", "미표기", "확인됨", "필수 여부 구분이 표에 없음"),
             c("파일 형식", "JPG, JPEG, PNG", "미표기"), c("최대 용량", "500KB", "미표기"),
             c("이미지 내 텍스트 크기", "48~164px", "권장"),
             c("이미지 내 텍스트 줄 수", "최대 3줄", "권장"),
             c("폰트 스타일·컬러", "4가지 이상 사용 불가", "강제"),
             c("프로필 이미지", "300x300 이상 1:1, 500KB", "강제"),
         ],
         texts=[t("타이틀", 25, "강제", required=True), t("홍보문구", 45, "강제", required=True),
                t("프로필 이름", 20, "강제", required=True)],
         safe={"top_px": 40, "left_px": 40, "right_px": 40, "bottom_px": 90, "level": "권장",
               "note": "이 영역에 텍스트·로고·주요 오브젝트 배치 지양. 큰 이미지는 같은 비율로 확대"},
         note="세이프존 수치는 원문 표 기준 (검색 요약과 다름, 재대조 필요)"),
    dict(platform="카카오", ad_type="디스플레이 광고(이미지 네이티브)", name="디스플레이 네이티브 1:1",
         kind="완성형", size=(500, 500), ratio="1:1", status="확인됨", urls=[K_DISPLAY, K_API],
         conditions=[
             c("최소 크기", "500x500", "미표기"),
             c("파일 형식", "JPG, JPEG, PNG", "미표기"), c("최대 용량", "500KB", "미표기"),
             c("폰트 스타일·컬러", "4가지 이상 사용 불가", "강제"),
         ],
         texts=[t("타이틀", 25, "강제", required=True), t("홍보문구", 45, "강제", required=True),
                t("프로필 이름", 20, "강제", required=True)],
         safe=None, note="세이프존 수치 없음"),
    dict(platform="카카오", ad_type="카카오톡 비즈보드(성과형)", name="비즈보드 배너 1029×258",
         kind="분리 자산형", size=(1029, 258), ratio=None, status="확인됨",
         urls=[K_BIZBOARD, K_API],
         conditions=[
             c("크기", "1029x258", "강제"), c("파일 형식", "PNG-24 또는 PNG-32", "강제"),
             c("최대 용량", "300KB", "강제"), c("배경", "투명 배경으로 제작", "강제"),
             c("광고주체 표기", "필수", "강제"),
             c("AD 마크 영역", "우측 상단 66x174px 에 오브젝트 겹침 불가", "강제"),
         ],
         texts=[t("배너 카피", None, "강제", min_px=290, count_method="px 길이",
                  note="텍스트 중 하나는 최소 290px (텍스트형 480px)")],
         safe=None, note="문구는 글자 수가 아니라 px 길이 기준"),
    dict(platform="카카오", ad_type="카카오톡 채널 메시지", name="채널 메시지 와이드이미지형 800×600",
         kind="완성형", size=(800, 600), ratio="4:3", status="확인됨", urls=[K_CHANNEL],
         conditions=[
             c("권장 크기", "800x600", "권장"), c("파일 형식", "JPG, JPEG, PNG", "미표기"),
             c("최대 용량", "10MB", "미표기"),
         ],
         texts=[t("내용", 76, "미표기", note="줄바꿈 최대 5회"), t("버튼명", 8, "미표기")],
         safe=None, note="필수 여부 원문 미표기"),
    # ---------------- Google Ads (RDA 제외 후 추가) ----------------
    dict(platform="Google Ads", ad_type="성과 최대화(PMax) 이미지 자산", name="PMax 가로 1.91:1",
         kind="분리 자산형", size=(1200, 628), ratio="1.91:1", status="확인됨", urls=[G_PMAX_SPEC, G_PMAX_OVERVIEW],
         conditions=[
             c("최소 크기", "600x314", "강제"), c("최대 용량", "5MB", "강제"), c("파일 형식", "JPG, PNG", "강제"),
             c("권장 크기", "1200x628", "권장"), c("수량", "1~20장 (권장 4장 이상)", "강제"),
             c("정사각 로고", "1:1, 권장 1200x1200, 최소 128x128 (필수 자산)", "강제"),
             c("문구 수량", "광고 제목 3~15개, 긴 광고 제목 1~5개, 설명 2~5개", "강제"),
         ],
         texts=[t("광고 제목", 30, "강제", required=True, count_method="전각 2자 계산", note="최소 3개, 그중 하나는 15자 이하 권장"),
                t("긴 광고 제목", 90, "강제", required=True, count_method="전각 2자 계산", note="최소 1개, 30자 이상 권장"),
                t("설명", 90, "강제", required=True, count_method="전각 2자 계산", note="최소 2개"),
                t("업체 이름", 25, "강제", required=True, count_method="전각 2자 계산",
                  note="도메인 또는 인증된 상호와 일치, 불필요한 기호·프로모션 문구 금지")],
         safe=None, note="한국어 등 전각 문자는 글자당 2자로 계산 (PMax 문서). 안전영역: 확인 불가",
         assets=["이미지 가로 1.91:1", "정사각 로고 1:1", "광고 제목", "긴 광고 제목", "설명", "업체 이름"]),
    dict(platform="Google Ads", ad_type="성과 최대화(PMax) 이미지 자산", name="PMax 정사각형 1:1",
         kind="분리 자산형", size=(1200, 1200), ratio="1:1", status="확인됨", urls=[G_PMAX_SPEC, G_PMAX_OVERVIEW],
         conditions=[
             c("최소 크기", "300x300", "강제"), c("최대 용량", "5MB", "강제"), c("파일 형식", "JPG, PNG", "강제"),
             c("권장 크기", "1200x1200", "권장"), c("수량", "1~20장 (권장 4장 이상)", "강제"),
             c("정사각 로고", "1:1, 권장 1200x1200, 최소 128x128 (필수 자산)", "강제"),
             c("문구 수량", "광고 제목 3~15개, 긴 광고 제목 1~5개, 설명 2~5개", "강제"),
         ],
         texts=[t("광고 제목", 30, "강제", required=True, count_method="전각 2자 계산", note="최소 3개, 그중 하나는 15자 이하 권장"),
                t("긴 광고 제목", 90, "강제", required=True, count_method="전각 2자 계산", note="최소 1개, 30자 이상 권장"),
                t("설명", 90, "강제", required=True, count_method="전각 2자 계산", note="최소 2개"),
                t("업체 이름", 25, "강제", required=True, count_method="전각 2자 계산",
                  note="도메인 또는 인증된 상호와 일치, 불필요한 기호·프로모션 문구 금지")],
         safe=None, note="한국어 등 전각 문자는 글자당 2자로 계산 (PMax 문서). 안전영역: 확인 불가",
         assets=["이미지 정사각형 1:1", "정사각 로고 1:1", "광고 제목", "긴 광고 제목", "설명", "업체 이름"]),
    dict(platform="Google Ads", ad_type="업로드형 이미지 디스플레이 광고", name="업로드형 300×250",
         kind="완성형", size=(300, 250), ratio="6:5", status="확인됨", urls=[G_UPLOADED],
         conditions=[
             c("크기", "300x250", "미표기", "확인됨", "지원 크기 목록 중 하나. 필수/권장 구분이 문서에 없음"),
             c("파일 형식", "GIF, JPG, PNG", "미표기"), c("최대 용량", "150KB", "미표기", "확인됨", "이미지 광고 기준"),
         ],
         texts=[], safe=None, note="안전영역: 확인 불가. 애니메이션 GIF 규칙(30초 이하, 5FPS 이하)은 정적 이미지에 해당 없음"),
    dict(platform="Google Ads", ad_type="업로드형 이미지 디스플레이 광고", name="업로드형 336×280",
         kind="완성형", size=(336, 280), ratio="6:5", status="확인됨", urls=[G_UPLOADED],
         conditions=[
             c("크기", "336x280", "미표기", "확인됨", "지원 크기 목록 중 하나. 필수/권장 구분이 문서에 없음"),
             c("파일 형식", "GIF, JPG, PNG", "미표기"), c("최대 용량", "150KB", "미표기", "확인됨", "이미지 광고 기준"),
         ],
         texts=[], safe=None, note="안전영역: 확인 불가"),
    dict(platform="Google Ads", ad_type="업로드형 이미지 디스플레이 광고", name="업로드형 320×100",
         kind="완성형", size=(320, 100), ratio="16:5", status="확인됨", urls=[G_UPLOADED],
         conditions=[
             c("크기", "320x100", "미표기", "확인됨", "지원 크기 목록 중 하나. 필수/권장 구분이 문서에 없음"),
             c("파일 형식", "GIF, JPG, PNG", "미표기"), c("최대 용량", "150KB", "미표기", "확인됨", "이미지 광고 기준"),
         ],
         texts=[], safe=None, note="안전영역: 확인 불가"),
    # ---------------- 네이버 (확인 필요 · 검수 불가) ----------------
    dict(platform="네이버", ad_type="미확인", name="지면 미확인", kind="완성형",
         size=None, ratio=None, status="확인 필요", urls=[],
         conditions=[], texts=[], safe=None,
         note="공식 규격 확인 불가(도구에서 공식 도메인 차단). 검수 불가 대상. 비공식 수치는 사용하지 않음"),
]


def seed(db: Session) -> int:
    """아직 없는 지면만 추가한다 (기존 데이터는 건드리지 않는다). 반환: 추가한 지면 수."""
    platforms: dict[str, Platform] = {p.name: p for p in db.scalars(select(Platform)).all()}
    added = 0
    for p in PRESETS:
        exists = db.scalar(select(Placement.id).join(Platform).where(
            Platform.name == p["platform"], Placement.name == p["name"]))
        if exists:
            continue
        plat = platforms.get(p["platform"])
        if plat is None:
            plat = Platform(name=p["platform"])
            db.add(plat)
            db.flush()
            platforms[p["platform"]] = plat
        pl = Placement(platform_id=plat.id, ad_type=p["ad_type"], name=p["name"],
                       content_kind=p["kind"], purpose="광고")
        db.add(pl)
        db.flush()
        w, h = p["size"] if p["size"] else (None, None)
        db.add(PresetVersion(
            placement_id=pl.id, version_no=1, active=True,
            production_width=w, production_height=h, ratio=p["ratio"],
            conditions=p["conditions"], text_limits=p["texts"], safe_area=p["safe"],
            required_assets=p.get("assets", []), status=p["status"], note=p["note"],
            source_urls=p["urls"], checked_at=CHECKED if p["urls"] else None,
        ))
        added += 1
    db.commit()
    return added



def sync_conditions(db: Session) -> int:
    """이미 있는 지면의 활성 규격 버전에 시드의 조건·상태·출처를 다시 반영한다 (공식 표기 재확인 등으로 시드가 바뀌었을 때).
    제작 크기·문구 제한·안전영역은 건드리지 않는다. 반환: 갱신한 지면 수."""
    changed = 0
    for p in PRESETS:
        pl_id = db.scalar(select(Placement.id).join(Platform).where(
            Platform.name == p["platform"], Placement.name == p["name"]))
        if not pl_id:
            continue
        pv = db.scalar(select(PresetVersion).where(PresetVersion.placement_id == pl_id, PresetVersion.active.is_(True))
                       .order_by(PresetVersion.version_no.desc()).limit(1))
        if pv is None:
            continue
        new = {"conditions": p["conditions"], "status": p["status"], "note": p["note"], "source_urls": p["urls"],
               "checked_at": CHECKED if p["urls"] else None}
        if any(getattr(pv, k) != v for k, v in new.items()):
            for k, v in new.items():
                setattr(pv, k, v)
            changed += 1
    db.commit()
    return changed

# 사용자 결정으로 지원 대상에서 제외한 지면. 기존 DB 에 남아 있어도 비활성화한다 (삭제하지 않아 이력은 보존).
EXCLUDED_PRESETS = [("Meta", "Instagram 릴스"), ("Google Ads", "RDA 가로 1.91:1"), ("Google Ads", "RDA 정사각형 1:1"),
                    ("Google Ads", "RDA 로고 1:1"), ("Google Ads", "RDA 로고 4:1")]


def deactivate_excluded(db: Session) -> int:
    n = 0
    for platform, name in EXCLUDED_PRESETS:
        pl = db.scalar(select(Placement).join(Platform).where(Platform.name == platform, Placement.name == name))
        if not pl:
            continue
        for v in pl.versions:
            if v.active:
                v.active = False
                n += 1
    if n:
        db.commit()
    return n
