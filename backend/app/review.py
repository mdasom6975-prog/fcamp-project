"""브랜드·규격 검수 (6-6). 실제 내려받을 파일(최신 합성본을 인코딩한 결과)과 편집 원본을 기준으로 판정한다.

상태: 통과 / 경고 / 수정 필요 / 확인 필요
- 규격 '강제' 위반 → 수정 필요(차단) / '권장' 위반 → 경고 / 구분 미표기 → 확인 필요(차단 아님)
- 브랜드 규칙 '필수' 위반 → 수정 필요(차단) / '권장'·'수정가능' → 경고
- 공식 기준이 확인되지 않았거나 충돌인 '강제' 조건 → 확인 필요(차단)
- 자동 판정이 어려운 항목(px 길이, 톤·어조, 광고주체 표기 등) → 확인 필요 + 사람이 '확인함' 표시 가능
"""
from pathlib import Path

from PIL import Image

from . import compose, copywriter, exporting
from .copyfields import EXTRA
from .textcount import count_len


def item(category, name, status, message="", level=None, blocking=False, needs_human=False, location=None):
    return {"category": category, "item": name, "status": status, "message": message, "level": level,
            "blocking": blocking, "needs_human": needs_human, "location": location}


def _spec_fail(level: str | None) -> tuple[str, bool]:
    if level == "강제":
        return "수정 필요", True
    if level == "권장":
        return "경고", False
    return "확인 필요", False      # 구분 미표기 (사용자 결정: 차단 아님)


def _brand_fail(level: str | None) -> tuple[str, bool]:
    return ("수정 필요", True) if level == "필수" else ("경고", False)


def _font_matches(font: dict, brand_fonts: list[dict]) -> bool:
    for f in brand_fonts:
        if font.get("source") == f.get("source") == "provided" and font.get("name") == f.get("name"):
            return True
        if font.get("source") == f.get("source") == "upload" and font.get("asset_id") == f.get("asset_id"):
            return True
    return False


def review_preset(pv, img_row, texts: dict, text_el: dict | None, logo_box, scrim_hex, font_ref,
                  brand: dict, logo_aspect, separate: bool) -> list[dict]:
    """한 지면의 검수 항목을 만든다. texts = {headline, body, cta} (내보낼 카피)."""
    out: list[dict] = []
    W, H = pv.production_width, pv.production_height
    conds = pv.conditions or []

    # ---- 규격: 파일 ----
    if img_row is None or not Path(img_row.file_path).exists():
        return [item("규격", "이미지", "수정 필요", "생성된 이미지가 없습니다", "강제", True)]
    img = Image.open(img_row.file_path)
    if img.size != (W, H):
        out.append(item("규격", "이미지 크기", "수정 필요", f"{img.size[0]}×{img.size[1]} (제작 기준 {W}×{H})", "강제", True))
    else:
        out.append(item("규격", "이미지 크기", "통과", f"{W}×{H}px"))

    fmt_c, size_c = exporting.cond(conds, "파일 형식"), exporting.cond(conds, "최대 용량")
    formats = exporting.parse_formats(fmt_c["value"]) if fmt_c else []
    max_bytes = exporting.parse_bytes(size_c["value"]) if size_c else None
    enc = exporting.encode(img, formats, max_bytes)
    shown = []
    def _unconfirmed(c):
        return c.get("status") in ("충돌", "확인 필요") or c.get("value") is None
    if fmt_c and _unconfirmed(fmt_c):
        out.append(item("규격", "파일 형식", "확인 필요", fmt_c.get("note") or "공식 기준이 확인되지 않았습니다",
                        fmt_c["level"], fmt_c["level"] == "강제"))
    elif fmt_c:
        if not formats:
            st, bl = ("확인 필요", fmt_c["level"] == "강제")
            out.append(item("규격", "파일 형식", st, f"허용 형식을 해석하지 못했습니다: {fmt_c['value']}", fmt_c["level"], bl))
        else:
            out.append(item("규격", "파일 형식", "통과", f"{enc['format']} 로 내보냅니다 (허용: {', '.join(formats)})", fmt_c["level"]))
    if size_c and _unconfirmed(size_c):
        out.append(item("규격", "최대 용량", "확인 필요", size_c.get("note") or "공식 기준이 확인되지 않았습니다",
                        size_c["level"], size_c["level"] == "강제"))
    elif size_c:
        if max_bytes is None:
            out.append(item("규격", "최대 용량", "확인 필요", f"한도를 해석하지 못했습니다: {size_c['value']}", size_c["level"],
                            size_c["level"] == "강제"))
        elif enc["ok"]:
            note = f"JPG 품질 {enc['quality']}" if enc["quality"] else ""
            out.append(item("규격", "최대 용량", "통과",
                            f"{enc['size'] / 1024:.0f}KB / 최대 {size_c['value']} {note}".strip(), size_c["level"]))
        else:
            st, bl = _spec_fail(size_c["level"])
            out.append(item("규격", "최대 용량", st, f"{enc['size'] / 1024:.0f}KB 로 한도({size_c['value']})를 넘습니다",
                            size_c["level"], bl))

    # ---- 규격: 조건 하나씩 ----
    handled = {"파일 형식", "최대 용량"}
    for c in conds:
        name, val, lv = c["name"], c.get("value"), c.get("level")
        if name in handled:
            continue
        # 공식 기준이 확인되지 않았거나 충돌이면 통과로 표시하지 않는다
        if c.get("status") in ("충돌", "확인 필요") or val is None:
            blocking = lv == "강제"
            out.append(item("규격", name, "확인 필요", c.get("note") or "공식 기준이 확인되지 않았습니다", lv, blocking))
            continue
        if name in ("최소 너비", "최소 높이", "최소 크기", "크기", "권장 해상도", "권장 크기"):
            dim = exporting.parse_dim(val)
            px = exporting.parse_px(val)
            ok, msg = True, f"{val}"
            if name == "최소 너비" and px:
                ok, msg = W >= px, f"너비 {W}px / 최소 {px}px"
            elif name == "최소 높이" and px:
                ok, msg = H >= px, f"높이 {H}px / 최소 {px}px"
            elif dim and name == "최소 크기":
                ok, msg = (W >= dim[0] and H >= dim[1]), f"{W}×{H} / 최소 {dim[0]}×{dim[1]}"
            elif dim and name == "크기":
                ok, msg = (W, H) == dim, f"{W}×{H} / 요구 {dim[0]}×{dim[1]}"
            elif dim and name in ("권장 해상도", "권장 크기"):
                ok, msg = (W, H) == dim, f"{W}×{H} / 권장 {dim[0]}×{dim[1]}"
            else:
                out.append(item("규격", name, "확인 필요", f"자동 검사하지 못했습니다: {val}", lv, False, True))
                continue
            if ok:
                out.append(item("규격", name, "통과", msg, lv))
            else:
                st, bl = _spec_fail(lv)
                out.append(item("규격", name, st, msg, lv, bl))
        elif name == "최소/최대 화면 비율":
            # Meta 표기 "400x500 / 191x100": 픽셀 쌍 두 개 = 허용 비율의 양 끝(4:5 ~ 1.91:1). 오차는 '비율 허용 오차' 조건을 따른다
            try:
                pairs = [exporting.parse_dim(tok) for tok in str(val).split("/")]
                ratios = sorted(a / b for a, b in pairs if a and b)
                lo, hi = ratios[0], ratios[-1]
                tol_c = next((x for x in conds if x["name"] == "비율 허용 오차" and x.get("value")), None)
                tol = float(str(tol_c["value"]).replace("%", "")) / 100 if tol_c else 0.01
                actual = W / H
                ok = lo * (1 - tol) <= actual <= hi * (1 + tol)
                msg = f"{W}×{H} = {actual:.3f} (허용 {lo:.3f}~{hi:.3f}, 오차 {tol * 100:g}%)"
                if ok:
                    out.append(item("규격", "화면 비율 범위", "통과", msg, lv))
                else:
                    st, bl = _spec_fail(lv)
                    out.append(item("규격", "화면 비율 범위", st, msg, lv, bl))
            except Exception:
                out.append(item("규격", name, "확인 필요", f"자동 검사하지 못했습니다: {val}", lv, False, True))
        elif name == "비율 허용 오차" and pv.ratio:
            try:
                rw, rh = [float(x) for x in pv.ratio.split(":")]
                diff = abs(W / H - rw / rh) / (rw / rh)
                tol = float(str(val).replace("%", "")) / 100
                if diff <= tol:
                    out.append(item("규격", "비율", "통과", f"{pv.ratio} 과의 차이 {diff * 100:.2f}% (허용 {val})", lv))
                else:
                    st, bl = _spec_fail(lv)
                    out.append(item("규격", "비율", st, f"{pv.ratio} 과의 차이 {diff * 100:.2f}% (허용 {val})", lv, bl))
            except Exception:
                out.append(item("규격", name, "확인 필요", f"자동 검사하지 못했습니다: {val}", lv, False, True))
        elif name == "수량":
            out.append(item("규격", "이미지 수량", "통과", f"이미지 1장 ({val})", lv))
        elif name == "필수 여부":
            continue
        elif name == "문구 수량":
            st, bl = _spec_fail(lv)
            out.append(item("문구", "문구 수량", st, f"요구: {val} — 현재는 문구를 1세트(제목 1개·설명 1개)만 만듭니다", lv, bl))
        elif name == "정사각 로고":
            dims = brand.get("logo_dims")
            if not dims:
                st, bl = _spec_fail(lv)
                out.append(item("브랜드", "정사각 로고", st, f"{val} — 브랜드에 로고가 없습니다", lv, bl))
            else:
                w_, h_ = dims
                if abs(w_ / h_ - 1) <= 0.05 and min(w_, h_) >= 128:
                    out.append(item("브랜드", "정사각 로고", "통과", f"{w_}×{h_} (1:1, 최소 128×128 이상)", lv))
                else:
                    st, bl = _spec_fail(lv)
                    out.append(item("브랜드", "정사각 로고", st,
                                    f"로고 {w_}×{h_} — 1:1 이고 128×128 이상이어야 합니다. 로고는 변형하지 않으므로 정사각 로고 파일을 따로 등록해야 합니다",
                                    lv, bl))
        else:
            # 광고주체 표기·AD 마크 영역·폰트 스타일 등 자동 판정이 어려운 조건은 사람이 확인한다
            out.append(item("규격", name, "확인 필요", f"{val} — 자동 검사 대상이 아닙니다", lv, False, True))

    # ---- 문구 ----
    limits = copywriter.limits_for(pv.text_limits or [])
    for slot, label in (("headline", "제목"), ("body", "본문"), ("cta", "CTA")):
        lim, text = limits.get(slot), texts.get(slot) or ""
        if lim is None:
            continue
        if not text.strip():
            out.append(item("문구", f"{label}({lim['field']})", "수정 필요", "내용이 비어 있습니다", lim.get("level"), True))
            continue
        n = count_len(text, lim.get("method"))
        bad = (lim.get("max") is not None and n > lim["max"]) or (lim.get("min") is not None and n < lim["min"])
        rng = f"{lim['min'] if lim.get('min') is not None else ''}~{lim['max'] if lim.get('max') is not None else ''}자"
        if not bad:
            out.append(item("문구", f"{label}({lim['field']}) 글자 수", "통과", f"{n}자 / {rng}", lim.get("level")))
        else:
            st, bl = _spec_fail(lim.get("level"))
            out.append(item("문구", f"{label}({lim['field']}) 글자 수", st, f"{n}자 / {rng}", lim.get("level"), bl))
    mapped = {v["field"] for k, v in limits.items() if k in ("headline", "body", "cta") and v}
    for t in pv.text_limits or []:
        f = t["field"]
        if t.get("count_method") == "px 길이":
            out.append(item("문구", f"{f} (px 길이)", "확인 필요",
                            f"{t.get('min_px')}px 이상 필요 — 매체가 그리는 글꼴로 측정해야 해 자동 판정하지 않습니다",
                            t.get("level"), False, True))
        elif f in EXTRA:
            src, label = EXTRA[f]
            value = (texts.get("body") if src == "body" else brand.get("name")) or ""
            n = count_len(value, t.get("count_method"))
            if not value.strip():
                out.append(item("문구", f"{f}", "수정 필요", f"{label}이(가) 비어 있습니다", t.get("level") or "강제", True))
            elif (t.get("max_chars") is not None and n > t["max_chars"]) or (t.get("min_chars") is not None and n < t["min_chars"]):
                st, bl = _spec_fail(t.get("level"))
                out.append(item("문구", f"{f} 글자 수", st, f"{n}자 / ~{t.get('max_chars')}자 ({label} 사용)", t.get("level"), bl))
            else:
                out.append(item("문구", f"{f} 글자 수", "통과", f"{n}자 / ~{t.get('max_chars')}자 ({label} 사용)", t.get("level")))
        elif t.get("required") and f not in mapped:
            out.append(item("문구", f"필수 문구 '{f}'", "수정 필요", "규격상 필수인데 아직 입력/생성되지 않았습니다",
                            t.get("level") or "강제", True))

    # ---- 브랜드 규칙 ----
    lv = brand["levels"]
    joined = " ".join(texts.get(k) or "" for k in ("headline", "body", "cta"))
    for ph in brand["forbidden_phrases"]:
        if ph and ph in joined:
            st, bl = _brand_fail(lv.get("forbidden_phrases"))
            out.append(item("브랜드", "금지 표현", st, f"'{ph}' 포함", lv.get("forbidden_phrases"), bl))
    if brand["forbidden_phrases"] and not any(ph and ph in joined for ph in brand["forbidden_phrases"]):
        out.append(item("브랜드", "금지 표현", "통과", "금지 표현이 없습니다", lv.get("forbidden_phrases")))
    for ph in brand["required_phrases"]:
        if ph and ph not in joined:
            st, bl = _brand_fail(lv.get("required_phrases"))
            out.append(item("브랜드", "필수 표현", st, f"'{ph}' 누락", lv.get("required_phrases"), bl))
    if brand["required_phrases"] and all(ph in joined for ph in brand["required_phrases"] if ph):
        out.append(item("브랜드", "필수 표현", "통과", "필수 표현이 모두 있습니다", lv.get("required_phrases")))

    palette = {c["hex"].upper() for c in brand["colors"] if c.get("hex")}
    if text_el and not separate:
        col = (text_el.get("color") or "").upper()
        if not palette:
            out.append(item("브랜드", "문구 색", "확인 필요", "브랜드에 등록된 색이 없어 비교할 수 없습니다", lv.get("colors"), False, True))
        elif col in palette:
            out.append(item("브랜드", "문구 색", "통과", col, lv.get("colors")))
        else:
            st, bl = _brand_fail(lv.get("colors"))
            out.append(item("브랜드", "문구 색", st, f"{col} 은(는) 브랜드 색이 아닙니다", lv.get("colors"), bl))
        if font_ref:
            if not brand["fonts"]:
                out.append(item("브랜드", "글꼴", "확인 필요", "브랜드에 등록된 글꼴이 없어 비교할 수 없습니다", lv.get("fonts"), False, True))
            elif _font_matches(font_ref, brand["fonts"]):
                out.append(item("브랜드", "글꼴", "통과", "브랜드 글꼴을 사용했습니다", lv.get("fonts")))
            else:
                st, bl = _brand_fail(lv.get("fonts"))
                out.append(item("브랜드", "글꼴", st, "브랜드에 등록된 글꼴이 아닙니다", lv.get("fonts"), bl))
    if scrim_hex and not separate and palette and scrim_hex.upper() not in palette:
        out.append(item("브랜드", "가독성 막(스크림) 색", "경고",
                        f"{scrim_hex} 은(는) 브랜드 색이 아닙니다 (도형 요소)", lv.get("colors")))

    # 로고
    if separate:
        out.append(item("브랜드", "로고", "통과" if brand["logo_asset_id"] else "수정 필요",
                        "로고 원본 파일을 별도로 제공합니다" if brand["logo_asset_id"] else "브랜드에 로고가 없습니다",
                        lv.get("logo"), not brand["logo_asset_id"] and lv.get("logo") == "필수"))
    elif not brand["logo_asset_id"]:
        st, bl = _brand_fail(lv.get("logo"))
        out.append(item("브랜드", "로고", st, "브랜드에 로고가 등록되어 있지 않습니다", lv.get("logo"), bl))
    elif logo_box is None:
        st, bl = _brand_fail(lv.get("logo"))
        out.append(item("브랜드", "로고", st, "이미지에 로고가 합성되어 있지 않습니다", lv.get("logo"), bl))
    else:
        bw, bh = logo_box[2] - logo_box[0], logo_box[3] - logo_box[1]
        dist = abs(bw / bh - logo_aspect) / logo_aspect if logo_aspect and bh else 0
        if dist <= 0.02:
            out.append(item("브랜드", "로고 비율", "통과", f"원본 비율 유지 (차이 {dist * 100:.1f}%)", lv.get("logo")))
        else:
            st, bl = _brand_fail(lv.get("logo"))
            out.append(item("브랜드", "로고 비율", st, f"원본 비율과 {dist * 100:.1f}% 달라 변형되었습니다", lv.get("logo"), bl))

    # 안전영역·잘림
    if not separate:
        L, T, R, B, _ = compose._margins(W, H, pv.safe_area)
        safe_level = (pv.safe_area or {}).get("level") or "권장"
        for label, box in (("문구", text_el.get("box") if text_el else None), ("로고", logo_box)):
            if not box:
                continue
            x0, y0, x1, y1 = box
            if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
                out.append(item("규격", f"{label} 잘림", "수정 필요", "캔버스 밖으로 나가 잘립니다", "강제", True, location={"box": box}))
            elif x0 < L or y0 < T or x1 > W - R or y1 > H - B:
                st, bl = _spec_fail(safe_level)
                out.append(item("규격", f"{label} 안전영역", st, "안전영역/여백 안쪽에 걸쳐 있습니다", safe_level, bl,
                                location={"box": box}))
            else:
                out.append(item("규격", f"{label} 안전영역", "통과", "안전영역 안에 있습니다", safe_level))

    # 톤·어조: 사람이 기준 자료와 비교해 확인한다
    out.append(item("브랜드", "이미지 톤", "확인 필요", "브랜드 기준 자료와 결과 이미지를 비교해 확인해 주세요",
                    lv.get("image_tone"), lv.get("image_tone") == "필수", True))
    out.append(item("브랜드", "문구 어조", "확인 필요", "브랜드 어조·예시 문구와 비교해 확인해 주세요",
                    lv.get("voice"), lv.get("voice") == "필수", True))
    return out


def review_icons(icons: list, brand: dict) -> list[dict]:
    out = []
    lv = brand["levels"]
    if len(icons) >= 10:
        out.append(item("시안", "기능 수", "통과", f"{len(icons)}개"))
    else:
        out.append(item("시안", "기능 수", "수정 필요", f"{len(icons)}개 / 최소 10개", "강제", True))
    st_brand = brand["icon_style"] or {}
    palette = {c["hex"].upper() for c in brand["colors"] if c.get("hex")}
    three_d = [a for a in icons if (a.style or {}).get("render") == "3d"]
    if three_d:
        # 3d: 세트 전체가 같은 스타일 블록·모델·품질로 렌더됐는지 (재질·광원·카메라는 프롬프트가 고정한다)
        keys = {(a.style.get("style_ref"), a.style.get("prompt_ref"), a.style.get("model"), a.style.get("quality"),
                 a.style.get("corner")) for a in icons}
        bad_corner = [a.name for a in icons if st_brand.get("corner") and a.style.get("corner") != st_brand["corner"]]
        if len(keys) > 1 or len(three_d) != len(icons) or bad_corner:
            st, bl = _brand_fail(lv.get("icon_style"))
            msg = "세트 안에 다른 스타일(프롬프트·모델·품질)로 만든 아이콘이 섞여 있습니다"
            if bad_corner:
                msg = f"브랜드 모서리 설정과 다른 아이콘: {', '.join(bad_corner)}"
            out.append(item("브랜드", "아이콘 스타일", st, msg, lv.get("icon_style"), bl))
        else:
            a0 = three_d[0].style
            out.append(item("브랜드", "아이콘 스타일", "통과",
                            f"모든 아이콘이 같은 3D 스타일({a0.get('model')} {a0.get('quality')}, {a0.get('style_ref')})로 렌더됐습니다",
                            lv.get("icon_style")))
        # 색: 렌더 이미지의 색은 코드로 단정하기 어려워 사람이 확인한다 (이미지 톤과 같은 처리)
        used = sorted({c.upper() for a in icons for c in (a.style.get("colors") or [])})
        out.append(item("브랜드", "아이콘 색", "확인 필요",
                        f"지정한 색({', '.join(used) or '없음'}) 안에서 렌더됐는지 결과 이미지를 확인해 주세요",
                        lv.get("colors"), lv.get("colors") == "필수", True))
        opaque = [a.name for a in icons if a.style.get("transparent") is False]
        if opaque:
            out.append(item("시안", "투명 배경", "수정 필요", f"배경이 투명하지 않은 아이콘: {', '.join(opaque)} — 다시 그려 주세요",
                            "강제", True))
        else:
            out.append(item("시안", "투명 배경", "통과", "모든 아이콘의 배경이 투명합니다"))
    else:
        bad_style = [a.name for a in icons if
                     (st_brand.get("stroke_width") and abs(float(a.style.get("stroke_width", 0)) - float(st_brand["stroke_width"])) > 0.01)
                     or (st_brand.get("corner") and a.style.get("corner") != st_brand["corner"])
                     or (st_brand.get("fill") and a.style.get("fill") != st_brand["fill"])]
        if bad_style:
            st, bl = _brand_fail(lv.get("icon_style"))
            out.append(item("브랜드", "아이콘 스타일", st, f"브랜드 스타일과 다른 아이콘: {', '.join(bad_style)}", lv.get("icon_style"), bl))
        else:
            out.append(item("브랜드", "아이콘 스타일", "통과", "선 굵기·모서리·채움이 브랜드와 같습니다", lv.get("icon_style")))
        colors = {a.style.get("color", "").upper() for a in icons}
        if palette and not colors <= palette:
            st, bl = _brand_fail(lv.get("colors"))
            out.append(item("브랜드", "아이콘 색", st, f"브랜드 색이 아닌 색: {', '.join(sorted(colors - palette))}", lv.get("colors"), bl))
        elif palette:
            out.append(item("브랜드", "아이콘 색", "통과", ", ".join(sorted(colors)), lv.get("colors")))
    margins = [min(a.style["bbox"][0], a.style["bbox"][1], 24 - a.style["bbox"][2], 24 - a.style["bbox"][3])
               for a in icons if a.style.get("bbox")]
    if margins and min(margins) < 1.9:
        out.append(item("규격", "아이콘 여백", "경고", f"최소 여백 {min(margins):.2f} / 기준 2.0"))
    else:
        out.append(item("규격", "아이콘 여백", "통과", "모든 아이콘의 여백이 같습니다"))
    for a in icons:
        for ph in brand["forbidden_phrases"]:
            if ph and ph in a.name:
                st, bl = _brand_fail(lv.get("forbidden_phrases"))
                out.append(item("브랜드", "금지 표현", st, f"기능명 '{a.name}' 에 '{ph}' 포함", lv.get("forbidden_phrases"), bl))
    return out
