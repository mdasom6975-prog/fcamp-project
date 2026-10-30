"""brands/<그룹>/sources.json 의 URL 을 받아 본문 텍스트를 raw/<id>.txt 로 저장한다 (RAG 1-a 수집, 과금 없음).

실행: backend/.venv/bin/python tools/fetch_sources.py brands/amorepacific/sources.json [--only id1,id2] [--force]
- kind=law: 국가법령정보센터 조문 페이지(lsSideInfoP)에서 '제N조(…)' 부터 조문 끝까지
- kind=html: <main> 안의 글(스크립트·메뉴 제외)
- kind=manual: 받지 않는다(스크립트로 그려지는 페이지). 사람이 raw/<id>.txt 를 만든다 — 없으면 목록만 알려 준다
- 원문 HTML(raw/<id>.html)과 텍스트(raw/<id>.txt)는 .gitignore 대상. 이미 있으면 건너뛴다(--force 로 다시 받기)
- 요청 사이에 1초 쉰다
"""
import html as htmllib
import json
import re
import sys
import time
from pathlib import Path

import httpx

UA = "Mozilla/5.0 (brand-studio RAG study; contact: local)"


def _text(fragment: str) -> str:
    fragment = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    fragment = re.sub(r"<(script|style|nav|header|footer|noscript)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<br\s*/?>|</(p|div|li|h\d|tr|section)>", "\n", fragment, flags=re.I)
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in t.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract_html(page: str) -> str:
    m = re.search(r"<main\b.*?</main>", page, flags=re.S | re.I)
    text = _text(m.group(0) if m else page)
    # 브랜드 페이지 끝의 외부 링크 안내(사이트 방문·SNS·매거진)는 지식이 아니다
    return re.split(r"\n\s*Visit and Follow Us", text)[0].strip()


def extract_law(page: str) -> str:
    """조문 페이지 → '제N조(제목) 본문'. 페이지 아래의 저장·검색 메뉴('위로 아래로' 이후)는 버린다."""
    t = re.sub(r"\s+", " ", _text(page))
    m = re.search(r"제\d+조(?:의\d+)?\([^)]*\)", t)
    if not m:
        return ""
    body = t[m.start():]
    return body.split(" 위로 아래로")[0].strip()


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    src = Path(args[0])
    only = set(args[args.index("--only") + 1].split(",")) if "--only" in args else None
    force = "--force" in args
    raw = src.parent / "raw"
    raw.mkdir(exist_ok=True)
    doc = json.loads(src.read_text(encoding="utf-8"))
    missing_manual = []
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": UA}) as c:
        for s in doc["sources"]:
            if only and s["id"] not in only:
                continue
            out = raw / f"{s['id']}.txt"
            if s["kind"] == "manual":
                if not out.exists():
                    missing_manual.append(s)
                continue
            if out.exists() and not force:
                print(f"[건너뜀] {s['id']} (이미 있음)")
                continue
            r = c.get(s["url"])
            r.raise_for_status()
            (raw / f"{s['id']}.html").write_text(r.text, encoding="utf-8")
            text = extract_law(r.text) if s["kind"] == "law" else extract_html(r.text)
            if len(text) < 30:
                print(f"[실패] {s['id']}: 본문을 찾지 못함 ({len(text)}자)")
                continue
            out.write_text(text + "\n", encoding="utf-8")
            print(f"[받음] {s['id']}: {len(text)}자")
            time.sleep(1)
    for s in missing_manual:
        print(f"[직접 준비] {s['id']}: {s.get('how', '')} → {raw / (s['id'] + '.txt')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
