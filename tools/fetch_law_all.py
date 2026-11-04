"""법령 한 건의 전 조문을 국가법령정보센터 조문 페이지에서 받아 raw/<id>.txt 로 저장하고 sources.json 의 규제 항목에 더한다 (3-B 데이터 확장).

실행 (저장소 루트에서):
    backend/.venv/bin/python tools/fetch_law_all.py brands/amorepacific/sources.json --law 화장품법 --seq 291337 --prefix law_cosmetics --industry 화장품
    backend/.venv/bin/python tools/fetch_law_all.py brands/amorepacific/sources.json --law "화장품법 시행규칙" --seq 285249 --prefix law_cosmetics_rule --industry 화장품
    … --dry-run   받기만 하고(네트워크) 파일·sources.json 은 바꾸지 않는다. 조문 수와 제목을 보여 준다
- 조 번호를 1부터 올리며 받고, 빈 응답(없는 조·삭제된 조)이 --stop 개 연속이면 끝. 조마다 '제N조의M'(joBrNo) 도 1~4 는 항상, 그 뒤는 있는 동안 확인
- id 는 <prefix>_<조>  /  <prefix>_<조>_<의M>  (기존 항목 law_cosmetics_2, law_cosmetics_rule_22 와 같은 규칙). 이미 있는 id 는 건드리지 않는다
- 제목은 본문 첫머리 '제N조(제목)' 에서 뽑는다. 본문이 너무 짧거나(40자 미만) '삭제' 뿐이면 건너뛴다
- 요청 사이에 --sleep 초(기본 0.7) 쉰다. 등록(임베딩)은 이 도구가 하지 않는다: tools/register_knowledge.py
- 업종(industry)은 검색·규제 투입의 필터다. 정의 조문처럼 업종과 무관한 조도 같은 법이면 같은 업종으로 표시한다(법 단위로 묶는다)
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from fetch_sources import UA, extract_law  # noqa: E402

TITLE = re.compile(r"^제(\d+)조(?:의(\d+))?\(([^)]*)\)")


def url_for(seq: int, jo: int, br: int) -> str:
    return f"https://www.law.go.kr/LSW/lsSideInfoP.do?lsiSeq={seq}&joNo={jo:04d}&joBrNo={br:02d}&docCls=jo&urlMode=lsScJoRltInfoR"


def fetch(c: httpx.Client, seq: int, jo: int, br: int, sleep: float) -> str:
    r = c.get(url_for(seq, jo, br))
    r.raise_for_status()
    time.sleep(sleep)
    t = extract_law(r.text)
    if len(t) < 40 or re.match(r"^제\d+조(?:의\d+)?\s*삭제", t) or re.match(r"^제\d+조(?:의\d+)?\([^)]*\)\s*삭제", t):
        return ""
    return t


def enumerate_law(c: httpx.Client, seq: int, stop: int, sleep: float, max_jo: int, log=print) -> list[tuple[int, int, str]]:
    """[(조, 의M(0이면 본조), 본문)]. 빈 조가 stop 개 연속이면 끝."""
    out, empty = [], 0
    for jo in range(1, max_jo + 1):
        t = fetch(c, seq, jo, 0, sleep)
        if not t:
            empty += 1
            if empty >= stop:
                break
            continue
        empty = 0
        out.append((jo, 0, t))
        log(f"  제{jo}조 {len(t)}자")
        br = 1
        while br <= 4 or (out and out[-1][0] == jo and out[-1][1] == br - 1):
            tb = fetch(c, seq, jo, br, sleep)
            if tb:
                out.append((jo, br, tb))
                log(f"  제{jo}조의{br} {len(tb)}자")
            br += 1
            if br > 20:
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources")
    ap.add_argument("--law", required=True, help="법령 이름 (chunk_law 가 조각 제목에 쓴다)")
    ap.add_argument("--seq", type=int, required=True, help="국가법령정보센터 lsiSeq")
    ap.add_argument("--prefix", required=True, help="id 접두 (예: law_cosmetics)")
    ap.add_argument("--industry", required=True)
    ap.add_argument("--stop", type=int, default=12, help="빈 조가 이만큼 연속이면 끝")
    ap.add_argument("--max", type=int, default=150)
    ap.add_argument("--sleep", type=float, default=0.7)
    ap.add_argument("--force", action="store_true", help="raw 가 있어도 다시 받는다")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    src = Path(a.sources)
    doc = json.loads(src.read_text(encoding="utf-8"))
    have = {s["id"]: s for s in doc["sources"]}
    raw = src.parent / "raw"
    raw.mkdir(exist_ok=True)
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": UA}) as c:
        found = enumerate_law(c, a.seq, a.stop, a.sleep, a.max)
    new, kept, written = [], 0, 0
    for jo, br, text in found:
        sid = f"{a.prefix}_{jo}" + (f"_{br}" if br else "")
        m = TITLE.match(text)
        title = f"{a.law} 제{jo}조" + (f"의{br}" if br else "") + (f"({m.group(3)})" if m else "")
        if sid in have:
            kept += 1
        else:
            new.append({"id": sid, "layer": "규제", "title": title, "kind": "law", "law": a.law, "url": url_for(a.seq, jo, br),
                        "industry": a.industry})
        out = raw / f"{sid}.txt"
        if not a.dry_run and (a.force or not out.exists()):
            out.write_text(text + "\n", encoding="utf-8")
            written += 1
    print(f"\n{a.law}: 조문 {len(found)}개 (본조 {sum(1 for _, b, _ in found if b == 0)} · 조의N {sum(1 for _, b, _ in found if b)}). "
          f"기존 항목 {kept}, 새 항목 {len(new)}, raw 저장 {written}" + (" (dry-run: 저장 안 함)" if a.dry_run else ""))
    if a.dry_run or not new:
        return 0
    # 규제 블록 끝(마지막 규제 항목 뒤)에 끼워 넣는다
    idx = max((i for i, s in enumerate(doc["sources"]) if s["layer"] == "규제"), default=-1) + 1
    doc["sources"][idx:idx] = new
    src.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{src}: 규제 항목 {len(new)}개 추가 → 등록: backend/.venv/bin/python tools/register_knowledge.py {src} --only {','.join(s['id'] for s in new[:3])},…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
