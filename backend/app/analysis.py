"""분석 화면(학습·개발용) 계산. 검수와 무관하게, 한 캠페인에 대해 세 가지를 한 페이지에 모은다.

1. 입력 ↔ 결과: 브랜드 규칙·캠페인 입력이 어느 단계(카피·배경·아이콘 프롬프트 / 합성 코드 / 검수)에서 반영되는지 표로,
   생성 배경·아이콘의 주요 색을 뽑아 브랜드 색과의 거리(ΔE, CIE76)를 수치로. 배경 프롬프트에는 색이 들어가지 않으므로
   배경의 색 일치는 '우연한 일치'의 정도이고, 아이콘(색 지시 포함)에서만 의도한 일치를 본다.
   버튼을 누르면 Claude 가 배경 1장을 보고 톤·내용·표현 방식·금지 요소·글자 항목을 1~5점으로 채점한다(채팅 1회 과금,
   kind=judge, prompts/analysis/judge_*.md). 사람 평가를 대신하지 않는 참고값이다.
2. 보낸 프롬프트: 호출 기록에 전문이 저장된 건(recorded) → DB 에 남은 배경·아이콘 프롬프트(stored) →
   둘 다 없는 채팅 호출은 현재 규칙·입력으로 다시 렌더(reconstructed) 순으로 보여 준다. 재구성은 보낸 당시와 다를 수 있다.
3. 비용: calllog 집계를 카피/배경/아이콘/채점 단계로 묶고 호출 내역 전체를 붙인다.
"""
import io
import math
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import calllog, copyfields, copywriter, icons, imagegen, llm, phrases, prompts, storage
from . import db as db_module
from .models import Brand, BrandVersion, CopyVariant, Design, GeneratedImage, IconAsset, Judgment, PresetVersion

STAGES = {"copy": ("copy", "copy_fit"), "background": ("background", "variant", "product"),
          "icon": ("icon", "icon_names", "icon_redraw"), "judge": ("judge",)}
STAGE_OF = {k: st for st, ks in STAGES.items() for k in ks}
STAGE_LABEL = {"copy": "시안 생성(카피)", "background": "배경 이미지 생성", "icon": "아이콘 생성", "judge": "비전 채점"}
NEAR_DE = 20.0            # 이 ΔE 이하면 '브랜드 색에 가깝다'고 본다 (CIE76: 10 이하 거의 같은 색, 20 이하 같은 계열)
JUDGE_ITEMS = ("톤 일치", "내용 일치", "표현 방식 일치", "금지 요소 없음", "글자·로고 없음")


# ---------------- 색 ----------------
def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = (h or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """sRGB(0~255) → CIE L*a*b* (D65)."""
    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / 1.0
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883
    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: str, b: str) -> float:
    """두 hex 색의 CIE76 거리."""
    la, lb = rgb_to_lab(hex_to_rgb(a)), rgb_to_lab(hex_to_rgb(b))
    return round(math.sqrt(sum((p - q) ** 2 for p, q in zip(la, lb))), 1)


def palette(im: Image.Image, n: int = 6) -> list[dict]:
    """이미지의 주요 색 n개 [{hex, share}] (share 는 불투명 픽셀 중 비율, 합 1). 투명 픽셀(알파 128 미만)은 뺀다."""
    im = im.convert("RGBA")
    im.thumbnail((160, 160))
    px = [p[:3] for p in im.getdata() if p[3] >= 128]
    if not px:
        return []
    flat = Image.new("RGB", (len(px), 1))
    flat.putdata(px)
    q = flat.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    counts = sorted(q.getcolors() or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    return [{"hex": "#%02X%02X%02X" % tuple(pal[i * 3:i * 3 + 3]), "share": round(c / total, 3)} for c, i in counts]


def color_match(pal: list[dict], brand_colors: list[dict]) -> dict:
    """팔레트와 브랜드 색의 대응. per_color: 브랜드 색마다 가장 가까운 팔레트 색과 ΔE·그 색의 비율(모든 역할).
    coverage: 정체성 색(주조·보조·강조색, 아이콘 프롬프트에 들어가는 색) 중 하나와 ΔE ≤ NEAR_DE 인 팔레트 색들의 비율 합(0~1).
    배경색·텍스트색(흰색·검정이 보통)은 어느 이미지에나 있어 넣으면 수치가 부풀므로 뺀다. 정체성 색이 없으면 모든 색으로 센다.
    브랜드 색이 없으면 None."""
    cols = [c for c in brand_colors or [] if c.get("hex")]
    if not cols or not pal:
        return {"per_color": [], "coverage": None, "coverage_roles": [], "near_de": NEAR_DE}
    per = []
    for c in cols:
        best = min(pal, key=lambda p: delta_e(c["hex"], p["hex"]))
        per.append({"role": c.get("role") or "", "hex": c["hex"].upper(), "nearest": best["hex"],
                    "delta_e": delta_e(c["hex"], best["hex"]), "share": best["share"]})
    ident = [c for c in cols if c.get("role") in icons.PALETTE_ROLES] or cols
    covered = sum(p["share"] for p in pal if min(delta_e(p["hex"], c["hex"]) for c in ident) <= NEAR_DE)
    return {"per_color": per, "coverage": round(covered, 3), "coverage_roles": [c.get("role") or "" for c in ident], "near_de": NEAR_DE}


def _open(path: str | None) -> Image.Image | None:
    try:
        return Image.open(storage.local(path)) if path else None
    except OSError:      # FileNotFoundError 포함
        return None


# ---------------- 입력이 반영되는 단계 ----------------
def _hexes(bv: BrandVersion) -> str:
    return ", ".join(f"{c.get('role') or ''} {c.get('hex')}" for c in (bv.colors or []) if c.get("hex")) or "없음"


def mapping(bv: BrandVersion, brief: dict) -> list[dict]:
    """브랜드 규칙·캠페인 입력 항목이 어디에서 쓰이는지. 값이 None 인 칸은 그 단계에 들어가지 않는다는 뜻이다(학습 포인트)."""
    eff = phrases.effective(bv, brief)
    tone = (bv.image_tone or {}).get("description") or ""
    voice = bv.voice or {}
    st = bv.icon_style or {}
    style, reason = imagegen.pick_style(bv.image_tone or {}, brief or {})
    rows = [
        ("logo", "로고", f"자리 {bv.logo_position or '좌상단(기본)'} · 여백 {(bv.logo_margin or {}).get('percent', '-')}%",
         {"copy": None, "background": None, "icon": None, "compose": "지면 모서리에 배치(자리·여백)", "review": "크기·여백 검사"}),
        ("colors", "허용 색상", _hexes(bv),
         {"copy": None, "background": None, "icon": "주조·보조·강조색을 '이 색만 쓴다'로 지시", "compose": "제목 글자색·CTA 버튼·스크림", "review": "합성 색 검사"}),
        ("fonts", "폰트", ", ".join(f"{f.get('role')} {f.get('name') or '업로드'}" for f in (bv.fonts or [])) or "없음",
         {"copy": None, "background": None, "icon": None, "compose": "제목·본문 글꼴", "review": "글꼴 검사"}),
        ("icon_style", "아이콘 스타일", f"모서리 {st.get('corner') or '둥근'} · 재질 {st.get('material') or '자동'} · 굵기 {st.get('stroke_width') or 2}",
         {"copy": None, "background": None, "icon": "모서리(corner)·재질(material) 문장", "compose": None, "review": None}),
        ("image_tone", "이미지 톤", tone or "없음",
         {"copy": None, "background": "[톤] 줄에 설명 전문 + 배경 형태 자동 선택의 키워드", "icon": "재질·광택 참고 문장(배경·구도 묘사는 무시하라고 지시)", "compose": None, "review": "사람 평가(권장 항목)"}),
        ("voice", "문구 어조", f"{voice.get('description') or '없음'} · 헤드라인 {voice.get('headline_max_chars') or '-'}자 · {'존댓말' if voice.get('polite') else '어조 지정 없음'}",
         {"copy": "어조·헤드라인 권장 길이·존댓말", "background": None, "icon": None, "compose": None, "review": None}),
        ("required_phrases", f"필수 표현({eff['required_source']})", ", ".join(eff["required_phrases"]) or "없음",
         {"copy": "'반드시 포함할 표현'", "background": None, "icon": None, "compose": None, "review": "포함 여부(차단)"}),
        ("forbidden_phrases", f"금지 표현({eff['forbidden_source']})", ", ".join(eff["forbidden_phrases"]) or "없음",
         {"copy": "'절대 쓰지 말 표현'", "background": None, "icon": "기능명 제안의 금지 표현 + 코드로 기능명 재검사", "compose": None, "review": "포함 시 차단"}),
        ("brief", "상품·메시지(캠페인 입력)", " / ".join(str(brief.get(k) or "-") for k in ("product_name", "description", "key_message", "target")),
         {"copy": "상품명·설명·핵심 메시지·타깃", "background": "[내용] 줄(상품명·설명·분위기=핵심 메시지·타깃)", "icon": "기능명 제안의 상품 설명", "compose": None, "review": None}),
        ("image_style", "배경 형태", f"{style} ({reason})",
         {"copy": None, "background": "[표현 방식] 블록(prompts/image/style/*.md)", "icon": None, "compose": None, "review": None}),
    ]
    return [{"key": k, "item": item, "value": value, "stages": stages} for k, item, value, stages in rows]


# ---------------- 프롬프트 ----------------
def _brief_vars(brand_name: str, bv: BrandVersion, brief: dict) -> dict:
    """프롬프트 전문에서 색으로 표시할 '입력에서 온 값'. 짧은 분기 문구('없음')는 뺀다."""
    eff = phrases.effective(bv, brief)
    v = {"brand_name": brand_name, "product_name": brief.get("product_name"), "description": brief.get("description"),
         "key_message": brief.get("key_message"), "target": brief.get("target"),
         "tone": (bv.image_tone or {}).get("description"), "voice": (bv.voice or {}).get("description"),
         "required": ", ".join(eff["required_phrases"]), "forbidden": ", ".join(eff["forbidden_phrases"])}
    for c in bv.colors or []:
        if c.get("role") in icons.PALETTE_ROLES and c.get("hex"):
            v[f"color_{c['role']}"] = c["hex"]
    return {k: str(x) for k, x in v.items() if x and len(str(x)) >= 2}


def _copy_prompt(db: Session, d: Design, bv: BrandVersion, brand: Brand) -> tuple[str, str] | None:
    """routers_copy.create_copy 가 보내는 것과 같은 조립(지면 제한 합치기·추가 문구 자산 요구 포함)."""
    presets = [pv for pid in d.preset_version_ids if (pv := db.get(PresetVersion, pid))]
    if not presets:
        return None
    per = [(pv.placement.name, copywriter.limits_for(pv.text_limits)) for pv in presets]
    merged = copywriter.merge_limits(per)
    spec = copyfields.asset_spec(presets)
    if spec:
        merged["assets"] = spec
    names = [pv.placement.name for pv in presets]
    placement = {"platform": "모든 선택 지면 공통",
                 "name": ", ".join(names[:3]) + (f" 외 {len(names) - 3}개" if len(names) > 3 else "")}
    return copywriter._prompt(brand.name, phrases.rules(bv, d.brief), d.brief or {}, placement, merged)


def _icon_names_prompt(d: Design, bv: BrandVersion, brand: Brand) -> tuple[str, str]:
    forbidden = phrases.effective(bv, d.brief)["forbidden_phrases"]
    brief = d.brief or {}
    system = prompts.render("icons/propose3d_system", count=icons.CANDIDATES)
    user = prompts.render("icons/propose3d_user", brand_name=brand.name, product_name=brief.get("product_name"),
                          description=brief.get("description") or "없음", key_message=brief.get("key_message") or "없음",
                          target=brief.get("target") or "없음", forbidden=", ".join(forbidden) or "없음", count=icons.CANDIDATES)
    return system, user


def _utc(t: datetime | None) -> str | None:
    """DB 의 naive 현지 시각을 호출 기록과 같은 UTC ISO 문자열로 (정렬·표시를 한 기준으로)."""
    return t.astimezone(timezone.utc).isoformat(timespec="seconds") if t else None


def _row_entry(r: dict, text: dict) -> dict:
    return {"stage": STAGE_OF.get(r.get("kind"), "copy"), "kind": r.get("kind"), "source": "recorded", "ts": r.get("ts"),
            "provider": r.get("provider"), "model": r.get("model"), "ok": r.get("ok"), "error": r.get("error"),
            "ms": r.get("ms"), "size": r.get("size"), "quality": r.get("quality"),
            "tokens_in": r.get("tokens_in"), "tokens_out": r.get("tokens_out"), "cost_usd": r.get("cost_usd"),
            "system": text.get("system"), "user": text.get("user"), "prompt": text.get("prompt")}


def _bg_entry(g: GeneratedImage, source: str = "stored") -> dict:
    lay = g.layout or {}
    ref = lay.get("reference") or {}
    kind = "variant" if ref else "product" if lay.get("product_asset_id") else "background"
    return {"stage": "background", "kind": kind, "source": source,
            "ts": _utc(g.created_at),
            "provider": lay.get("provider"), "model": lay.get("model"), "quality": lay.get("quality"),
            "size": f"{g.width}x{g.height}", "prompt": g.prompt, "refs": lay.get("prompts") or [],
            "image": {"id": g.id, "file": Path(g.file_path).stem, "width": g.width, "height": g.height,
                      "template": lay.get("template"), "style": lay.get("style"), "reference": lay.get("reference")}}


def _icon_entry(a: IconAsset, source: str = "stored") -> dict:
    st = a.style or {}
    return {"stage": "icon", "kind": "icon", "source": source,
            "ts": _utc(a.created_at),
            "provider": "openai", "model": st.get("model"), "quality": st.get("quality"), "prompt": a.prompt,
            "refs": [r for r in (st.get("prompt_ref"), st.get("style_ref")) if r],
            "icon": {"id": a.id, "name": a.name, "concept": a.concept, "position": a.position}}


def prompt_entries(db: Session, d: Design, bv: BrandVersion, brand: Brand, rows: list[dict],
                   backgrounds: list[GeneratedImage], icon_set: list[IconAsset]) -> list[dict]:
    """시간순 프롬프트 목록. recorded(호출 기록 전문) 가 있으면 그것을, 같은 문장의 DB 행(배경·아이콘)은 이미지 정보만 붙인다.
    기록에 없는 DB 행은 stored, 기록·DB 둘 다 없는 채팅은 reconstructed."""
    out: list[dict] = []
    bg_by_prompt: dict[str, list[GeneratedImage]] = {}
    for g in backgrounds:
        if g.prompt:
            bg_by_prompt.setdefault(g.prompt, []).append(g)
    ic_by_prompt: dict[str, list[IconAsset]] = {}
    for a in icon_set:
        if a.prompt:
            ic_by_prompt.setdefault(a.prompt, []).append(a)
    kinds_seen: set[str] = set()
    for r in rows:
        text = calllog.prompt_text(r.get("prompt_id"))
        if not text:
            continue
        e = _row_entry(r, text)
        kinds_seen.add(e["kind"])
        p = text.get("prompt")
        if p and bg_by_prompt.get(p):
            g = bg_by_prompt[p].pop(0)
            e["image"], e["refs"] = _bg_entry(g)["image"], (g.layout or {}).get("prompts") or []
        elif p and ic_by_prompt.get(p):
            a = ic_by_prompt[p].pop(0)
            e["icon"], e["refs"] = _icon_entry(a)["icon"], _icon_entry(a)["refs"]
        out.append(e)
    for gs in bg_by_prompt.values():          # 기록 전문이 없던 때의 배경(전문은 DB 에 있다)
        out.extend(_bg_entry(g) for g in gs)
    for As in ic_by_prompt.values():
        out.extend(_icon_entry(a) for a in As)
    has_copy = db.scalar(select(CopyVariant.id).where(CopyVariant.design_id == d.id).limit(1)) is not None
    if has_copy and "copy" not in kinds_seen:
        sp = _copy_prompt(db, d, bv, brand)
        if sp:
            out.append({"stage": "copy", "kind": "copy", "source": "reconstructed", "ts": None, "provider": None,
                        "model": llm.chat_model(), "system": sp[0], "user": sp[1],
                        "note": "이 캠페인의 카피는 프롬프트 전문을 기록하기 전에 만들었습니다. 현재 브랜드 규칙·입력·프롬프트 파일로 다시 조립한 문장이라 보낸 당시와 다를 수 있습니다."})
    if icon_set and "icon_names" not in kinds_seen:
        if (d.brief or {}).get("icon_candidates"):
            out.append({"stage": "icon", "kind": "icon_names", "source": "note", "ts": None,
                        "note": "아이콘 기능명 후보는 카피 생성 호출의 응답(icons)에서 함께 받았으므로 별도 기능명 제안 호출이 없습니다. 카피 System 프롬프트의 icons 지시가 그 문장입니다."})
        else:
            s, u = _icon_names_prompt(d, bv, brand)
            out.append({"stage": "icon", "kind": "icon_names", "source": "reconstructed", "ts": None, "provider": None,
                        "model": llm.chat_model(), "system": s, "user": u,
                        "note": "기능명 제안 호출의 전문이 기록에 없어 현재 입력으로 다시 조립했습니다."})
    out.sort(key=lambda e: (e.get("ts") or "9999", e["stage"]))
    return out


# ---------------- 채점 ----------------
JUDGE_SCHEMA = llm._obj({
    "scores": llm._arr(llm._obj({"item": llm.STR, "score": {"type": "integer"}, "reason": llm.STR})),
    "overall": {"type": "integer"}, "summary": llm.STR,
})


def judgments(design_id: int) -> list[dict]:
    """채점 결과, 최신순."""
    try:
        with db_module.SessionLocal() as s:
            rows = s.scalars(select(Judgment.result).where(Judgment.design_id == design_id).order_by(Judgment.id.desc())).all()
    except SQLAlchemyError:
        return []
    return list(rows)


def judge_vars(brand: Brand, bv: BrandVersion, brief: dict, style: str | None) -> dict:
    return {"brand_name": brand.name, "tone": (bv.image_tone or {}).get("description") or "지정 없음",
            "product_name": brief.get("product_name") or "없음", "description": brief.get("description") or "없음",
            "key_message": brief.get("key_message") or "없음", "target": brief.get("target") or "없음",
            "style": style or imagegen.pick_style(bv.image_tone or {}, brief)[0]}


def judge_background(db: Session, d: Design, bv: BrandVersion, brand: Brand, g: GeneratedImage) -> dict:
    """배경 1장을 Claude 에 보여 주고 항목별 점수를 받는다(채팅 1회). 결과는 judgments 테이블에 쌓는다."""
    im = _open(g.file_path)
    if im is None:
        raise FileNotFoundError("배경 파일이 없습니다")
    im = im.convert("RGB")
    im.thumbnail((1024, 1024))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    vars = judge_vars(brand, bv, d.brief or {}, (g.layout or {}).get("style"))
    system = prompts.render("analysis/judge_system")
    user = prompts.render("analysis/judge_user", **vars)
    data = llm.vision_json(system, user, [buf.getvalue()], "judge", JUDGE_SCHEMA)
    scores = [{"item": str(s.get("item") or ""), "score": int(s.get("score") or 0), "reason": str(s.get("reason") or "")}
              for s in (data.get("scores") or []) if isinstance(s, dict)]
    last = next((r for r in reversed(calllog.calls(d.id)) if r.get("kind") == "judge"), None)
    result = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "background_id": g.id,
              "file": Path(g.file_path).stem, "template": (g.layout or {}).get("template"), "style": vars["style"],
              "model": llm.CLAUDE_MODEL, "scores": scores, "overall": int(data.get("overall") or 0),
              "summary": str(data.get("summary") or ""), "prompt_refs": [prompts.ref("analysis/judge_system"), prompts.ref("analysis/judge_user")],
              "cost_usd": (last or {}).get("cost_usd"), "tokens_in": (last or {}).get("tokens_in"), "tokens_out": (last or {}).get("tokens_out")}
    try:
        with db_module.SessionLocal() as s:
            s.add(Judgment(design_id=d.id, result=result))
            s.commit()
    except SQLAlchemyError:
        pass
    return result


# ---------------- 한 페이지 ----------------
def _preset_lite(pv: PresetVersion | None) -> dict | None:
    if pv is None:
        return None
    return {"id": pv.id, "platform": pv.placement.platform.name, "name": pv.placement.name,
            "width": pv.production_width, "height": pv.production_height, "content_kind": pv.placement.content_kind}


def _unique_backgrounds(db: Session, d: Design) -> list[GeneratedImage]:
    """배경 행은 같은 파일이 비율군의 지면마다 한 행씩 연결되므로 파일 기준으로 하나만 본다(생성 1회 = 파일 1개)."""
    seen: set[str] = set()
    out = []
    for g in db.scalars(select(GeneratedImage).where(GeneratedImage.design_id == d.id, GeneratedImage.kind == "background")
                        .order_by(GeneratedImage.id)).all():
        if g.file_path in seen:
            continue
        seen.add(g.file_path)
        out.append(g)
    return out


def build(db: Session, d: Design) -> dict:
    bv = db.get(BrandVersion, d.brand_version_id)
    brand = db.get(Brand, d.brand_id)
    brief = d.brief or {}
    rows = calllog.calls(d.id)
    backgrounds = _unique_backgrounds(db, d)
    composites = []
    for pid in d.preset_version_ids:
        g = db.scalar(select(GeneratedImage).where(GeneratedImage.design_id == d.id, GeneratedImage.preset_version_id == pid,
                                                   GeneratedImage.kind == "composite").order_by(GeneratedImage.id.desc()).limit(1))
        if g is not None:
            composites.append({"preset": _preset_lite(db.get(PresetVersion, pid)), "id": g.id, "file": Path(g.file_path).stem,
                               "width": g.width, "height": g.height, "layout": g.layout or {}})
    from .routers_review import version_icon_set
    icon_set = version_icon_set(db, d.brand_version_id)
    brand_colors = bv.colors or []
    bg_out = []
    for g in backgrounds:
        im = _open(g.file_path)
        pal = palette(im) if im is not None else []
        e = _bg_entry(g)
        bg_out.append({**e["image"], "kind": e["kind"], "quality": (g.layout or {}).get("quality"), "provider": (g.layout or {}).get("provider"),
                       "model": (g.layout or {}).get("model"), "style_reason": (g.layout or {}).get("style_reason"),
                       "created_at": e["ts"], "palette": pal, "match": color_match(pal, brand_colors), "exists": im is not None})
    ic_out = []
    for a in icon_set:
        im = _open(a.png_path)
        pal = palette(im) if im is not None else []
        ic_out.append({"id": a.id, "name": a.name, "concept": a.concept, "position": a.position, "style": a.style,
                       "violations": a.violations, "palette": pal, "match": color_match(pal, brand_colors), "exists": im is not None})
    selected = db.scalars(select(CopyVariant).where(CopyVariant.design_id == d.id, CopyVariant.selected.is_(True))).all()
    sel = selected[0] if selected else None
    variants = []
    if d.preset_version_ids:
        first = d.preset_version_ids[0]
        last = max((r.batch_no for r in db.scalars(select(CopyVariant).where(CopyVariant.design_id == d.id, CopyVariant.preset_version_id == first)).all()), default=None)
        if last:
            variants = [{"variant_no": r.variant_no, "headline": r.headline, "body": r.body, "cta": r.cta, "selected": r.selected,
                         "violations": r.violations}
                        for r in db.scalars(select(CopyVariant).where(CopyVariant.design_id == d.id, CopyVariant.preset_version_id == first,
                                                                     CopyVariant.batch_no == last).order_by(CopyVariant.variant_no)).all()]
    stages = calllog.stage_summary(d.id, STAGES)
    style, reason = imagegen.pick_style(bv.image_tone or {}, brief)
    return {
        "design": {"id": d.id, "kind": d.kind, "status": d.status, "created_at": d.created_at.isoformat(), "brief": brief,
                   "presets": [p for p in (_preset_lite(db.get(PresetVersion, pid)) for pid in d.preset_version_ids) if p],
                   "settings": imagegen.settings_for(brief), "style": style, "style_reason": reason},
        "brand": {"id": brand.id, "name": brand.name, "description": brand.description, "version_no": bv.version_no,
                  "colors": brand_colors, "fonts": bv.fonts or [], "icon_style": bv.icon_style or {}, "image_tone": bv.image_tone or {},
                  "voice": bv.voice or {}, "levels": bv.levels or {}, "logo_position": bv.logo_position,
                  "logo_margin": bv.logo_margin, "effective": phrases.effective(bv, brief)},
        "mapping": mapping(bv, brief),
        "results": {"backgrounds": bg_out, "composites": composites, "icons": ic_out,
                    "copy": {"selected": {"variant_no": sel.variant_no, "headline": sel.headline, "body": sel.body, "cta": sel.cta} if sel else None,
                             "variants": variants, "text_assets": brief.get("text_assets") or {}}},
        "prompts": prompt_entries(db, d, bv, brand, rows, backgrounds, icon_set),
        "variables": _brief_vars(brand.name, bv, brief),
        "cost": {"summary": calllog.summary(design_id=d.id), "stages": stages, "stage_label": STAGE_LABEL, "rows": rows[::-1]},
        "judgments": judgments(d.id), "judge_items": list(JUDGE_ITEMS),
        "judge_estimate": _judge_estimate(),
    }


def _judge_estimate() -> dict:
    """채점 1회의 대략적인 비용(이미지 1장 ≈ 1,500 토큰 + 프롬프트·응답)."""
    from . import pricing
    usd, est = pricing.cost_of({"ok": True, "api": "chat", "provider": "anthropic", "model": llm.CLAUDE_MODEL,
                                "tokens_in": 2500, "tokens_out": 400})
    return {"usd": usd, "krw": pricing.krw(usd), "estimated": True, "model": llm.CLAUDE_MODEL}
