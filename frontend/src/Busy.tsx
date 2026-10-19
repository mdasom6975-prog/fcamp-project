import { useEffect, useState, useSyncExternalStore } from 'react'

/* 오래 걸리는(과금) 작업 동안 화면 전체를 막는 덮개.
   - busy.run(라벨, 작업) 으로 감싸면 작업이 끝날 때까지 다른 버튼·탭을 누를 수 없고, 창을 닫거나 새로고침하면 확인창이 뜬다.
   - 카피 생성·배경/이미지 생성·아이콘 생성/채우기/다시 그리기·편집 재생성에 쓴다. */

type State = { label: string; since: number } | null
let state: State = null
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((l) => l())

export const busy = {
  start(label: string) { state = { label, since: Date.now() }; emit() },
  end() { state = null; emit() },
  async run<T>(label: string, fn: () => Promise<T>): Promise<T> {
    busy.start(label)
    try { return await fn() } finally { busy.end() }
  },
}

// 개발 중 화면 확인용 (프로덕션 빌드에서는 노출하지 않는다)
if (import.meta.env.DEV) (window as unknown as { __busy?: typeof busy }).__busy = busy

const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }
const snapshot = () => state

export default function BusyOverlay() {
  const s = useSyncExternalStore(subscribe, snapshot, snapshot)
  const [, tick] = useState(0)

  // 경과 시간 표시용 1초 타이머 + 페이지 이탈 경고
  useEffect(() => {
    if (!s) return
    const t = window.setInterval(() => tick((v) => v + 1), 1000)
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => { window.clearInterval(t); window.removeEventListener('beforeunload', warn) }
  }, [s])

  if (!s) return null
  const sec = Math.floor((Date.now() - s.since) / 1000)
  const mmss = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
  return (
    <div className="busy-overlay" role="alertdialog" aria-modal="true" aria-live="polite"
      onKeyDown={(e) => e.stopPropagation()}>
      <div className="busy-box">
        <div className="busy-spinner" aria-hidden="true" />
        <div className="busy-label">{s.label}</div>
        <div className="muted">{mmss} 경과 · 완료될 때까지 기다려 주세요</div>
        <div className="muted busy-note">이 창을 닫거나 다른 화면으로 이동하면 진행 중인 결과를 잃을 수 있습니다.</div>
      </div>
    </div>
  )
}
