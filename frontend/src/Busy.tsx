import { useEffect, useState, useSyncExternalStore } from 'react'

/* 오래 걸리는(과금) 작업 동안 화면 전체를 막는 덮개.
   - busy.run(라벨, 작업) 으로 감싸면 작업이 끝날 때까지 다른 버튼·탭을 누를 수 없고, 창을 닫거나 새로고침하면 확인창이 뜬다.
   - 카피 생성·배경/이미지 생성·아이콘 생성/채우기/다시 그리기·편집 재생성에 쓴다.
   - opts.sketch 를 주면(아이콘 렌더) 그릴 기능명을 연필 스케치 카드로 깔아 둔다. 진행률이 아니라 '무엇을 그리는 중인지'만 보여 준다.
   - setQuizSource 로 퀴즈 문항을 가져오는 함수를 걸어 두면(시안 화면) 덮개를 띄울 때마다 새로 받아 브랜드 퀴즈 카드를 보여 준다.
     문항은 서버가 브랜드 규칙·표현 사전으로 만든다(GET /api/designs/{id}/quiz, 모델 호출 없음).
   - 작업이 끝났을 때 다른 탭에 가 있으면 탭 제목에 완료/실패를 붙이고, 알림을 허용했다면 브라우저 알림을 띄운다. */

export type SketchCard = { name: string; concept?: string }
export type QuizItem = { kind: string; question: string; options: { label: string; color?: string }[]; answer: number; explain: string }
type State = { label: string; since: number; sketch?: SketchCard[]; quiz?: QuizItem[] } | null
let state: State = null
let quizSource: (() => Promise<QuizItem[]>) | null = null
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((l) => l())

// 다른 탭에 있는 동안 끝나면 탭 제목으로 알린다. 탭으로 돌아오면 원래 제목으로 되돌린다
let baseTitle: string | null = null
function restoreTitle() {
  if (baseTitle !== null && !document.hidden) { document.title = baseTitle; baseTitle = null }
}
document.addEventListener('visibilitychange', restoreTitle)

function announce(label: string, ok: boolean) {
  if (!document.hidden) return
  const what = label.split('…')[0].trim() || '작업'
  if (baseTitle === null) baseTitle = document.title
  document.title = `${ok ? '✓ 완료' : '✕ 실패'} · ${baseTitle}`
  if ('Notification' in window && Notification.permission === 'granted') {
    try { new Notification(ok ? '작업이 끝났어요' : '작업이 실패했어요', { body: `${what} ${ok ? '— 결과를 확인해 보세요' : '— 화면에서 오류를 확인해 주세요'}` }) } catch { /* 알림을 못 띄우는 환경은 탭 제목만 */ }
  }
}

export const busy = {
  start(label: string, opts: { sketch?: SketchCard[] } = {}) {
    const mine: State = { label, since: Date.now(), sketch: opts.sketch }
    state = mine; emit()
    // 퀴즈는 덮개를 띄운 뒤 받아 붙인다(못 받으면 퀴즈 없이). 그 사이 작업이 끝났으면 버린다
    quizSource?.().then((quiz) => { if (state === mine && quiz.length) { state = { ...mine, quiz }; emit() } }).catch(() => undefined)
  },
  setQuizSource(fn: (() => Promise<QuizItem[]>) | null) { quizSource = fn },
  end() { state = null; emit() },
  async run<T>(label: string, fn: () => Promise<T>, opts: { sketch?: SketchCard[] } = {}): Promise<T> {
    busy.start(label, opts)
    let ok = false
    try { const r = await fn(); ok = true; return r } finally { busy.end(); announce(label, ok) }
  },
}

// 개발 중 화면 확인용 (프로덕션 빌드에서는 노출하지 않는다)
if (import.meta.env.DEV) (window as unknown as { __busy?: typeof busy }).__busy = busy

const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }
const snapshot = () => state

// 연필이 지나가는 듯한 낙서 선. 한 번에 카드 하나만 SKETCH_STEP 초 동안 그려지고(테두리 진하게), 그려진 선은 옅게 남는다.
// 카드 수에 따라 한 바퀴 길이가 달라 키프레임은 렌더할 때 만든다. 장식이며 진행률이 아니다
const SCRIBBLES = [
  'M8 40 C 20 10, 40 10, 50 30 S 75 55, 88 22',
  'M10 30 Q 30 5, 48 28 T 86 30 M20 46 L 80 46',
  'M14 44 C 14 14, 50 6, 50 30 C 50 50, 84 48, 84 18',
  'M10 20 L 30 44 L 50 18 L 70 44 L 88 20',
]

const SKETCH_STEP = 1.6     // 카드 한 장에 머무는 시간(초)

function SketchBoard({ cards }: { cards: SketchCard[] }) {
  const cycle = SKETCH_STEP * cards.length
  const frac = 100 / cards.length          // 한 바퀴 중 카드 한 장의 차례(%)
  const keyframes = `
@keyframes sketch-draw { 0% { stroke-dashoffset: 160; opacity: 1; } ${frac * 0.8}% { stroke-dashoffset: 0; opacity: 1; } ${frac}%, 100% { stroke-dashoffset: 0; opacity: .35; } }
@keyframes sketch-turn { 0%, ${frac}% { border-color: var(--accent); border-style: solid; } ${frac + 0.01}%, 100% { border-color: var(--line-strong); border-style: dashed; } }`
  return (
    <div className="busy-sketch" aria-label="그리는 중인 아이콘" style={{ ['--sketch-cycle' as string]: `${cycle}s` }}>
      <style>{keyframes}</style>
      {cards.map((c, i) => {
        // 음수 지연: 첫 카드부터 차례가 오고, 아직 차례가 안 온 카드도 처음부터 옅게 그려진 상태로 보인다
        const delay = { animationDelay: `${i * SKETCH_STEP - cycle}s` }
        return (
        <div key={`${c.name}-${i}`} className="sketch-card" style={delay}>
          <svg viewBox="0 0 96 56" aria-hidden="true">
            <path d={SCRIBBLES[i % SCRIBBLES.length]} style={delay} />
          </svg>
          <div className="sketch-name">{c.name}</div>
          {c.concept && <div className="sketch-concept">{c.concept}</div>}
        </div>
        )
      })}
    </div>
  )
}

// 브랜드 퀴즈 카드: 한 문제씩. 보기를 누르면 정답·해설을 보여 주고 '다음 문제'로 넘어간다(끝나면 처음부터)
function QuizCard({ items }: { items: QuizItem[] }) {
  const [idx, setIdx] = useState(0)
  const [picked, setPicked] = useState<number | null>(null)
  const [score, setScore] = useState({ right: 0, done: 0 })
  const q = items[idx % items.length]
  const pick = (i: number) => {
    if (picked !== null) return
    setPicked(i)
    setScore((s) => ({ right: s.right + (i === q.answer ? 1 : 0), done: s.done + 1 }))
  }
  const next = () => { setPicked(null); setIdx((v) => v + 1) }
  return (
    <div className="busy-quiz">
      <div className="quiz-head">
        <span className="quiz-tag">기다리는 동안 퀴즈</span>
        {score.done > 0 && <span className="muted">맞힌 문제 {score.right}/{score.done}</span>}
      </div>
      <div className="quiz-q">{q.question}</div>
      <div className={q.kind === 'color' ? 'quiz-opts colors' : 'quiz-opts'}>
        {q.options.map((o, i) => {
          const cls = picked === null ? '' : i === q.answer ? ' right' : i === picked ? ' wrong' : ' dim'
          return (
            <button key={`${idx}-${i}`} type="button" className={`quiz-opt${cls}`} disabled={picked !== null} onClick={() => pick(i)}>
              {o.color && <span className="quiz-chip" style={{ background: o.color }} aria-hidden="true" />}
              {o.label}
            </button>
          )
        })}
      </div>
      {picked !== null && (
        <div className="quiz-explain">
          <strong>{picked === q.answer ? '정답이에요!' : '아쉬워요.'}</strong> {q.explain}
          <button type="button" className="quiz-next" onClick={next}>다음 문제</button>
        </div>
      )}
    </div>
  )
}

export default function BusyOverlay() {
  const s = useSyncExternalStore(subscribe, snapshot, snapshot)
  const [, tick] = useState(0)
  const canNotify = typeof window !== 'undefined' && 'Notification' in window
  const [perm, setPerm] = useState(canNotify ? Notification.permission : 'denied')

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
  const sketch = s.sketch && s.sketch.length > 0 ? s.sketch : null
  const quiz = s.quiz && s.quiz.length > 0 ? s.quiz : null
  return (
    <div className="busy-overlay" role="alertdialog" aria-modal="true" aria-live="polite"
      onKeyDown={(e) => e.stopPropagation()}>
      <div className={sketch || quiz ? 'busy-box wide' : 'busy-box'}>
        <div className="busy-spinner" aria-hidden="true" />
        <div className="busy-label">{s.label}</div>
        <div className="muted">{mmss} 경과 · 완료될 때까지 기다려 주세요</div>
        {sketch && <SketchBoard cards={sketch} />}
        {quiz && <QuizCard key={s.since} items={quiz} />}
        {canNotify && perm === 'default' && (
          <button type="button" className="busy-notify"
            onClick={() => Notification.requestPermission().then(setPerm).catch(() => undefined)}>끝나면 알림 받기</button>
        )}
        {perm === 'granted' && <div className="muted busy-note">끝나면 브라우저 알림으로 알려 드려요.</div>}
        <div className="muted busy-note">다른 탭으로 옮겨도 괜찮아요(끝나면 탭 제목에 표시됩니다). 이 창을 닫거나 새로고침하면 진행 중인 결과를 잃을 수 있습니다.</div>
      </div>
    </div>
  )
}
