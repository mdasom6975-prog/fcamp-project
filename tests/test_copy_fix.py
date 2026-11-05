"""6단계 "고쳐 보기"(copyfix): 차단 위반이 든 필드만 모델에 고치게 하고 코드로 재검사해 줄었을 때만 받아들인다. 최대 3회.
모델은 가짜(llm.chat_json)로 바꾼다. 캠페인·지면·합성은 test_image_fit 의 env 픽스처(임시 Postgres·가짜 이미지 생성)."""
import pytest

from app import copyfix, llm
from tests.test_image_fit import env  # noqa: F401  (픽스처)

BAD = "세계 최초 리빙 창고"          # 법적 block 표현 2개('세계 최초'·'최초')가 든 제목
OK = "리빙 창고 대방출"


def _setup(env, headline=BAD, body="본문", cta="보러 가기"):
    """선택 카피 + 합성본(검수가 제목을 합성 레이아웃에서 읽는다)을 만든다. 반환: (client, design_id, preset_id)"""
    c, ids, M = env["client"], env["ids"], env["models"]
    did, pid = ids["design"], ids["presets"]["피드 4:5"]
    with env["Local"]() as db:
        db.add(M.CopyVariant(design_id=did, preset_version_id=pid, variant_no=1, headline=headline, body=body, cta=cta,
                             selected=True, batch_no=1))
        db.commit()
    assert c.post(f"/api/designs/{did}/presets/{pid}/image", json={"regenerate_background": False}).status_code == 200
    return c, did, pid


def _fake(monkeypatch, answers):
    """회차마다 answers 의 다음 답을 돌려준다(마지막 답 반복). 보낸 프롬프트는 seen 에 남긴다."""
    seen = []

    def chat(system, user, kind, schema):
        seen.append((kind, system, user))
        return answers[min(len(seen), len(answers)) - 1]
    monkeypatch.setattr(llm, "chat_json", chat)
    return seen


def _legal_blockers(review, pid):
    g = next(x for x in review["groups"] if x["preset_id"] == pid)
    return [i for i in g["items"] if i["blocking"] and i["category"] in ("법적", "브랜드")]


def test_blocking_violations_match_review_rules():
    brand = {"levels": {"forbidden_phrases": "필수"}, "forbidden_phrases": ["당첨"], "required_phrases": [], "source_text": "", "functional": None}
    v = copyfix.blocking_violations({"headline": BAD, "body": "100% 당첨", "cta": ""}, brand)
    assert {x["phrase"] for x in v} == {"당첨", "세계 최초", "최초", "100%"}
    assert all(x["law"] for x in v)
    assert copyfix.fields_with({"headline": BAD, "body": "100% 당첨", "cta": "보기"}, v) == ["headline", "body"]
    # 입력(source)에 있던 표현은 경고로 내려가므로 차단 목록에 없다. 금지 표현의 구분이 '필수'가 아니면 차단이 아니다
    assert copyfix.blocking_violations({"headline": BAD, "body": "", "cta": ""}, {**brand, "source_text": BAD}) == []
    assert copyfix.blocking_violations({"headline": "당첨", "body": "", "cta": ""}, {**brand, "levels": {}}) == []


def test_fix_adopts_when_blocking_drops_and_reruns_review(env, monkeypatch):
    c, did, pid = _setup(env)
    seen = _fake(monkeypatch, [{"headline": OK, "body": "본문", "cta": "보러 가기"}])
    rv = c.post(f"/api/designs/{did}/review").json()
    assert len(_legal_blockers(rv, pid)) == 2 and rv["fix_enabled"] and rv["fix_rounds"] == {}
    r = c.post(f"/api/designs/{did}/presets/{pid}/copy/fix")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["adopted"] and j["changed"] == ["headline"] and (j["blocking_before"], j["blocking_after"]) == (2, 0) and j["round"] == 1
    assert j["texts"]["headline"] == OK and "차단 2 → 0" in j["note"]
    # 프롬프트: 종류 copy_fix, 위반 표현·근거·제안·고칠 필드가 들어간다
    kind, system, user = seen[0]
    assert kind == "copy_fix" and "'세계 최초'" in user and "표시·광고의 공정화" in user and "[고칠 필드] headline" in user
    # 검수가 바로 다시 돌아 법적 차단이 사라졌고, 합성본(편집 요소)의 제목도 바뀌었다
    assert _legal_blockers(j["review"], pid) == [] and j["review"]["fix_rounds"] == {str(pid): 1}
    els = c.get(f"/api/designs/{did}/presets/{pid}/edit").json()["elements"]
    assert next(e for e in els if e["type"] == "text")["props"]["text"] == OK
    with env["Local"]() as db:
        d = db.get(env["models"].Design, did)
        rec = d.brief["fix_rounds"][str(pid)]
        assert len(rec) == 1 and rec[0]["adopted"] and rec[0]["before"]["headline"] == BAD and rec[0]["after"]["headline"] == OK
        assert rec[0]["model"] and sorted(rec[0]["phrases"]) == ["세계 최초", "최초"]
    # 차단이 없으면 더 누를 수 없다
    assert c.post(f"/api/designs/{did}/presets/{pid}/copy/fix").status_code == 400


def test_fix_stops_after_three_rounds_when_model_keeps_violating(env, monkeypatch):
    c, did, pid = _setup(env)
    _fake(monkeypatch, [{"headline": BAD, "body": "본문", "cta": "보러 가기"}])     # 항상 같은 위반
    for n in (1, 2, 3):
        j = c.post(f"/api/designs/{did}/presets/{pid}/copy/fix").json()
        assert not j["adopted"] and j["round"] == n and j["texts"]["headline"] == BAD and "바꾸지 않아" in j["note"]
    assert "직접 편집" in j["note"]
    r = c.post(f"/api/designs/{did}/presets/{pid}/copy/fix")
    assert r.status_code == 400 and "3/3" in r.json()["detail"]
    assert c.get(f"/api/designs/{did}/review").json()["fix_rounds"] == {str(pid): 3}


@pytest.mark.parametrize("answer, why", [
    ({"headline": "", "body": "", "cta": ""}, "빈 생성"),
    ({"foo": 1}, "스키마 불일치"),
    ({"headline": "죄송하지만 이 요청은 도와드릴 수 없습니다", "body": "본문", "cta": "보러 가기"}, "거절"),
])
def test_fix_keeps_original_on_bad_answers(env, monkeypatch, answer, why):
    c, did, pid = _setup(env)
    _fake(monkeypatch, [answer])
    j = c.post(f"/api/designs/{did}/presets/{pid}/copy/fix").json()
    assert not j["adopted"] and j["texts"]["headline"] == BAD and j["blocking_after"] == 2, why
    assert len(_legal_blockers(j["review"], pid)) == 2


def test_fix_keeps_previous_round_when_next_is_worse(env, monkeypatch):
    c, did, pid = _setup(env)
    # 위반이 든 필드(제목)만 받아들이므로 본문을 바꿔 보내도 무시된다 — 나빠지는 것도 제목에서
    _fake(monkeypatch, [{"headline": "100% 리빙 창고", "body": "100% 본문", "cta": "보러 가기"},        # 2 → 1: 채택(본문 변경은 무시)
                        {"headline": "100% 세계 최초 리빙", "body": "본문", "cta": "보러 가기"}])      # 1 → 3: 이전 회차 유지
    j1 = c.post(f"/api/designs/{did}/presets/{pid}/copy/fix").json()
    assert j1["adopted"] and (j1["blocking_before"], j1["blocking_after"]) == (2, 1) and j1["changed"] == ["headline"]
    assert j1["texts"]["body"] == "본문"
    j2 = c.post(f"/api/designs/{did}/presets/{pid}/copy/fix").json()
    assert not j2["adopted"] and "줄지 않아" in j2["note"] and j2["texts"] == j1["texts"]
    with env["Local"]() as db:
        M = env["models"]
        sel = db.query(M.CopyVariant).filter_by(design_id=did, preset_version_id=pid).one()
        assert sel.body == "본문" and any(v["type"] == "legal" for v in sel.violations)
        els = c.get(f"/api/designs/{did}/presets/{pid}/edit").json()["elements"]
        assert next(e for e in els if e["type"] == "text")["props"]["text"] == "100% 리빙 창고"
        assert [r["adopted"] for r in db.get(M.Design, did).brief["fix_rounds"][str(pid)]] == [True, False]


def test_fix_records_model_failure_as_round(env, monkeypatch):
    c, did, pid = _setup(env)

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(llm, "chat_json", boom)
    j = c.post(f"/api/designs/{did}/presets/{pid}/copy/fix").json()
    assert not j["adopted"] and "모델 호출 실패" in j["note"] and j["round"] == 1


def test_fix_disabled_by_env(env, monkeypatch):
    c, did, pid = _setup(env)
    monkeypatch.setattr(copyfix, "ENABLED", False)
    assert c.get(f"/api/designs/{did}/review").json()["fix_enabled"] is False
    r = c.post(f"/api/designs/{did}/presets/{pid}/copy/fix")
    assert r.status_code == 503 and "COPY_AGENT" in r.json()["detail"]


def test_fix_requires_blocking_violation_and_selected_copy(env):
    c, did, pid = _setup(env, headline=OK)
    r = c.post(f"/api/designs/{did}/presets/{pid}/copy/fix")
    assert r.status_code == 400 and "차단 위반" in r.json()["detail"]
