import { useCallback, useEffect, useRef, useState } from 'react'
import { busy as overlay } from './Busy'

type Font = { label: string; ref: Record<string, unknown> }
type El = { id: number; type: 'image' | 'shape' | 'logo' | 'text'; role: string; locked: boolean; props: Record<string, any> }
type EditData = { canvas: [number, number]; name: string; elements: El[]; fonts: Font[]; brand_colors: { role: string; hex: string }[]; edited: boolean }

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

export default function EditPanel({ designId, presetId, onClose, onSaved }: {
  designId: number; presetId: number; onClose: () => void; onSaved: () => void
}) {
  const base = `/api/designs/${designId}/presets/${presetId}/edit`
  const [data, setData] = useState<EditData | null>(null)
  const [els, setEls] = useState<El[]>([])
  const [preview, setPreview] = useState<string | null>(null)
  const [warns, setWarns] = useState<string[]>([])
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef<number | undefined>(undefined)

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
      try {
        const j = await call('POST', `${base}/preview`, { elements: payload(els) })
        setPreview(j.image); setWarns(j.warnings); setMsg((m) => (m && !m.ok ? null : m))
      } catch (e) { setMsg({ ok: false, text: (e as Error).message }) }
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
  const reset = () => run(() => call('POST', `${base}/reset`), '처음 상태(자동 배치)로 되돌렸습니다.')
  const regen = (element: 'background' | 'text') => overlay.run(
    element === 'background' ? '배경 새로 만드는 중… 모든 매체에 적용합니다 (1~3분)' : '문구 다시 만드는 중… (수십 초)',
    () => regenInner(element))
  const regenInner = (element: 'background' | 'text') =>
    run(() => call('POST', `${base}/regenerate`, { element }), element === 'background'
      ? (j: any) => {
          const sk = (j?.skipped ?? []) as { name: string; reason: string }[]
          const n = (j?.applied ?? []).length
          return `배경을 새로 만들어 모든 매체에 적용했습니다. 잠긴 요소는 유지됩니다.${n ? ` 편집 중인 ${n}개 매체도 다시 그렸습니다.` : ''}`
            + (sk.length ? ` 반영 안 됨: ${sk.map((x) => `${x.name}(${x.reason})`).join(' / ')}` : '')
        }
      : '문구를 새로 만들었습니다.')
  const toggleLock = async (e: El) => {
    setBusy(true)
    try { const j = await call('PUT', base, { elements: [{ id: e.id, locked: !e.locked }] }); setEls(j.elements) }
    catch (x) { setMsg({ ok: false, text: (x as Error).message }) } finally { setBusy(false) }
  }

  if (!data) return <div className="editor"><p className="muted">편집 정보를 불러오는 중…</p>{msg && <div className="notice err">{msg.text}</div>}</div>

  const Lock = ({ e }: { e: El }) => (
    <label className="lockbox"><input type="checkbox" checked={e.locked} disabled={busy} onChange={() => toggleLock(e)} /> 잠금</label>
  )
  const fontKey = (f: Record<string, any>) => JSON.stringify(f)

  return (
    <div className="editor">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <strong>편집 · {data.name} <span className="muted">{data.canvas[0]}×{data.canvas[1]}px</span></strong>
        <button onClick={onClose}>닫기</button>
      </div>
      <div className="editor-body">
        <div className="editor-preview">
          {preview ? <img src={preview} alt="편집 미리보기" /> : <p className="muted">미리보기를 만드는 중…</p>}
          {warns.map((w, i) => <div key={i} className="badge b-warn">{w}</div>)}
        </div>

        <div className="editor-controls">
          {els.map((e) => (
            <fieldset key={e.id} disabled={busy} className={e.locked ? 'locked' : ''}>
              <legend>{e.role}</legend>
              <Lock e={e} />
              {e.locked && <span className="muted"> 잠겨 있어 수정할 수 없습니다. 수정하려면 잠금을 해제하세요.</span>}

              {e.type === 'text' && (
                <div className="ctl" aria-disabled={e.locked}>
                  <textarea rows={2} disabled={e.locked} value={e.props.text} onChange={(x) => patch(e.id, { text: x.target.value })} />
                  <div className="row">
                    <label>글꼴 <select disabled={e.locked} value={fontKey(e.props.font)}
                      onChange={(x) => patch(e.id, { font: JSON.parse(x.target.value) })}>
                      {data.fonts.map((f) => <option key={fontKey(f.ref)} value={fontKey(f.ref)}>{f.label}</option>)}
                    </select></label>
                    <label>크기 <input type="number" disabled={e.locked} min={8} max={400} value={e.props.font_size}
                      onChange={(x) => patch(e.id, { font_size: num(x.target.value) })} /></label>
                    <label>정렬 <select disabled={e.locked} value={e.props.align} onChange={(x) => patch(e.id, { align: x.target.value })}>
                      <option>왼쪽</option><option>가운데</option><option>오른쪽</option></select></label>
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
                  {e.props.text_override && <span className="muted">이 지면에서 따로 수정한 문구입니다(공통 변경에서 보호됨)</span>}
                  <div><button disabled={e.locked} onClick={() => regen('text')}>문구 다시 생성</button></div>
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
                  <label>확대 {e.props.zoom.toFixed(2)}배 <input type="range" min={1} max={3} step={0.05} disabled={e.locked}
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
        <button disabled={busy} onClick={reset}>처음 상태로 되돌리기</button>
        <span className="muted">실행 취소(Undo)는 제공하지 않습니다. 저장 전에는 미리보기만 바뀝니다.</span>
      </div>
    </div>
  )
}

function Pos({ e, patch, nudge, wLabel }: {
  e: El; patch: (id: number, p: Record<string, any>) => void; nudge: (e: El, dx: number, dy: number) => void; wLabel: string
}) {
  const step = 10
  return (
    <div className="row">
      <label>X <input type="number" disabled={e.locked} value={e.props.x} onChange={(x) => patch(e.id, { x: num(x.target.value) })} /></label>
      <label>Y <input type="number" disabled={e.locked} value={e.props.y} onChange={(x) => patch(e.id, { y: num(x.target.value) })} /></label>
      <label>{wLabel} <input type="number" disabled={e.locked} value={e.props.w} onChange={(x) => patch(e.id, { w: num(x.target.value) })} /></label>
      <span className="nudge">
        <button type="button" disabled={e.locked} onClick={() => nudge(e, -step, 0)} aria-label="왼쪽으로">←</button>
        <button type="button" disabled={e.locked} onClick={() => nudge(e, 0, -step)} aria-label="위로">↑</button>
        <button type="button" disabled={e.locked} onClick={() => nudge(e, 0, step)} aria-label="아래로">↓</button>
        <button type="button" disabled={e.locked} onClick={() => nudge(e, step, 0)} aria-label="오른쪽으로">→</button>
      </span>
    </div>
  )
}
