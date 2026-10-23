"""평가 보고서 생성 (워크북 Day 5 "첫 평가 보고서", Day 10 "결과 세트 전체 검수", Day 11 "모델 비교"의 뼈대).

로컬 DB 의 시안을 읽어 검수 결과·호출 기록(시간·비용)·저장된 채점을 한 장의 report.html 로 정리한다.
앱 코드는 바꾸지 않고 app.routers_review(검수)·app.calllog(비용)·app.analysis(채점)를 가져다 쓴다.

실행 (저장소 루트에서):
    backend/.venv/bin/python eval/run_eval.py                      # 합성본이 있는 광고 시안 전부, 저장된 검수 결과 사용
    backend/.venv/bin/python eval/run_eval.py --designs 8 15       # 특정 시안만
    backend/.venv/bin/python eval/run_eval.py --brand 쿠챠          # 브랜드 이름으로 고르기
    backend/.venv/bin/python eval/run_eval.py --rerun              # 시안마다 검수를 다시 실행 (사람 '확인함' 표시는 초기화됨)
    backend/.venv/bin/python eval/run_eval.py --db backend/data/app.db --out eval/reports/직접지정

출력: eval/reports/<YYYYMMDD_HHMM>/report.html · results.json · summary.csv
외부 API 는 호출하지 않는다 (채점은 저장된 것만 싣는다).
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import db as db_module  # noqa: E402
from app import models  # noqa: E402,F401  (테이블 등록)
from app.models import Brand, BrandVersion, Design, GeneratedImage, PresetVersion  # noqa: E402

STATUSES = ("통과", "경고", "수정 필요", "확인 필요")
THUMB_PX = 160          # 썸네일 긴 변 (작게 — 보고서 파일 크기를 위해)
KIND_LABEL = {"background": "배경", "variant": "지면 이미지", "product": "제품", "icon": "아이콘", "copy": "카피",
              "copy_fit": "카피 맞춤", "icon_names": "기능명", "icon_redraw": "아이콘 재생성", "judge": "채점"}
IMAGE_KINDS = ("background", "variant", "product", "icon", "icon_redraw")
COPY_KINDS = ("copy", "copy_fit", "icon_names")


# ---------------------------------------------------------------- 수집
def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _settings() -> dict:
    """보고서에 적을 기준선. 모듈 상수는 .env 를 읽은 뒤의 값이다."""
    from app import copywriter, imagegen, llm
    return {"CHAT_MODEL": copywriter.CHAT_MODEL, "CLAUDE_MODEL": llm.CLAUDE_MODEL,
            "IMAGE_MODEL": imagegen.OPENAI_MODEL, "IMAGE_QUALITY": imagegen.OPENAI_QUALITY,
            "IMAGE_PROVIDER": getattr(imagegen, "PROVIDER", "openai")}


def _thumb(path: str | None) -> str | None:
    """합성본을 작은 JPEG(base64 data URI)로. 파일이 없으면 None. 경로는 storage(로컬 폴더 또는 Supabase 캐시) 기준."""
    from app import storage
    if not path or not storage.exists(path):
        return None
    try:
        from PIL import Image
        im = Image.open(storage.local(path)).convert("RGB")
        im.thumbnail((THUMB_PX, THUMB_PX))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=70, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _latest_composite(s, design_id: int, preset_id: int) -> GeneratedImage | None:
    return s.scalar(select(GeneratedImage).where(
        GeneratedImage.design_id == design_id, GeneratedImage.preset_version_id == preset_id,
        GeneratedImage.kind == "composite").order_by(GeneratedImage.id.desc()).limit(1))


def _models_used(calls: list[dict]) -> dict:
    """호출 기록에서 시안이 쓴 모델: {'copy': {...}, 'image': {...}, 'judge': {...}}"""
    out: dict[str, set] = {"copy": set(), "image": set(), "judge": set()}
    for r in calls:
        if not r.get("ok"):
            continue
        k, m = r.get("kind"), r.get("model")
        if not m:
            continue
        if k in COPY_KINDS:
            out["copy"].add(m)
        elif k in IMAGE_KINDS:
            out["image"].add(m)
        elif k == "judge":
            out["judge"].add(m)
    return {k: sorted(v) for k, v in out.items()}


def select_designs(s, design_ids: list[int] | None, brand: str | None) -> list[Design]:
    q = select(Design).where(Design.kind == "광고 이미지").order_by(Design.id)
    if design_ids:
        q = q.where(Design.id.in_(design_ids))
    if brand:
        q = q.join(Brand, Brand.id == Design.brand_id).where(Brand.name == brand)
    designs = list(s.scalars(q).all())
    if design_ids:
        return designs
    # 지정하지 않았으면 합성본이 하나라도 있는 시안만 (아무것도 안 만든 캠페인은 평가 대상이 아니다)
    return [d for d in designs if any(_latest_composite(s, d.id, pid) for pid in d.preset_version_ids)]


def collect_design(s, d: Design, rerun: bool) -> dict:
    from app import analysis, calllog, storage
    from app.routers_review import _collect, run_review
    brand = s.get(Brand, d.brand_id)
    bv = s.get(BrandVersion, d.brand_version_id)
    cur_no = None
    if brand and brand.current_version_id:
        cur = s.get(BrandVersion, brand.current_version_id)
        cur_no = cur.version_no if cur else None
    review = run_review(d.id, db=s) if rerun else _collect(s, d)
    groups = []
    for g in review["groups"]:
        pid = g["preset_id"]
        pv = s.get(PresetVersion, pid) if pid is not None else None
        comp = _latest_composite(s, d.id, pid) if pid is not None else None
        problems = [{"category": i["category"], "item": i["item"], "status": i["status"], "message": i["message"],
                     "blocking": i["blocking"], "needs_human": i["needs_human"], "confirmed": i["confirmed"]}
                    for i in g["items"] if i["status"] != "통과"]
        groups.append({
            "preset_id": pid, "name": g["name"],
            "platform": pv.placement.platform.name if pv else None,
            "spec_status": pv.status if pv else None, "spec_version_no": pv.version_no if pv else None,
            "size": [pv.production_width, pv.production_height] if pv else None,
            "has_image": bool(comp and storage.exists(comp.file_path)),
            "summary": g["summary"], "export_ready": g["export_ready"], "reviewed": bool(g["items"]),
            "problems": problems, "thumb": _thumb(comp.file_path) if comp else None,
        })
    calls = calllog.calls(d.id)
    cost = calllog.summary(design_id=d.id)
    judged = analysis.judgments(d.id)
    latest_judge = judged[0] if judged else None
    return {
        "id": d.id, "status": d.status, "created_at": d.created_at.isoformat(timespec="minutes") if d.created_at else None,
        "brand": {"id": d.brand_id, "name": brand.name if brand else None,
                  "version_no": bv.version_no if bv else None, "current_version_no": cur_no,
                  "outdated": bool(bv and cur_no and cur_no != bv.version_no)},
        "brief": {"product_name": (d.brief or {}).get("product_name"), "key_message": (d.brief or {}).get("key_message")},
        "reviewed": review["reviewed"], "export_ready": review["export_ready"],
        "groups": groups, "blockers": review["blockers"],
        "cost": {"calls": cost["ok"], "failed": cost["failed"], "ms_total": cost["ms_total"],
                 "cost_usd": cost["cost_usd"], "cost_krw": cost["cost_krw"], "estimated": cost["estimated"],
                 "by_kind": {k: {"count": v["count"], "ms_total": v["ms_total"], "cost_krw": v["cost_krw"]}
                             for k, v in cost["by_kind"].items()}},
        "models": _models_used(calls),
        "judge": ({"overall": latest_judge.get("overall"), "scores": latest_judge.get("scores"),
                   "model": latest_judge.get("model"), "count": len(judged)} if latest_judge else None),
    }


# ---------------------------------------------------------------- 집계
def aggregate(designs: list[dict]) -> dict:
    preset_groups = [g for d in designs for g in d["groups"] if g["preset_id"] is not None]
    icon_groups = [g for d in designs for g in d["groups"] if g["preset_id"] is None]
    reviewed = [g for g in preset_groups if g["reviewed"]]
    ready = [g for g in reviewed if g["export_ready"]]
    status_counts = Counter()
    for g in preset_groups + icon_groups:
        for k, v in g["summary"].items():
            status_counts[k] += v
    violations = Counter()
    blocking_items = Counter()
    for g in preset_groups + icon_groups:
        for p in g["problems"]:
            violations[(p["category"], p["item"], p["status"])] += 1
            if p["blocking"]:
                blocking_items[(p["category"], p["item"])] += 1
    by_platform: dict[str, dict] = defaultdict(lambda: {"presets": 0, "reviewed": 0, "ready": 0})
    for g in preset_groups:
        p = by_platform[g["platform"] or "?"]
        p["presets"] += 1
        p["reviewed"] += int(g["reviewed"])
        p["ready"] += int(g["export_ready"])
    cost_total_krw = sum(d["cost"]["cost_krw"] or 0 for d in designs)
    cost_total_usd = round(sum(d["cost"]["cost_usd"] or 0 for d in designs), 4)
    calls_total = sum(d["cost"]["calls"] for d in designs)
    ms_total = sum(d["cost"]["ms_total"] for d in designs)
    by_kind: dict[str, dict] = defaultdict(lambda: {"count": 0, "ms_total": 0, "cost_krw": 0})
    for d in designs:
        for k, v in d["cost"]["by_kind"].items():
            by_kind[k]["count"] += v["count"]
            by_kind[k]["ms_total"] += v["ms_total"]
            by_kind[k]["cost_krw"] += v["cost_krw"] or 0

    def model_table(slot: str) -> list[dict]:
        rows: dict[str, dict] = defaultdict(lambda: {"designs": 0, "presets": 0, "ready": 0, "cost_krw": 0, "ms": 0})
        for d in designs:
            names = d["models"].get(slot) or ["(기록 없음)"]
            for m in names:
                r = rows[m]
                r["designs"] += 1
                ps = [g for g in d["groups"] if g["preset_id"] is not None and g["reviewed"]]
                r["presets"] += len(ps)
                r["ready"] += sum(1 for g in ps if g["export_ready"])
                r["cost_krw"] += d["cost"]["cost_krw"] or 0
                r["ms"] += d["cost"]["ms_total"]
        out = []
        for m, r in sorted(rows.items()):
            out.append({"model": m, **r,
                        "pass_rate": round(r["ready"] / r["presets"], 3) if r["presets"] else None,
                        "cost_per_design_krw": round(r["cost_krw"] / r["designs"]) if r["designs"] else None})
        return out

    return {
        "designs": len(designs), "presets": len(preset_groups), "presets_reviewed": len(reviewed),
        "presets_ready": len(ready),
        "pass_rate": round(len(ready) / len(reviewed), 3) if reviewed else None,
        "designs_ready": sum(1 for d in designs if d["export_ready"]),
        "designs_unreviewed": sum(1 for d in designs if not d["reviewed"]),
        "icon_sets": len(icon_groups), "icon_sets_ready": sum(1 for g in icon_groups if g["export_ready"]),
        "status_counts": {k: status_counts.get(k, 0) for k in STATUSES},
        "violations": [{"category": c, "item": i, "status": st, "count": n}
                       for (c, i, st), n in violations.most_common()],
        "blocking_items": [{"category": c, "item": i, "count": n} for (c, i), n in blocking_items.most_common()],
        "by_platform": dict(by_platform),
        "cost": {"calls": calls_total, "ms_total": ms_total, "cost_usd": cost_total_usd, "cost_krw": cost_total_krw,
                 "per_design_krw": round(cost_total_krw / len(designs)) if designs else None,
                 "per_ready_preset_krw": round(cost_total_krw / len(ready)) if ready else None,
                 "by_kind": dict(by_kind)},
        "models": {"copy": model_table("copy"), "image": model_table("image")},
        "judged": sum(1 for d in designs if d["judge"]),
    }


def build_report(session_factory, design_ids: list[int] | None = None, brand: str | None = None,
                 rerun: bool = False) -> dict:
    with session_factory() as s:
        designs = [collect_design(s, d, rerun) for d in select_designs(s, design_ids, brand)]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"), "git_commit": _git_commit(),
        "settings": _settings(), "rerun": rerun, "filter": {"design_ids": design_ids, "brand": brand},
        "summary": aggregate(designs), "designs": designs,
    }


# ---------------------------------------------------------------- 출력
def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _sec(ms: int) -> str:
    return f"{ms / 1000:.0f}s"


def _krw(v) -> str:
    return "—" if v is None else f"{int(v):,}원"


def _badge(status: str) -> str:
    cls = {"통과": "ok", "경고": "warn", "수정 필요": "fail", "확인 필요": "check"}.get(status, "")
    return f'<span class="badge {cls}">{html.escape(status)}</span>'


def render_html(data: dict) -> str:
    S = data["summary"]
    e = html.escape
    parts = [f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>평가 보고서 {e(data['generated_at'][:16])}</title>
<style>
body{{font-family:-apple-system,"Apple SD Gothic Neo","Pretendard","Noto Sans KR",sans-serif;margin:24px;color:#1b1f2a;font-size:13px;line-height:1.45}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:28px 0 8px;border-bottom:2px solid #e5e7eb;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;margin:6px 0 12px}} th,td{{border:1px solid #e5e7eb;padding:4px 7px;text-align:left;vertical-align:top}}
th{{background:#f6f7fa;font-weight:600}} td.num,th.num{{text-align:right}}
.kpi{{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}} .kpi div{{background:#f6f7fa;border-radius:8px;padding:8px 14px;min-width:120px}}
.kpi b{{display:block;font-size:20px}} .muted{{color:#6b7280}}
.badge{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:600}}
.badge.ok{{background:#dcfce7;color:#166534}} .badge.warn{{background:#fef9c3;color:#854d0e}}
.badge.fail{{background:#fee2e2;color:#991b1b}} .badge.check{{background:#e0e7ff;color:#3730a3}}
img.thumb{{max-width:{THUMB_PX}px;max-height:{THUMB_PX}px;border:1px solid #e5e7eb;border-radius:4px;display:block}}
ul.problems{{margin:0;padding-left:16px}} ul.problems li{{margin:1px 0}}
.design{{border:1px solid #e5e7eb;border-radius:8px;padding:10px 14px;margin:10px 0}}
.design h3{{margin:0 0 6px;font-size:14px}}
</style></head><body>
<h1>평가 보고서 — 검수 결과 · 위반 유형 · 비용</h1>
<div class="muted">생성 {e(data['generated_at'])} · 커밋 {e(data['git_commit'])} · {"검수 재실행" if data['rerun'] else "저장된 검수 결과 사용"}
 · 카피 {e(data['settings']['CHAT_MODEL'])} / {e(data['settings']['CLAUDE_MODEL'])} · 이미지 {e(data['settings']['IMAGE_MODEL'])} ({e(data['settings']['IMAGE_QUALITY'])})</div>

<h2>1. 요약</h2>
<div class="kpi">
<div><span class="muted">시안</span><b>{S['designs']}</b>검수 완료 가능 {S['designs_ready']} · 미검수 {S['designs_unreviewed']}</div>
<div><span class="muted">지면</span><b>{S['presets']}</b>검수됨 {S['presets_reviewed']} · 통과 {S['presets_ready']}</div>
<div><span class="muted">지면 통과율</span><b>{_pct(S['pass_rate'])}</b>검수된 지면 중 차단 없음</div>
<div><span class="muted">아이콘 세트</span><b>{S['icon_sets']}</b>통과 {S['icon_sets_ready']}</div>
<div><span class="muted">총 비용</span><b>{_krw(S['cost']['cost_krw'])}</b>시안당 {_krw(S['cost']['per_design_krw'])} · 통과 지면당 {_krw(S['cost']['per_ready_preset_krw'])}</div>
<div><span class="muted">호출</span><b>{S['cost']['calls']}</b>소요 {_sec(S['cost']['ms_total'])}</div>
</div>
<table><tr>{''.join(f'<th>{e(k)}</th>' for k in STATUSES)}</tr><tr>{''.join(f'<td class="num">{S["status_counts"][k]}</td>' for k in STATUSES)}</tr></table>
<p class="muted">통과율 = 검수된 지면 중 '수정 필요·확인 필요(차단)'가 하나도 없는 지면의 비율. 미검수 시안은 분모에서 뺀다.</p>
"""]
    # 매체별
    parts.append("<h2>2. 매체별</h2><table><tr><th>매체</th><th class='num'>지면</th><th class='num'>검수됨</th><th class='num'>통과</th><th class='num'>통과율</th></tr>")
    for plat, p in sorted(S["by_platform"].items()):
        rate = _pct(p["ready"] / p["reviewed"]) if p["reviewed"] else "—"
        parts.append(f"<tr><td>{e(plat)}</td><td class='num'>{p['presets']}</td><td class='num'>{p['reviewed']}</td><td class='num'>{p['ready']}</td><td class='num'>{rate}</td></tr>")
    parts.append("</table>")
    # 위반 유형
    parts.append("<h2>3. 위반·확인 유형 (많은 순)</h2>")
    if S["violations"]:
        parts.append("<table><tr><th>분류</th><th>항목</th><th>상태</th><th class='num'>건수</th></tr>")
        for v in S["violations"]:
            parts.append(f"<tr><td>{e(v['category'])}</td><td>{e(v['item'])}</td><td>{_badge(v['status'])}</td><td class='num'>{v['count']}</td></tr>")
        parts.append("</table>")
        if S["blocking_items"]:
            parts.append("<p><b>검수 완료를 막는 항목</b>: " + " · ".join(f"{e(b['item'])} {b['count']}건" for b in S["blocking_items"]) + "</p>")
    else:
        parts.append("<p class='muted'>통과 외 항목이 없습니다.</p>")
    # 비용
    parts.append("<h2>4. 시간·비용 (종류별)</h2><table><tr><th>종류</th><th class='num'>호출</th><th class='num'>소요</th><th class='num'>비용</th></tr>")
    for k, v in sorted(S["cost"]["by_kind"].items(), key=lambda x: -(x[1]["cost_krw"] or 0)):
        parts.append(f"<tr><td>{e(KIND_LABEL.get(k, k))}</td><td class='num'>{v['count']}</td><td class='num'>{_sec(v['ms_total'])}</td><td class='num'>{_krw(v['cost_krw'])}</td></tr>")
    parts.append(f"<tr><th>합계</th><th class='num'>{S['cost']['calls']}</th><th class='num'>{_sec(S['cost']['ms_total'])}</th><th class='num'>{_krw(S['cost']['cost_krw'])} (${S['cost']['cost_usd']})</th></tr></table>")
    # 모델별
    parts.append("<h2>5. 모델별 (호출 기록 기준)</h2>")
    for slot, label in (("image", "이미지 모델"), ("copy", "카피 모델")):
        parts.append(f"<table><tr><th>{label}</th><th class='num'>시안</th><th class='num'>검수 지면</th><th class='num'>통과</th><th class='num'>통과율</th><th class='num'>시안당 비용</th><th class='num'>소요</th></tr>")
        for r in S["models"][slot]:
            parts.append(f"<tr><td>{e(r['model'])}</td><td class='num'>{r['designs']}</td><td class='num'>{r['presets']}</td><td class='num'>{r['ready']}</td><td class='num'>{_pct(r['pass_rate'])}</td><td class='num'>{_krw(r['cost_per_design_krw'])}</td><td class='num'>{_sec(r['ms'])}</td></tr>")
        parts.append("</table>")
    parts.append("<p class='muted'>한 시안이 여러 모델을 썼으면 각 모델 행에 모두 센다. 모델만 바꾼 비교(Day 11)는 브리프·브랜드·규격을 고정한 시안 묶음으로 다시 돌린다.</p>")
    # 시안별
    parts.append("<h2>6. 시안별 결과</h2>")
    for d in data["designs"]:
        b = d["brand"]
        ver = f"v{b['version_no']}" + (f" <span class='badge warn'>브랜드가 v{b['current_version_no']}로 변경됨</span>" if b["outdated"] else "")
        state = "<span class='badge ok'>검수 완료 가능</span>" if d["export_ready"] else ("<span class='badge check'>미검수</span>" if not d["reviewed"] else "<span class='badge fail'>차단 항목 있음</span>")
        judge = f" · 채점 {d['judge']['overall']}/5 ({e(str(d['judge']['model']))})" if d["judge"] else ""
        parts.append(f"<div class='design'><h3>#{d['id']} {e(b['name'] or '')} {ver} · {e(d['brief']['product_name'] or '')} {state}</h3>"
                     f"<div class='muted'>호출 {d['cost']['calls']} · {_sec(d['cost']['ms_total'])} · {_krw(d['cost']['cost_krw'])} · 이미지 {e(', '.join(d['models']['image']) or '—')} · 카피 {e(', '.join(d['models']['copy']) or '—')}{judge}</div>")
        parts.append("<table><tr><th>지면</th><th>미리보기</th><th>통과/경고/수정/확인</th><th>통과 외 항목</th></tr>")
        for g in d["groups"]:
            sm = g["summary"]
            img = f"<img class='thumb' src='{g['thumb']}'>" if g["thumb"] else ("<span class='muted'>이미지 없음</span>" if g["preset_id"] is not None else "")
            ready = "<span class='badge ok'>통과</span>" if g["export_ready"] else ("<span class='badge check'>미검수</span>" if not g["reviewed"] else "<span class='badge fail'>차단</span>")
            probs = "".join(f"<li>{_badge(p['status'])} {e(p['item'])}{' <b>[차단]</b>' if p['blocking'] else ''}{' (확인함)' if p['confirmed'] else ''} <span class='muted'>{e(p['message'])}</span></li>" for p in g["problems"])
            size = f" <span class='muted'>{g['size'][0]}×{g['size'][1]}</span>" if g["size"] and g["size"][0] else ""
            parts.append(f"<tr><td>{e(g['name'])}{size}<br>{ready}</td><td>{img}</td>"
                         f"<td class='num'>{sm.get('통과', 0)} / {sm.get('경고', 0)} / {sm.get('수정 필요', 0)} / {sm.get('확인 필요', 0)}</td>"
                         f"<td><ul class='problems'>{probs or '<li class=muted>없음</li>'}</ul></td></tr>")
        parts.append("</table></div>")
    parts.append("<p class='muted'>eval/run_eval.py · 외부 API 호출 없음 · 비용은 app/pricing.py 요율 기준 추정</p></body></html>")
    return "".join(parts)


def write_outputs(data: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.html").write_text(render_html(data), encoding="utf-8")
    slim = json.loads(json.dumps(data, ensure_ascii=False))
    for d in slim["designs"]:
        for g in d["groups"]:
            g.pop("thumb", None)          # JSON 에는 썸네일을 넣지 않는다
    (out_dir / "results.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["시안", "브랜드", "규칙 버전", "지면", "검수됨", "통과", "통과 외 항목 수", "차단 항목", "호출", "소요(초)", "비용(원)"])
        for d in data["designs"]:
            for g in d["groups"]:
                w.writerow([d["id"], d["brand"]["name"], d["brand"]["version_no"], g["name"],
                            "예" if g["reviewed"] else "아니오", "예" if g["export_ready"] else "아니오",
                            len(g["problems"]), "; ".join(p["item"] for p in g["problems"] if p["blocking"]),
                            d["cost"]["calls"], round(d["cost"]["ms_total"] / 1000), d["cost"]["cost_krw"]])
    return {"report": out_dir / "report.html", "json": out_dir / "results.json", "csv": out_dir / "summary.csv"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="검수 결과·비용 평가 보고서(report.html) 생성")
    ap.add_argument("--designs", type=int, nargs="*", help="시안 id (생략하면 합성본이 있는 광고 시안 전부)")
    ap.add_argument("--brand", help="브랜드 이름으로 고르기")
    ap.add_argument("--rerun", action="store_true", help="시안마다 검수를 다시 실행 (사람 '확인함' 표시 초기화)")
    ap.add_argument("--db", default=str(ROOT / "backend" / "data" / "app.db"), help="SQLite 파일 (기본 backend/data/app.db)")
    ap.add_argument("--out", help="출력 폴더 (기본 eval/reports/<날짜_시각>)")
    a = ap.parse_args(argv)

    db_path = Path(a.db)
    if not db_path.exists():
        print(f"DB 가 없습니다: {db_path}", file=sys.stderr)
        return 2
    engine = db_module.make_engine(f"sqlite:///{db_path}")
    factory = sessionmaker(bind=engine, autoflush=False)
    db_module.SessionLocal = factory          # calllog·analysis 가 같은 DB 를 읽게 한다
    data = build_report(factory, a.designs or None, a.brand, a.rerun)
    out_dir = Path(a.out) if a.out else ROOT / "eval" / "reports" / datetime.now().strftime("%Y%m%d_%H%M")
    paths = write_outputs(data, out_dir)
    S = data["summary"]
    print(f"시안 {S['designs']}개 · 지면 {S['presets']}개 (검수됨 {S['presets_reviewed']}, 통과 {S['presets_ready']}, "
          f"통과율 {_pct(S['pass_rate'])}) · 비용 {_krw(S['cost']['cost_krw'])}")
    for k, p in paths.items():
        print(f"  {k}: {p.relative_to(ROOT) if p.is_relative_to(ROOT) else p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
