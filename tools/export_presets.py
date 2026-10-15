"""시드(backend/app/seed.py)의 규격 데이터를 specs/presets.json 과 specs/sources.md 로 내보낸다 (Day 2 산출물).

DB 없이 seed.PRESETS 를 그대로 읽는다. 앱이 쓰는 값과 문서가 항상 같게 유지하기 위한 단방향 내보내기다.
실행: backend/.venv/bin/python tools/export_presets.py
"""
import json
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.seed import CHECKED, EXCLUDED_PRESETS, PRESETS  # noqa: E402


def preset_record(p: dict) -> dict:
    w, h = p["size"] if p["size"] else (None, None)
    return OrderedDict(
        platform=p["platform"], ad_type=p["ad_type"], placement=p["name"],
        content_kind=p["kind"], purpose="광고",
        spec_version=1, status=p["status"],
        production_default={"width": w, "height": h, "ratio": p["ratio"]},
        conditions=p["conditions"], text_limits=p["texts"], safe_area=p["safe"],
        required_assets=p.get("assets", []),
        sources=[{"url": u, "checked_at": CHECKED} for u in p["urls"]],
        note=p["note"],
    )


def main() -> None:
    records = [preset_record(p) for p in PRESETS]
    doc = OrderedDict(
        schema_version="1.0",
        generated_from="backend/app/seed.py",
        checked_at=CHECKED,
        rule="수치는 AI 추측 금지. 공식 문서에서 확인한 값만 '확인됨'. 미확인·충돌은 검수 통과 금지.",
        level_values=["강제", "권장", "미표기"],
        status_values=["확인됨", "충돌", "확인 필요"],
        excluded=[{"platform": a, "placement": b, "reason": "사용자 결정으로 지원 제외 (DB 에는 비활성으로 보존)"}
                  for a, b in EXCLUDED_PRESETS],
        presets=records,
    )
    out = ROOT / "specs" / "presets.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = ["# 규격 출처 목록 (Day 2 산출물)", "",
             f"`specs/presets.json` 과 같은 시드에서 생성. 확인일 {CHECKED}. 생성 스크립트: `tools/export_presets.py`", "",
             "| 매체 | 지면 | 상태 | 제작 기본값 | 출처 URL | 확인일 |", "|---|---|---|---|---|---|"]
    for r in records:
        pd_ = r["production_default"]
        size = f"{pd_['width']}×{pd_['height']}" if pd_["width"] else "—"
        urls = "<br>".join(s["url"] for s in r["sources"]) or "(없음)"
        checked = r["sources"][0]["checked_at"] if r["sources"] else "—"
        lines.append(f"| {r['platform']} | {r['placement']} | {r['status']} | {size} | {urls} | {checked} |")
    lines += ["", "## 조건별 확인 상태", "",
              "| 매체 | 지면 | 조건 | 값 | 구분 | 상태 | 비고 |", "|---|---|---|---|---|---|---|"]
    for r in records:
        for c in r["conditions"]:
            lines.append(f"| {r['platform']} | {r['placement']} | {c['name']} | {c['value']} | {c['level']} | {c['status']} | {c.get('note') or ''} |")
    lines += ["", "## 찾지 못해 '확인 필요'로 둔 값", ""]
    for r in records:
        if r["status"] == "확인 필요":
            lines.append(f"- {r['platform']} · {r['placement']}: {r['note']}")
        for c in r["conditions"]:
            if c["status"] in ("확인 필요", "충돌"):
                lines.append(f"- {r['platform']} · {r['placement']} · {c['name']}: [{c['status']}] {c.get('note') or ''}")
        if r["note"] and "확인 불가" in r["note"] and r["status"] != "확인 필요":
            lines.append(f"- {r['platform']} · {r['placement']}: {r['note']}")
    (ROOT / "specs" / "sources.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"presets: {len(records)}개 → {out}")


if __name__ == "__main__":
    main()
