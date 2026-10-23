"""법령 변경 점검: prompts/legal/ 의 규칙·표현 사전이 근거로 삼는 법령이 바뀌었는지 국가법령정보센터 Open API 로 확인한다.

사용:  backend/.venv/bin/python tools/check_legal_sources.py            # 점검만 (파일을 바꾸지 않는다)
       backend/.venv/bin/python tools/check_legal_sources.py --update   # 사람이 변경 내용을 검토한 뒤 기준(snapshot)을 현재 값으로 저장
       backend/.venv/bin/python tools/check_legal_sources.py --json     # 결과를 JSON 으로 (다른 도구·스케줄러용)
종료 코드: 0 변경 없음 · 1 변경 있음/기준 없음 · 2 조회 실패가 있음

판단 기준은 현행 법령의 법령일련번호·공포일자·시행일자·제개정구분이 저장된 snapshot 과 달라졌는지다. 법령 본문을 AI 가 읽고 규칙을 고치지 않는다 —
바뀐 법령이 나오면 '영향받는 규칙'(그 법을 근거로 적은 표현 사전 항목·규칙 파일)을 알려 줄 뿐, 수정과 검토는 사람이 한다.
OC 값은 국가법령정보센터 Open API 신청 시 받은 인증값이다(환경변수 LAW_OC, 없으면 공개 예시 값 'test' 로 시도하며 호출이 제한될 수 있다)."""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGAL = ROOT / "prompts" / "legal"
SOURCES = LEGAL / "sources.json"
API = "https://www.law.go.kr/DRF/lawSearch.do"
FIELDS = ("법령일련번호", "공포일자", "시행일자", "제개정구분명")


def norm(name: str) -> str:
    """법령명 비교용: 국가법령정보센터는 가운뎃점을 'ㆍ'(U+318D)로 쓰므로 '·' 와 같게 보고 공백을 없앤다."""
    return name.replace("ㆍ", "·").replace(" ", "")


def fetch_law(name: str, oc: str) -> dict:
    """현행 법령 한 건의 요약 {법령ID, 법령명한글, 법령일련번호, 공포일자, 시행일자, 제개정구분명}. 못 찾으면 LookupError."""
    url = API + "?" + urllib.parse.urlencode({"OC": oc, "target": "law", "type": "JSON", "query": name, "display": "20"})
    with urllib.request.urlopen(url, timeout=20) as r:
        data = json.load(r)
    laws = data.get("LawSearch", {}).get("law") or []
    laws = laws if isinstance(laws, list) else [laws]
    hit = [l for l in laws if norm(l.get("법령명한글", "")) == norm(name) and l.get("현행연혁코드", "현행") == "현행"]
    if not hit:
        raise LookupError(f"'{name}' 현행 법령을 찾지 못했습니다 (검색 결과 {len(laws)}건)")
    l = hit[0]
    return {"법령ID": l.get("법령ID"), "법령명한글": l["법령명한글"], **{k: l.get(k) for k in FIELDS}}


def affected(cite: str) -> dict:
    """그 법을 근거(law)로 적은 규칙: 표현 사전 항목 수와 규칙 파일 이름."""
    phrases = json.loads((LEGAL / "phrases.json").read_text(encoding="utf-8"))["entries"]
    n = sum(1 for e in phrases if norm(cite) in norm(e["law"]))
    files = []
    for p in sorted(LEGAL.rglob("*.md")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("law:") and norm(cite) in norm(line):
                files.append(str(p.relative_to(ROOT / "prompts").with_suffix("")))
    return {"phrases": n, "files": files}


def compare(old: dict | None, new: dict) -> str:
    """'기준 없음' | '변경 없음' | '변경됨'"""
    if not old:
        return "기준 없음"
    return "변경 없음" if all(old.get(k) == new.get(k) for k in ("법령ID", *FIELDS)) else "변경됨"


def run(oc: str, update: bool, fetch=fetch_law) -> list[dict]:
    data = json.loads(SOURCES.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = []
    for s in data["sources"]:
        r = {"name": s["name"], "affected": affected(s["cite"]), "old": s.get("snapshot")}
        try:
            r["new"] = fetch(s["name"], oc)
            r["status"] = compare(s.get("snapshot"), r["new"])
        except Exception as e:      # 네트워크·API 오류는 '변경 없음'으로 오인하지 않게 따로 표시한다
            r["status"], r["error"] = "조회 실패", str(e)
        if update and "new" in r and r["status"] in ("변경됨", "기준 없음"):
            if s.get("snapshot"):
                s.setdefault("history", []).append({"until": now, **s["snapshot"]})
            s["snapshot"] = {**r["new"], "recorded_at": now}
        results.append(r)
    if update:
        data["checked_at"] = now
        SOURCES.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return results


def render(results: list[dict]) -> str:
    lines = []
    for r in results:
        a = r["affected"]
        lines.append(f"[{r['status']}] {r['name']}")
        if r["status"] == "조회 실패":
            lines.append(f"    {r['error']}")
            continue
        n = r["new"]
        lines.append(f"    현행: 공포 {n['공포일자']} · 시행 {n['시행일자']} · {n['제개정구분명']} · 일련번호 {n['법령일련번호']}")
        if r["status"] == "변경됨":
            o = r["old"]
            lines.append(f"    이전: 공포 {o.get('공포일자')} · 시행 {o.get('시행일자')} · {o.get('제개정구분명')} · 일련번호 {o.get('법령일련번호')}")
        if r["status"] != "변경 없음":
            lines.append(f"    영향받는 규칙: 표현 사전 {a['phrases']}개, 규칙 파일 {', '.join(a['files']) or '없음'}")
    ch = sum(r["status"] == "변경됨" for r in results)
    nb = sum(r["status"] == "기준 없음" for r in results)
    er = sum(r["status"] == "조회 실패" for r in results)
    lines.append(f"\n합계 {len(results)}건: 변경됨 {ch} · 기준 없음 {nb} · 조회 실패 {er}")
    if ch or nb:
        lines.append("변경 내용을 확인해 규칙·표현 사전을 고친 뒤 --update 로 기준을 갱신하세요. (이 점검은 법률 자문이 아닙니다)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--update", action="store_true", help="변경 내용을 검토한 뒤 기준(snapshot)을 현재 값으로 저장")
    ap.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    a = ap.parse_args(argv)
    oc = os.getenv("LAW_OC", "test")
    if oc == "test":
        print("※ LAW_OC 가 없어 공개 예시 값('test')으로 조회합니다. 정식 인증값을 쓰려면 환경변수 LAW_OC 를 설정하세요.", file=sys.stderr)
    results = run(oc, a.update)
    print(json.dumps(results, ensure_ascii=False, indent=1) if a.json else render(results))
    if any(r["status"] == "조회 실패" for r in results):
        return 2
    return 1 if any(r["status"] in ("변경됨", "기준 없음") for r in results) and not a.update else 0


if __name__ == "__main__":
    sys.exit(main())
