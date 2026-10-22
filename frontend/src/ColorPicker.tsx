import { useEffect, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'

type HSV = { h: number; s: number; v: number }

const HEX_RE = /^#[0-9a-fA-F]{6}$/

function hexToHsv(hex: string): HSV {
  const r = parseInt(hex.slice(1, 3), 16) / 255
  const g = parseInt(hex.slice(3, 5), 16) / 255
  const b = parseInt(hex.slice(5, 7), 16) / 255
  const max = Math.max(r, g, b)
  const d = max - Math.min(r, g, b)
  let h = 0
  if (d) {
    if (max === r) h = ((g - b) / d) % 6
    else if (max === g) h = (b - r) / d + 2
    else h = (r - g) / d + 4
    h *= 60
    if (h < 0) h += 360
  }
  return { h, s: max ? d / max : 0, v: max }
}

function hsvToHex({ h, s, v }: HSV): string {
  const f = (n: number) => {
    const k = (n + h / 60) % 6
    return Math.round((v - v * s * Math.max(0, Math.min(k, 4 - k, 1))) * 255)
  }
  return '#' + [f(5), f(3), f(1)].map((x) => x.toString(16).padStart(2, '0')).join('').toUpperCase()
}

// 드래그 가능한 영역에서 0~1 범위의 상대 좌표를 얻는다
function useDrag(onMove: (x: number, y: number) => void) {
  const ref = useRef<HTMLDivElement>(null)
  const move = (e: ReactPointerEvent) => {
    const r = ref.current!.getBoundingClientRect()
    onMove(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)))
  }
  return {
    ref,
    onPointerDown: (e: ReactPointerEvent) => { e.currentTarget.setPointerCapture(e.pointerId); move(e) },
    onPointerMove: (e: ReactPointerEvent) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) move(e) },
  }
}

type EyeDropperCtor = new () => { open: () => Promise<{ sRGBHex: string }> }

export default function ColorPicker({ value, onChange, label, swatches = [] }: {
  value: string
  onChange: (hex: string) => void
  label: string
  swatches?: string[]   // 같은 브랜드에서 이미 쓰는 색
}) {
  const valid = HEX_RE.test(value) ? value.toUpperCase() : '#000000'
  const [open, setOpen] = useState(false)
  const [hsv, setHsv] = useState<HSV>(() => hexToHsv(valid))
  const [text, setText] = useState(valid)
  const box = useRef<HTMLDivElement>(null)
  const EyeDropper = (window as unknown as { EyeDropper?: EyeDropperCtor }).EyeDropper

  // 바깥에서 값이 바뀌면(HEX 직접 입력 등) 피커 상태를 맞춘다. 색조는 회색·검정에서 잃지 않도록 같은 색이면 유지
  useEffect(() => {
    setHsv((cur) => (hsvToHex(cur) === valid ? cur : hexToHsv(valid)))
    setText(valid)
  }, [valid])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [open])

  const apply = (next: HSV) => { setHsv(next); onChange(hsvToHex(next)) }
  const sv = useDrag((x, y) => apply({ ...hsv, s: x, v: 1 - y }))
  const hue = useDrag((x) => apply({ ...hsv, h: x * 360 }))
  const commitText = (t: string) => {
    setText(t)
    const hex = (t.startsWith('#') ? t : '#' + t).toUpperCase()
    if (HEX_RE.test(hex)) onChange(hex)
  }
  const pickScreen = async () => {
    try { onChange((await new EyeDropper!().open()).sRGBHex.toUpperCase()) } catch { /* 취소 */ }
  }
  const uniq = [...new Set(swatches.map((s) => s.toUpperCase()).filter((s) => HEX_RE.test(s)))]

  return (
    <div className="cp" ref={box}>
      <button type="button" className="cp-swatch" aria-label={label} aria-expanded={open}
        style={{ background: valid }} onClick={() => setOpen(!open)} />
      {open && (
        <div className="cp-pop" role="dialog" aria-label={label}>
          <div className="cp-sv" {...sv} style={{ backgroundColor: `hsl(${hsv.h} 100% 50%)` }}>
            <div className="cp-knob" style={{ left: `${hsv.s * 100}%`, top: `${(1 - hsv.v) * 100}%`, background: valid }} />
          </div>
          <div className="cp-hue" {...hue}>
            <div className="cp-knob" style={{ left: `${(hsv.h / 360) * 100}%`, top: '50%', background: `hsl(${hsv.h} 100% 50%)` }} />
          </div>
          <div className="cp-foot">
            <span className="cp-dot" style={{ background: valid }} />
            <input className="cp-hex" aria-label="색상 코드" value={text} maxLength={7} spellCheck={false}
              onChange={(e) => commitText(e.target.value)} onBlur={() => setText(valid)} />
            {EyeDropper && <button type="button" className="cp-eye" aria-label="화면에서 색 추출" onClick={pickScreen}>스포이드</button>}
          </div>
          {uniq.length > 0 && (
            <div className="cp-used">
              <div className="muted">사용 중인 색상</div>
              <div className="cp-swatches">
                {uniq.map((c) => (
                  <button type="button" key={c} className={'cp-swatch sm' + (c === valid ? ' on' : '')} aria-label={c}
                    style={{ background: c }} onClick={() => onChange(c)} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
