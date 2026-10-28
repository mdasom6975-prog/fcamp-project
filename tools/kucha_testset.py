"""쿠챠 프롬프트 데이터 명세서 v1.0(Kucha_AI_Marketing_Asset_Guidelines_v1.pdf) 8쪽 '테스트 세트 70건'을 만든다.

실행: backend/.venv/bin/python tools/kucha_testset.py build            # demo/kucha_testset/records.jsonl 생성
      backend/.venv/bin/python tools/kucha_testset.py check            # 파일의 70건을 검증기로 돌려 예상 결과와 대조
      backend/.venv/bin/python tools/kucha_testset.py seed [--base http://127.0.0.1:8000] [--group visual|ux] [--limit N] [--dry-run]

- 시각 에셋 정상 48건(자산 유형 4 × 캠페인 목표 3 × 시각 분위기 2 × 시안 2) + UX 카피 12건(문구 목적 3 × 문체 2 × 시안 2)
  + 반려 예상 10건(정상 레코드에서 위반 하나만 넣은 것).
- 시안 2개: 시각 에셋은 피사체 키워드(캡슐 / 캡슐+전시 받침대), UX 카피는 문구 역할(제목 / 버튼)만 다르다.
- 가이드가 값을 주지 않은 항목(소장 욕구 환기의 문구, 자산별 출력 규격, 예상 단가, 문구 글자수 한도)은 이 파일의 상수로 정한 가정이다.
- seed: 정상 60건을 앱의 캠페인(브리프)으로 바꿔 넣는다. 카피·이미지 생성(과금)은 하지 않는다. 반려 10건은 앱에 넣지 않는다.
  쿠챠 브랜드가 없으면 brands/kucha 로 먼저 등록한다. 같은 상품명 캠페인은 다시 만들지 않는다.
"""
import argparse
import copy
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo" / "kucha_testset" / "records.jsonl"
SCHEMA_VERSION = "1.0"
BRAND_PRESET_ID = "kucha_core_v1"
AS_OF = date(2026, 10, 13)          # 가이드의 실시간 정보 확인일. 만료 판정의 기준일
MIN_AGE = 19

# ── 허용 목록 (한글 키워드 → 영문 ID) ──
ASSET_TYPES = {"ad_image": "광고 이미지", "background": "배경 이미지", "generic_3d": "범용 3D 오브젝트",
               "marketing_icon": "마케팅 아이콘", "ux_copy": "UX 문구"}
OBJECTIVES = {"brand_awareness": "브랜드 인지도", "new_collection_focus": "신규 컬렉션 주목",
              "collection_desire": "소장 욕구 환기"}
OBJECTIVE_PHRASE = {"brand_awareness": "처음 보는 성인도 쿠챠를 기억하도록",
                    "new_collection_focus": "새로 나온 컬렉션에 시선이 가도록",
                    "collection_desire": "갖고 싶고 모으고 싶은 마음이 들도록"}
MESSAGE_DIRECTION = {"brand_awareness": "발견의 즐거움과 쿠챠의 첫인상",
                     "new_collection_focus": "발견의 즐거움과 소장 욕구",
                     "collection_desire": "모으는 만족과 소장 욕구"}
MOTIVES = {"new_goods_discovery": "새로운 굿즈 발견", "reveal_anticipation": "무작위 공개의 기대",
           "series_collection": "시리즈 수집", "storage_management": "보관함 관리", "bundle_shipping": "합배송 편의"}
OBJECTIVE_MOTIVE = {"brand_awareness": "new_goods_discovery", "new_collection_focus": "new_goods_discovery",
                    "collection_desire": "series_collection"}
SUBJECTS = {"unbranded_capsule": "무상표 캡슐", "display_stand": "전시 받침대"}
VISUALS = {  # 키워드 → 모델 전달 문구 (가이드 5쪽)
    "bright_neutral": "밝고 중립적인 배경, 균형 잡힌 노출",
    "editorial_commerce": "정돈된 상품 광고 구성, 정보 위계가 분명함",
    "adult_playful": "성인 대상이되 아동 광고처럼 보이지 않게",
    "soft_rounded": "부드럽고 둥근 형태, 깔끔한 윤곽",
    "matte_resin": "무광 레진 재질, 과하지 않은 하이라이트",
    "soft_diffused_studio": "스튜디오 확산광, 자연스럽고 부드러운 그림자",
    "controlled_reveal_glow": "은은한 그라데이션 광원, 카지노 네온 금지",
}
COPY_ROLES = {"headline": "제목", "button": "버튼", "disclosure": "고지"}
COPY_INTENTS = {"lineup_browse": "라인업 탐색", "random_explain": "무작위 방식 설명", "draw_start": "뽑기 시작",
                "result_reveal": "결과 공개", "storage_use": "보관함 사용", "shipping_request": "배송 신청"}
TONES = {"clear": "명확함", "curious": "호기심", "friendly": "친근함", "concise": "간결함", "trustworthy": "신뢰감"}
MAX_CHARS = {"headline": 20, "button": 10, "disclosure": 40}
ENVIRONMENTS = ("test", "staging", "production")

# 금지 프리셋(가이드 8쪽). terms 는 부정 프롬프트에 들어가는 문구
NEGATIVE_PRESETS = {
    "service_v1": ["가격 비교", "디지털 보상", "경품 행사", "투자 수익"],
    "casino_v1": ["카지노", "잭팟", "룰렛", "슬롯머신", "포커칩", "베팅", "코인 폭발"],
    "claim_v1": ["특정 상품 직접 선택", "중복 없음", "시크릿 보장", "희귀 보장", "100% 당첨", "손해 없음",
                 "근거 없는 최저가", "무조건 무료배송"],
    "audience_v1": ["아동·미성년자 대상", "유아 말투", "아동용 완구 광고"],
    "ip_v1": ["가짜 로고", "미승인 캐릭터", "상품 모방", "위조 패키지", "UI·타사 아이콘 복제"],
    "text_v1": ["깨진 한글", "무의미한 문자·숫자", "읽을 수 없는 문구"],
    "quality_v1": ["워터마크", "저해상도", "망가진 형태", "과도한 네온·광택", "빨강-금색 카지노 색상"],
    "context_v1": ["서비스 이용 단계", "사용자 여정", "앱 화면", "버튼", "화살표", "단계 번호", "절차 안내",
                   "순서도", "튜토리얼", "멀티패널 설명"],
}
# 쿠챠 가이드의 교육용 서비스 흐름. 생성 입력·프롬프트에는 어디에도 넣지 않는다
SERVICE_FLOW = "라인업 보기 → 무작위 뽑기 → 결과 확인 → 보관함 → 배송·합배송"
LEAK_TERMS = ("→", "라인업 보기", "사용자 여정", "앱 화면", "순서도", "튜토리얼", "단계 번호", "절차 안내")
CLAIM_TERMS = tuple(t for t in NEGATIVE_PRESETS["claim_v1"] if not t.startswith("근거")) + ("최저가",)
REQUIRED_BASE = ("record_id", "schema_version", "brand_preset_id")
REQUIRED_BY_TYPE = {  # 가이드 7쪽 '자산별 입력 규칙'의 필수 입력
    "ad_image": ("campaign_objective", "audience_motive", "subject_keywords", "output_contract"),
    "background": ("campaign_objective", "subject_keywords", "output_contract"),
    "generic_3d": ("subject_keywords", "output_contract"),
    "marketing_icon": ("subject_keywords", "output_contract"),
    "ux_copy": ("campaign_objective", "audience_motive", "copy_intent", "output_contract"),
}
OUTPUT = {  # 자산별 출력 규격(가정). 광고·배경은 가이드 예시(세로 4:5 2048×2560)
    "ad_image": {"ratio": "4:5", "width": 2048, "height": 2560, "format": "PNG", "alpha": False},
    "background": {"ratio": "4:5", "width": 2048, "height": 2560, "format": "PNG", "alpha": False},
    "generic_3d": {"ratio": "1:1", "width": 2048, "height": 2048, "format": "PNG", "alpha": True},
    "marketing_icon": {"ratio": "1:1", "width": 1024, "height": 1024, "format": "PNG", "alpha": True},
    "ux_copy": {"ratio": None, "width": None, "height": None, "format": "TEXT", "alpha": False},
}
UNIT_COST_KRW = {"ad_image": 150, "background": 150, "generic_3d": 150, "marketing_icon": 60, "ux_copy": 30}
GENERATION_BUDGET_KRW = 1000
SUPPORTED_RATIOS = ("4:5", "1:1", "9:16", "16:9", "1.91:1", "2:1")
RATIO_WORD = {"4:5": "세로 4:5", "1:1": "정사각 1:1"}

ASSET_SENTENCE = {
    "ad_image": "{subject}를 정돈된 상품 광고 구성으로 배치한다.",
    "background": "제품이 없는 배경 이미지로 만들고 {subject}의 질감만 은은하게 암시한다. 문구가 들어갈 여백을 남긴다.",
    "generic_3d": "{subject}를 단독 3D 오브젝트로 만든다. 배경은 투명하게 둔다.",
    "marketing_icon": "{subject}를 단순한 마케팅 아이콘으로 만든다. 둥근 윤곽과 단색 배경을 쓴다.",
}
BRAND_LINE = "쿠챠 파랑 #4C69FE와 보라 #734CFE를 절제해 사용하고 성인 대상의 경쾌한 분위기로 표현한다."

# ── 앱 변환 (brief) ──
APP_STYLE = {"ad_image": ("실사 제품·공간", "크게"), "background": ("그래픽 패턴", "작게"),
             "generic_3d": ("3D 렌더", "크게"), "marketing_icon": ("3D 렌더", "보통"), "ux_copy": ("그래픽 패턴", "작게")}
APP_PLACEMENT = "Instagram 피드"
APP_BRAND_SLUG = "kucha"

COMPILED_FIELDS = ("brand_name", "brand_traits", "asset_type", "campaign_objective", "audience_motive",
                   "subject_keywords", "visual_keywords", "asset_bindings", "output_contract")


def _korean(codes: list[str], table: dict) -> str:
    return "·".join(table.get(c, c) for c in codes)


def compile_prompts(r: dict) -> tuple[str, str]:
    """레코드에서 AI 에 전달할 긍정·금지 프롬프트를 조립한다. 교육용 정보(service_flow)는 읽지 않는다."""
    at = r["asset_type"]
    obj = r.get("campaign_objective")
    if at == "ux_copy":
        pos = (f"쿠챠 {COPY_ROLES.get(r['copy']['role'], r['copy']['role'])} 문구를 작성한다. "
               f"목적: {COPY_INTENTS.get(r['copy']['intent'], r['copy']['intent'])}. "
               f"문체: {TONES.get(r['copy']['tone'], r['copy']['tone'])}, 성인 대상. "
               f"한글 {r['copy']['max_korean_characters']}자 이내, 시안 {r['copy']['variation_count']}개. "
               "확인되지 않은 혜택·확률·가격은 쓰지 않는다.")
    else:
        visual = ", ".join(VISUALS.get(v, v) for v in r["visual_keywords"])
        subject = _korean(r["subject_keywords"], SUBJECTS)
        o = r["output_contract"]
        pos = (f"{visual}. {ASSET_SENTENCE[at].format(subject=subject)} "
               f"{OBJECTIVE_PHRASE.get(obj, '')} 표현한다. {BRAND_LINE} "
               f"이미지 안에는 글자를 생성하지 않고 {RATIO_WORD.get(o['ratio'], o['ratio'])} 비율로 만든다.")
        pos = pos.replace("  ", " ")
    terms: list[str] = []
    for pid in r["negative_preset_ids"]:
        terms += NEGATIVE_PRESETS.get(pid, [])
    neg = ("금지: " + ", ".join(dict.fromkeys(terms)) + ". "
           "서비스 이용 단계·앱 화면·절차 안내·플로차트 생성 금지.")
    return pos, neg


def _base(record_id: str, asset_type: str, group: str) -> dict:
    return {
        "record_id": record_id, "schema_version": SCHEMA_VERSION, "brand_preset_id": BRAND_PRESET_ID,
        "test_group": group, "environment": "test", "locale": "ko-KR", "publishable": False,
        "asset_type": asset_type, "channel": "instagram", "placement": APP_PLACEMENT,
        "audience_age_min": MIN_AGE,
        "asset_bindings": [], "live_claims": [],
        "transaction_rules": "특정 상품 선택 불가 · 중복 가능 · 시크릿 보장 없음",
        "negative_preset_ids": list(NEGATIVE_PRESETS),
        "budget": {"generation_budget_krw": GENERATION_BUDGET_KRW, "max_unit_cost_krw": UNIT_COST_KRW[asset_type] * 2,
                   "campaign_budget_krw": None, "production_tier": "standard",
                   "estimated_unit_cost_krw": UNIT_COST_KRW[asset_type], "variant_count": 2},
        "instruction_context": {"service_flow": SERVICE_FLOW, "compile_to_prompt": False},
        "excluded_context_keys": ["service_flow"], "excluded_context_ids": ["service_flow"],
        "generated_text": None,
    }


def visual_record(n: int, asset_type: str, objective: str, look: str, variant: int) -> dict:
    r = _base(f"KCH-IMG-{n:03d}", asset_type, "visual")
    r.update({
        "campaign_objective": objective, "audience_motive": OBJECTIVE_MOTIVE[objective],
        "message_direction": MESSAGE_DIRECTION[objective],
        "subject_keywords": ["unbranded_capsule"] + (["display_stand"] if variant == 2 else []),
        "visual_keywords": [look, "adult_playful"],
        "variant_no": variant, "output_contract": dict(OUTPUT[asset_type]),
    })
    return r


def ux_record(n: int, intent: str, tone: str, variant: int) -> dict:
    r = _base(f"KCH-UXC-{n:03d}", "ux_copy", "ux")
    role = "headline" if variant == 1 else "button"
    objective = {"draw_start": "brand_awareness", "result_reveal": "new_collection_focus",
                 "shipping_request": "collection_desire"}[intent]
    r.update({
        "campaign_objective": objective, "audience_motive": OBJECTIVE_MOTIVE[objective],
        "message_direction": MESSAGE_DIRECTION[objective],
        "copy": {"role": role, "intent": intent, "tone": tone, "max_korean_characters": MAX_CHARS[role],
                 "variation_count": 2, "required_disclosure": None},
        "copy_intent": intent, "subject_keywords": [], "visual_keywords": [],
        "variant_no": variant, "output_contract": dict(OUTPUT["ux_copy"]),
    })
    return r


def finish(r: dict, status: str = "accepted", code: str | None = None) -> dict:
    r["compiled_positive_prompt"], r["compiled_negative_prompt"] = compile_prompts(r)
    r["compiled_source_fields"] = list(COMPILED_FIELDS)
    r["generation_metadata"] = None     # 제공사·모델·버전·시드·설정·소요·비용·시각·파일 해시: 생성한 뒤에 채운다
    r["evaluation"] = {"rubric_version": "1.0", "evaluator_type": "auto", "expected_status": status,
                       "error_code": code, "final_verdict": None}
    return r


# 반려 10건: (오류 코드, 바탕 레코드 유형, 위반을 넣는 함수)
def _m_missing(r): r.pop("subject_keywords")
def _m_enum(r): r["campaign_objective"] = "price_drop_promo"
def _m_leak(r): r["instruction_context"]["compile_to_prompt"] = True
def _m_ip(r): r["asset_bindings"] = [{"kind": "character", "name": "타사 인기 캐릭터", "approved": False}]
def _m_minor(r): r["audience_age_min"] = 14
def _m_claim(r): r["message_direction"] = "지금 뽑으면 100% 당첨, 손해 없음"
def _m_expired(r): r["live_claims"] = [{"key": "shipping_fee", "value": 3000, "unit": "원", "checked_at": "2026-09-01",
                                         "expires_at": "2026-09-30", "source_url": "https://kucha.kr/legal/terms"}]
def _m_kuji(r): r["subject_keywords"] = ["unbranded_capsule", "kuji_lineup"]
def _m_broken(r): r["generated_text"] = "ㅋㅐㅍㅅㅜㄹ 뽑ㄱㅣ ▒▒ 시작"
def _m_budget(r): r["budget"]["generation_budget_krw"] = 100


REJECTS = [("missing_required", "ad_image", _m_missing), ("invalid_enum", "ad_image", _m_enum),
           ("training_context_leak", "ad_image", _m_leak), ("unapproved_ip", "generic_3d", _m_ip),
           ("minor_targeting", "ad_image", _m_minor), ("unsupported_claim", "ad_image", _m_claim),
           ("expired_live_claim", "ad_image", _m_expired), ("kuji_prelaunch", "background", _m_kuji),
           ("broken_text", "ux_copy", _m_broken), ("over_budget", "marketing_icon", _m_budget)]


def build_records() -> list[dict]:
    out: list[dict] = []
    n = 0
    for at in ("ad_image", "background", "generic_3d", "marketing_icon"):
        for obj in OBJECTIVES:
            for look in ("bright_neutral", "editorial_commerce"):
                for variant in (1, 2):
                    n += 1
                    out.append(finish(visual_record(n, at, obj, look, variant)))
    n = 0
    for intent in ("draw_start", "result_reveal", "shipping_request"):
        for tone in ("clear", "friendly"):
            for variant in (1, 2):
                n += 1
                out.append(finish(ux_record(n, intent, tone, variant)))
    accepted = list(out)
    for i, (code, at, mutate) in enumerate(REJECTS, 1):
        base = next(r for r in accepted if r["asset_type"] == at and r["variant_no"] == 1 and (
            r["copy_intent"] == "draw_start" and r["copy"]["tone"] == "clear" if at == "ux_copy"
            else r["campaign_objective"] == "new_collection_focus" and r["visual_keywords"][0] == "bright_neutral"))
        r = copy.deepcopy(base)
        r.update({"record_id": f"KCH-REJ-{i:03d}", "test_group": "reject", "base_record_id": base["record_id"]})
        mutate(r)
        try:
            finish(r, "rejected", code)         # 위반 레코드도 정상 규칙으로 프롬프트를 조립한다
        except (KeyError, TypeError):           # 필수값이 빠져 조립할 수 없는 경우는 비워 둔다
            r["compiled_positive_prompt"] = r["compiled_negative_prompt"] = None
            r["evaluation"] = {"rubric_version": "1.0", "evaluator_type": "auto", "expected_status": "rejected",
                               "error_code": code, "final_verdict": None}
        if code == "training_context_leak":
            r["compiled_positive_prompt"] += f" {SERVICE_FLOW}"
        out.append(r)
    return out


# ── 검증기: 레코드가 어떤 반려 코드에 걸리는지 ──
def validate(r: dict) -> list[str]:
    """위반한 오류 코드 목록. 비어 있으면 통과(accepted)."""
    codes: list[str] = []
    at = r.get("asset_type")
    need = REQUIRED_BASE + REQUIRED_BY_TYPE.get(at, ())
    if any(r.get(k) in (None, "", []) for k in need):
        return ["missing_required"]
    enum_ok = (at in ASSET_TYPES and r.get("campaign_objective") in OBJECTIVES
               and r.get("audience_motive") in MOTIVES and r.get("environment") in ENVIRONMENTS
               and all(s in SUBJECTS or s == "kuji_lineup" for s in r.get("subject_keywords", []))
               and all(v in VISUALS for v in r.get("visual_keywords", []))
               and all(p in NEGATIVE_PRESETS for p in r.get("negative_preset_ids", []))
               and (at != "ux_copy" or (r["copy"]["role"] in COPY_ROLES and r["copy"]["tone"] in TONES
                                        and r["copy_intent"] in COPY_INTENTS))
               and (at == "ux_copy" or r["output_contract"]["ratio"] in SUPPORTED_RATIOS))
    if not enum_ok:
        return ["invalid_enum"]
    pos = r.get("compiled_positive_prompt") or ""
    if r["instruction_context"].get("compile_to_prompt") or any(t in pos for t in LEAK_TERMS):
        codes.append("training_context_leak")
    if any(not b.get("approved") for b in r.get("asset_bindings", [])):
        codes.append("unapproved_ip")
    if r.get("audience_age_min", 0) < MIN_AGE:
        codes.append("minor_targeting")
    if any(t in (r.get("message_direction") or "") + pos for t in CLAIM_TERMS):
        codes.append("unsupported_claim")
    for c in r.get("live_claims", []):
        if not c.get("source_url") or not c.get("checked_at") or date.fromisoformat(c["expires_at"]) < AS_OF:
            codes.append("expired_live_claim")
            break
    if "kuji_lineup" in r.get("subject_keywords", []):
        codes.append("kuji_prelaunch")
    text = r.get("generated_text") or ""
    if "�" in text or "▒" in text or re.search(r"[ㄱ-ㅣ]{3,}", text):
        codes.append("broken_text")
    b = r["budget"]
    if b["estimated_unit_cost_krw"] * b["variant_count"] > b["generation_budget_krw"] or \
            b["estimated_unit_cost_krw"] > b["max_unit_cost_krw"]:
        codes.append("over_budget")
    return codes


def verdict(r: dict) -> tuple[str, str | None]:
    codes = validate(r)
    return ("rejected", codes[0]) if codes else ("accepted", None)


# ── 앱 변환 ──
def to_campaign(r: dict) -> dict:
    """정상 레코드를 demo/campaigns/*.json 과 같은 모양의 캠페인(브리프)으로 바꾼다."""
    at = r["asset_type"]
    obj = OBJECTIVES[r["campaign_objective"]]
    if at == "ux_copy":
        name = f"{r['record_id']} UX 카피 · {COPY_INTENTS[r['copy_intent']]}({COPY_ROLES[r['copy']['role']]})"
    else:
        name = f"{r['record_id']} {ASSET_TYPES[at]} · {obj}"
    style, scale = APP_STYLE[at]
    return {"slug": r["record_id"].lower(), "brand": APP_BRAND_SLUG, "placements": [r["placement"]],
            "brief": {"product_name": name, "description": r["compiled_positive_prompt"],
                      "key_message": r["message_direction"],
                      "target": f"만 {MIN_AGE}세 이상 성인 · {MOTIVES[r['audience_motive']]}",
                      "image_style": style, "product_scale": scale, "layout": "기본",
                      "required_phrases": [], "forbidden_phrases": []}}


def seed(client, group: str | None = None, limit: int | None = None, dry_run: bool = False, log=print) -> int:
    sys.path.insert(0, str(ROOT / "tools"))
    import seed_demo
    recs = [r for r in load_records() if r["test_group"] != "reject" and (not group or r["test_group"] == group)]
    if limit:
        recs = recs[:limit]
    camps = [to_campaign(r) for r in recs]
    if dry_run:
        for c in camps:
            log(f"{c['slug']:12} {c['brief']['product_name']}")
        return len(camps)
    seed_demo.seed(client, brand_slugs={APP_BRAND_SLUG}, campaign_slugs=set(), log=log)
    brand = next((b for b in client.get("/api/brands").json() if b["name"] == "쿠챠"), None)
    if not brand:
        log("쿠챠 브랜드를 등록하지 못했습니다")
        return 0
    presets = client.get("/api/presets").json()
    by_name = {p["name"]: p["id"] for plat in presets for a in plat["ad_types"] for p in a["presets"]}
    have = {d["brief"]["product_name"] for d in client.get("/api/designs").json() if d["brand"]["id"] == brand["id"]}
    made = 0
    for c in camps:
        if c["brief"]["product_name"] in have:
            continue
        r = client.post("/api/designs", json={"brand_id": brand["id"], "kind": "광고 이미지", "brief": c["brief"],
                                              "preset_version_ids": [by_name[n] for n in c["placements"]]})
        if r.status_code >= 400:
            log(f"{c['slug']} 실패: {r.text}")
            continue
        made += 1
    log(f"캠페인 {made}개 생성 (이미 있음 {len(camps) - made}개)")
    return made


def load_records(path: Path = OUT) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("build", "check", "seed"))
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--group", choices=("visual", "ux"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "build":
        recs = build_records()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
        print(f"{len(recs)}건 → {OUT.relative_to(ROOT)}")
        return 0
    if a.cmd == "check":
        bad = 0
        for r in load_records():
            got = verdict(r)
            want = (r["evaluation"]["expected_status"], r["evaluation"]["error_code"])
            if got != want:
                bad += 1
                print(f"불일치 {r['record_id']}: 기대 {want} · 판정 {got}")
        print(f"불일치 {bad}건")
        return 1 if bad else 0
    if a.dry_run:
        seed(None, a.group, a.limit, True)
        return 0
    import httpx
    with httpx.Client(base_url=a.base, timeout=60.0) as client:
        try:
            client.get("/api/brands").raise_for_status()
        except Exception as e:
            print(f"서버에 연결하지 못했습니다 ({a.base}): {e}", file=sys.stderr)
            return 1
        seed(client, a.group, a.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
