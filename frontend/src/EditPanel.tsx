import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties, KeyboardEvent as RKeyboardEvent, PointerEvent as RPointerEvent } from 'react'
import { busy as overlay } from './Busy'

type Font = { label: string; ref: Record<string, unknown> }
type El = { id: number; type: 'image' | 'shape' | 'logo' | 'text' | 'body' | 'cta'; role: string; locked: boolean; props: Record<string, any> }
type EditData = { canvas: [number, number]; name: string; elements: El[]; fonts: Font[]; brand_colors: { role: string; hex: string }[]; edited: boolean }
type Rect = { id: number; x: number; y: number; w: number; h: number }
type Guide = { axis: 'x' | 'y'; at: number }
type Drag = { id: number; mode: 'move' | 'resize'; sx: number; sy: number; ox: number; oy: number; w: number; h: number; scale: number; others: Rect[] }

async function call(method: string, url: string, body?: unknown) {
  const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) {
    const d = j.detail
    throw new Error(Array.isArray(d) ? d.map((x: { msg: string }) => x.msg).join(', ') : d || '요청에 실패했습니다')
  }
  return j
}

const num = (v: string) => (v === '' || Number.isNaN(Number(v)) ? 0 : Number(v))
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
const MOVABLE = ['text', 'body', 'cta', 'logo']      // 미리보기에서 끌어 옮길 수 있는 요소(서버가 상자를 재는 종류)
const SNAP_PX = 7                                    // 화면 픽셀 기준 달라붙는 거리

/** 시작 위치 start·크기 size 인 구간의 시작·중앙·끝 중 가장 가까운 목표(targets)에 달라붙는다. 목표를 못 찾으면 그대로. */
function snapAxis(start: number, size: number, targets: number[], thr: number): { pos: number; at: number | null } {
  let best: { d: number; at: number } | null = null
  for (const edge of [start, start + size / 2, start + size]) {
    for (const t of targets) {
      const d = t - edge
      if (Math.abs(d) <= thr && (!best || Math.abs(d) < Math.abs(best.d))) best = { d, at: t }
    }
  }
  return best ? { pos: start + best.d, at: best.at } : { pos: start, at: null }
}

export default function EditPanel({ designId, presetId, onClose, onSaved }: {
  designId: number; presetId: number; onClose: () => void; onSaved: () => void
}) {
  const base = `/api/designs/${designId}/presets/${presetId}/edit`
  const [data, setData] = useState<EditData | null>(null)
  const [els, setEls] = useState<El[]>([])
  const [preview, setPreview] = useState<string | null>(null)
  const [boxes, setBoxes] = useState<Record<string, number[]>>({})     // 요소 id → 마지막 미리보기에서 잰 상자(캔버스 px)
  const [margins, setMargins] = useState<number[]>([0, 0, 0, 0])       // 안전영역 여백 [좌, 상, 우, 하]
  const [warns, setWarns] = useState<string[]>([])
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [sel, setSel] = useState<number | null>(null)
  const [guides, setGuides] = useState<Guide[]>([])
  const [dragging, setDragging] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const seq = useRef(0)           // 미리보기 요청 순번: 늦게 도착한 옛 응답이 새 응답을 덮지 않게
  const stage = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)

  const load = useCallback(async () => {
    const j: EditData = await call('GET', base)
    setData(j); setEls(j.elements)
  }, [base])

  useEffect(() => { load().catch((e) => setMsg({ ok: false, text: e.message })) }, [load])

  // 잠기지 않은 요소의 값만 보낸다 (잠긴 요소는 서버가 변경을 거절한다)
  const payload = (list: El[]) => list.filter((e) => !e.locked).map((e) => ({ id: e.id, props: e.props }))

  useEffect(() => {
    if (!data) return
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(async () => {
      const mine = ++seq.current
      try {
        const j = await call('POST', `${base}/preview`, { elements: payload(els) })
        if (mine !== seq.current) return
        setPreview(j.image); setWarns(j.warnings); setBoxes(j.boxes ?? {}); setMargins(j.margins ?? [0, 0, 0, 0])
        setMsg((m) => (m && !m.ok ? null : m))
      } catch (e) { if (mine === seq.current) setMsg({ ok: false, text: (e as Error).message }) }
    }, 350)
    return () => window.clearTimeout(timer.current)
  }, [els, data, base])

  const patch = (id: number, p: Record<string, any>) =>
    setEls((l) => l.map((e) => (e.id === id ? { ...e, props: { ...e.props, ...p } } : e)))
  const nudge = (e: El, dx: number, dy: number) => patch(e.id, { x: e.props.x + dx, y: e.props.y + dy })

  const run = async (fn: () => Promise<any>, okText: string | ((j: any) => string)) => {
    setBusy(true); setMsg(null)
    try { const j = await fn(); if (j?.elements) setEls(j.elements); if (j?.warnings) setWarns(j.warnings); setMsg({ ok: true, text: typeof okText === 'function' ? okText(j) : okText }); onSaved() }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }) }
    finally { setBusy(false) }
  }

  const save = () => run(() => call('PUT', base, { elements: payload(els) }), '저장했습니다. (저장 시점 스냅샷이 남았습니다)')
  const undo = () => run(() => call('POST', `${base}/undo`), (j: any) => `실행 취소: ${j?.to ?? '이전 상태'}로 돌아갔습니다. (배경 이미지는 되돌리지 않습니다)`)
  const reset = () => run(() => call('POST', `${base}/reset`), '처음 상태(자동 배치)로 되돌렸습니다.')
  const regen = (element: 'background' | 'text') => overlay.run(
    element === 'background' ? '배경 새로 만드는 중… 편집 중인 매체의 비율군만 만들어 적용합니다 (1~3분)' : '문구 다시 만드는 중… (수십 초)',
    () => regenInner(element))
  const regenInner = (element: 'background' | 'text') =>
    run(() => call('POST', `${base}/regenerate`, { element }), element === 'background'
      ? (j: any) => {
          const sk = (j?.skipped ?? []) as { name: string; reason: string }[]
          const df = (j?.deferred ?? []) as string[]
          const n = (j?.applied ?? []).length
          return `배경을 새로 만들어 모든 매체에 적용했습니다. 잠긴 요소는 유지됩니다.${n ? ` 편집 중인 ${n}개 매체도 다시 그렸습니다.` : ''}`
            + (sk.length ? ` 반영 안 됨: ${sk.map((x) => `${x.name}(${x.reason})`).join(' / ')}` : '')
            + (df.length ? ` 편집 중이 아닌 비율군(${df.join(', ')})은 그 매체를 다시 합성할 때 같은 장면으로 만듭니다.` : '')
        }
      : '문구를 새로 만들었습니다.')
  const toggleLock = async (e: El) => {
    setBusy(true)
    try { const j = await call('PUT', base, { elements: [{ id: e.id, locked: !e.locked }] }); setEls(j.elements) }
    catch (x) { setMsg({ ok: false, text: (x as Error).message }) } finally { setBusy(false) }
  }

  if (!data) return <div className="editor"><p className="muted">편집 정보를 불러오는 중…</p>{msg && <div className="notice err">{msg.text}</div>}</div>

  const [W, H] = data.canvas
  const Lock = ({ e }: { e: El }) => (
    <label className="lockbox"><input type="checkbox" checked={e.locked} disabled={busy} onChange={() => toggleLock(e)} /> 잠금</label>
  )
  const fontKey = (f: Record<string, any>) => JSON.stringify(f)

  // ---- 드래그앤드랍 ----
  // 상자의 위치·너비는 현재 값(props)에서, 높이는 마지막 미리보기에서 잰 값에서 구한다 → 끄는 동안 상자가 포인터를 바로 따라가고,
  // 서버가 그린 그림은 놓은 뒤(350ms 디바운스) 따라온다.
  const rectOf = (e: El): Rect | null => {
    const bx = boxes[String(e.id)]
    if (!MOVABLE.includes(e.type) || !bx) return null
    const bw = Math.max(1, bx[2] - bx[0]), bh = bx[3] - bx[1]
    const w = Number(e.props.w), h = e.type === 'cta' ? Number(e.props.h) : e.type === 'logo' ? bh * (w / bw) : bh
    return { id: e.id, x: Number(e.props.x), y: Number(e.props.y), w, h }
  }
  const hits = els.map((e) => ({ e, r: rectOf(e) })).filter((v): v is { e: El; r: Rect } => v.r !== null)

  const pick = (id: number) => {
    setSel(id)
    document.getElementById(`edel-${id}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
  const startDrag = (ev: RPointerEvent, e: El, r: Rect, mode: 'move' | 'resize') => {
    pick(e.id)
    if (e.locked || busy || !stage.current) return
    ev.preventDefault(); ev.stopPropagation()
    const target = ev.currentTarget as HTMLElement
    target.setPointerCapture(ev.pointerId)
    // pointerdown 의 기본 동작을 막으면 포커스가 안 옮겨지므로 상자에 직접 준다(방향키 이동이 이 상자에 먹도록)
    ;(target.closest('.hit') as HTMLElement | null)?.focus({ preventScroll: true })
    const scale = stage.current.getBoundingClientRect().width / W
    drag.current = { id: e.id, mode, sx: ev.clientX, sy: ev.clientY, ox: r.x, oy: r.y, w: r.w, h: r.h, scale, others: hits.map((v) => v.r).filter((o) => o.id !== e.id) }
    setDragging(true)
  }
  const moveDrag = (ev: RPointerEvent) => {
    const d = drag.current
    if (!d) return
    const dx = (ev.clientX - d.sx) / d.scale, dy = (ev.clientY - d.sy) / d.scale
    const thr = SNAP_PX / d.scale
    const [L, T, R, B] = margins
    const gs: Guide[] = []
    if (d.mode === 'move') {
      let x = d.ox + dx, y = d.oy + dy
      if (!ev.altKey) {     // Alt 를 누르면 달라붙지 않고 자유롭게 옮긴다
        const sx = snapAxis(x, d.w, [L, W - R, W / 2, ...d.others.flatMap((o) => [o.x, o.x + o.w])], thr)
        const sy = snapAxis(y, d.h, [T, H - B, H / 2, ...d.others.flatMap((o) => [o.y, o.y + o.h])], thr)
        x = sx.pos; y = sy.pos
        if (sx.at !== null) gs.push({ axis: 'x', at: sx.at })
        if (sy.at !== null) gs.push({ axis: 'y', at: sy.at })
      }
      patch(d.id, { x: clamp(Math.round(x), -W, 2 * W), y: clamp(Math.round(y), -H, 2 * H) })
    } else {
      let w = d.w + dx
      if (!ev.altKey) {
        const right = snapAxis(d.ox + w, 0, [W - R, W / 2, ...d.others.map((o) => o.x + o.w)], thr)
        w = right.pos - d.ox
        if (right.at !== null) gs.push({ axis: 'x', at: right.at })
      }
      patch(d.id, { w: clamp(Math.round(w), 20, 2 * W) })
    }
    setGuides(gs)
  }
  const endDrag = () => {
    if (!drag.current) return
    drag.current = null
    setDragging(false); setGuides([])
  }
  const keyMove = (ev: RKeyboardEvent, e: El) => {
    const step = ev.shiftKey ? 10 : 1
    const v = ev.key === 'ArrowLeft' ? [-step, 0] : ev.key === 'ArrowRight' ? [step, 0] : ev.key === 'ArrowUp' ? [0, -step] : ev.key === 'ArrowDown' ? [0, step] : null
    if (!v) return
    ev.preventDefault()
    if (!e.locked && !busy) nudge(e, v[0], v[1])
  }
  const pct = (v: number, total: number) => `${(v / total) * 100}%`

  // 제목·본문이 같이 쓰는 문구 컨트롤 (본문은 줄 수 상한이 더 있고, '문구 다시 생성'은 제목만)
  const textControls = (e: El, isBody: boolean) => (
    <div className="ctl" aria-disabled={e.locked}>
      <textarea rows={isBody ? 3 : 2} disabled={e.locked} value={e.props.text} onChange={(x) => patch(e.id, { text: x.target.value })} />
      <div className="row">
        <label>글꼴 <select disabled={e.locked} value={fontKey(e.props.font)}
          onChange={(x) => patch(e.id, { font: JSON.parse(x.target.value) })}>
          {data.fonts.map((f) => <option key={fontKey(f.ref)} value={fontKey(f.ref)}>{f.label}</option>)}
        </select></label>
        <label>크기 <input type="number" disabled={e.locked} min={8} max={400} value={e.props.font_size}
          onChange={(x) => patch(e.id, { font_size: num(x.target.value) })} /></label>
        <label>정렬 <select disabled={e.locked} value={e.props.align} onChange={(x) => patch(e.id, { align: x.target.value })}>
          <option>왼쪽</option><option>가운데</option><option>오른쪽</option></select></label>
        {isBody && (
          <label>줄 수 상한 <select disabled={e.locked} value={e.props.max_lines ?? 2} onChange={(x) => patch(e.id, { max_lines: Number(x.target.value) })}>
            {[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}줄</option>)}</select></label>
        )}
      </div>
      <div className="row">
        <label>색 <input type="color" disabled={e.locked} value={e.props.color} onChange={(x) => patch(e.id, { color: x.target.value.toUpperCase() })} /></label>
        <input className="hex" disabled={e.locked} value={e.props.color} maxLength={7} onChange={(x) => patch(e.id, { color: x.target.value })} />
        {data.brand_colors.filter((c) => c.hex).map((c, i) => (
          <button key={i} type="button" disabled={e.locked} title={c.role} onClick={() => patch(e.id, { color: c.hex.toUpperCase() })}
            style={{ background: c.hex, width: 26, height: 26, padding: 0, borderRadius: 13 }} aria-label={`브랜드 색 ${c.role}`} />
        ))}
      </div>
      <Pos e={e} patch={patch} nudge={nudge} wLabel="너비" />
      {!isBody && e.props.text_override && <span className="muted">이 지면에서 따로 수정한 문구입니다(공통 변경에서 보호됨)</span>}
      {!isBody && <div><button disabled={e.locked} onClick={() => regen('text')}>문구 다시 생성</button></div>}
    </div>
  )

  return (
    <div className="editor">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <strong>편집 · {data.name} <span className="muted">{W}×{H}px</span></strong>
        <button onClick={onClose}>닫기</button>
      </div>
      <div className="editor-body">
        <div className="editor-preview">
          <div className="editor-stage" ref={stage} style={{ ['--ar' as string]: W / H } as CSSProperties}>
            {preview ? <img src={preview} alt="편집 미리보기" draggable={false} /> : <p className="muted" style={{ padding: 12 }}>미리보기를 만드는 중…</p>}
            {dragging && <div className="safe-rect" style={{ left: pct(margins[0], W), top: pct(margins[1], H), right: pct(margins[2], W), bottom: pct(margins[3], H) }} />}
            {hits.map(({ e, r }) => (
              <div key={e.id} className={`hit${sel === e.id ? ' sel' : ''}${e.locked ? ' locked' : ''}`} tabIndex={0}
                role="button" aria-label={`${e.role} 위치 조정${e.locked ? ' (잠김)' : ''}`}
                title={e.locked ? `${e.role}: 잠겨 있어 옮길 수 없습니다` : `${e.role}: 끌어서 옮기기 · 방향키 1px (Shift 10px) · Alt 누르면 자유 이동`}
                style={{ left: pct(r.x, W), top: pct(r.y, H), width: pct(r.w, W), height: pct(r.h, H) }}
                onPointerDown={(ev) => startDrag(ev, e, r, 'move')} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag}
                onFocus={() => setSel(e.id)} onKeyDown={(ev) => keyMove(ev, e)}>
                <span className="hit-tag">{e.role}{e.locked ? ' 🔒' : ''}</span>
                {!e.locked && (e.type === 'text' || e.type === 'body' || e.type === 'logo') && (
                  <span className="hit-handle" title="끌어서 너비 조절" onPointerDown={(ev) => startDrag(ev, e, r, 'resize')} />
                )}
              </div>
            ))}
            {guides.map((g, i) => g.axis === 'x'
              ? <div key={i} className="guide-x" style={{ left: pct(g.at, W) }} />
              : <div key={i} className="guide-y" style={{ top: pct(g.at, H) }} />)}
          </div>
          <p className="muted editor-hint">상자를 끌어 옮기고, 제목·본문·로고는 오른쪽 가장자리를 끌어 너비를 조절합니다. 안전영역·중앙·다른 요소에 달라붙고, Alt 를 누르면 자유롭게 움직입니다. 방향키는 1px, Shift+방향키는 10px 이동합니다.</p>
          {warns.map((w, i) => <div key={i} className="badge b-warn">{w}</div>)}
        </div>

        <div className="editor-controls">
          {els.map((e) => (
            <fieldset key={e.id} id={`edel-${e.id}`} disabled={busy} className={`${e.locked ? 'locked' : ''}${sel === e.id ? ' sel' : ''}`}
              onClick={() => setSel(e.id)} onFocusCapture={() => setSel(e.id)}>
              <legend>{e.role}</legend>
              <Lock e={e} />
              {e.locked && <span className="muted"> 잠겨 있어 수정할 수 없습니다. 수정하려면 잠금을 해제하세요.</span>}

              {e.type === 'text' && textControls(e, false)}
              {e.type === 'body' && textControls(e, true)}

              {e.type === 'cta' && (
                <div className="ctl">
                  <span className="muted">버튼 문구 “{e.props.text}” · CTA 는 위치만 수정할 수 있습니다. 문구와 색은 카피·브랜드 설정을 따릅니다.</span>
                  <Pos e={e} patch={patch} nudge={nudge} showW={false} />
                </div>
              )}

              {e.type === 'logo' && (
                <div className="ctl">
                  <Pos e={e} patch={patch} nudge={nudge} wLabel="너비(비율 고정)" />
                  <span className="muted">로고는 원본 비율을 유지하며 변형·재생성하지 않습니다.</span>
                </div>
              )}

              {e.type === 'image' && (
                <div className="ctl">
                  <label>확대/축소 {e.props.zoom.toFixed(2)}배 <input type="range" min={0.5} max={3} step={0.05} disabled={e.locked}
                    value={e.props.zoom} onChange={(x) => patch(e.id, { zoom: Number(x.target.value) })} /></label>
                  <label>가로 초점 <input type="range" min={0} max={1} step={0.02} disabled={e.locked}
                    value={e.props.cx} onChange={(x) => patch(e.id, { cx: Number(x.target.value) })} /></label>
                  <label>세로 초점 <input type="range" min={0} max={1} step={0.02} disabled={e.locked}
                    value={e.props.cy} onChange={(x) => patch(e.id, { cy: Number(x.target.value) })} /></label>
                  <div><button disabled={e.locked} onClick={() => regen('background')}>배경 새로 생성 (비용 발생)</button></div>
                </div>
              )}

              {e.type === 'shape' && (
                <div className="ctl row">
                  <label>색 <input type="color" disabled={e.locked} value={e.props.color} onChange={(x) => patch(e.id, { color: x.target.value.toUpperCase() })} /></label>
                  <label>진하기 {Math.round(e.props.opacity * 100)}% <input type="range" min={0} max={1} step={0.02} disabled={e.locked}
                    value={e.props.opacity} onChange={(x) => patch(e.id, { opacity: Number(x.target.value) })} /></label>
                </div>
              )}
            </fieldset>
          ))}
        </div>
      </div>

      {msg && <div className={msg.ok ? 'notice ok' : 'notice err'}>{msg.text}</div>}
      <div className="row">
        <button className="primary" disabled={busy} onClick={save}>저장</button>
        <button disabled={busy} onClick={undo}>실행 취소</button>
        <button disabled={busy} onClick={reset}>처음 상태로 되돌리기</button>
        <span className="muted">실행 취소는 직전에 저장한 상태로 돌아갑니다(배경 이미지 제외). 저장 전에는 미리보기만 바뀝니다.</span>
      </div>
    </div>
  )
}

function Pos({ e, patch, nudge, wLabel = '너비', showW = true }: {
  e: El; patch: (id: number, p: Record<string, any>) => void; nudge: (e: El, dx: number, dy: number) => void; wLabel?: string; showW?: boolean
}) {
  const step = 10
  return (
    <div className="row">
      <label>X <input type="number" disabled={e.locked} value={e.props.x} onChange={(x) => patch(e.id, { x: num(x.target.value) })} /></label>
      <label>Y <input type="number" disabled={e.locked} value={e.props.y} onChange={(x) => patch(e.id, { y: num(x.target.value) })} /></label>
      {showW && <label>{wLabel} <input type="number" disabled={e.locked} value={e.props.w} onChange={(x) => patch(e.id, { w: num(x.target.value) })} /></label>}
      <span className="nudge">
        <button type="button" disabled={e.locked} onClick={() => nudge(e, -step, 0)} aria-label="왼쪽으로">←</button>
        <button type="button" disabled={e.locked} onClick={() => nudge(e, 0, -step)} aria-label="위로">↑</button>
        <button type="button" disabled={e.locked} onClick={() => nudge(e, 0, step)} aria-label="아래로">↓</button>
        <button type="button" disabled={e.locked} onClick={() => nudge(e, step, 0)} aria-label="오른쪽으로">→</button>
      </span>
    </div>
  )
}
