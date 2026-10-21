import { useCallback, useEffect, useRef, useState } from 'react'

// 개발자 지표: 이 시안이 부른 외부 모델 호출의 횟수·실패·소요·토큰·비용. 절감 효과를 화면에서 바로 비교하기 위한 것.
// 서버(GET /api/designs/{id}/api-calls)가 종류별로 집계하고, 비용은 backend/app/pricing.py 로 계산한다
// (토큰이 기록된 호출은 토큰×단가 = 실제 과금 방식, 토큰이 없는 예전 호출·요금표에 없는 모델은 기본 요율로 추정 → '*').
export type KindAgg = {
  count: number; failed: number; ms_total: number; ms_avg: number; tokens_in: number; tokens_out: number
  cost_usd: number; cost_krw: number | null; unpriced: number; estimated: boolean
}
export type CallRow = {
  ts: string; api: string; kind: string; provider: string; model: string; ok: boolean; ms: number | null
  size?: string; quality?: string; tokens_in?: number; tokens_out?: number; error?: string
  cost_usd: number | null; cost_krw: number | null; estimated: boolean
}
export type ApiCalls = {
  total: number; ok: number; failed: number; images: number; chats: number
  by_kind: Record<string, KindAgg>; cost_usd: number; cost_krw: number | null; estimated: boolean; unpriced: number
  tokens_in: number; tokens_out: number; ms_total: number; usd_krw: number; recent: CallRow[]
}

export const KIND_LABEL: Record<string, string> = {
  background: '배경 생성', variant: '배경 변형', icon: '아이콘 렌더', copy: '카피', copy_fit: '카피 지면 맞춤', icon_names: '아이콘 기능명', icon_redraw: '아이콘 재작성',
}
const API_LABEL: Record<string, string> = { 'images.generate': 'generate', 'images.edit': 'edit', chat: 'chat' }

const usd = (v: number | null | undefined) => (v == null ? '—' : `$${v.toFixed(v < 0.1 ? 4 : 3)}`)
const krw = (v: number | null | undefined) => (v == null ? '' : ` (약 ${v.toLocaleString()}원)`)
// 기록은 UTC ISO 문자열이므로 브라우저 현지 시간(한국이면 KST)으로 바꿔 HH:MM:SS 로 보여 준다
const hhmmss = (iso: string) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? iso.slice(11, 19) : [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':') }
const sec = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}초` : `${ms}ms`)
const tok = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))

function sum(agg: ApiCalls | null, kinds: string[]): KindAgg {
  const z: KindAgg = { count: 0, failed: 0, ms_total: 0, ms_avg: 0, tokens_in: 0, tokens_out: 0, cost_usd: 0, cost_krw: 0, unpriced: 0, estimated: false }
  if (!agg) return z
  for (const k of kinds) {
    const a = agg.by_kind[k]
    if (!a) continue
    z.count += a.count; z.failed += a.failed; z.ms_total += a.ms_total; z.tokens_in += a.tokens_in; z.tokens_out += a.tokens_out
    z.cost_usd += a.cost_usd; z.cost_krw = (z.cost_krw ?? 0) + (a.cost_krw ?? 0); z.unpriced += a.unpriced; z.estimated = z.estimated || a.estimated
  }
  z.ms_avg = z.count ? Math.round(z.ms_total / z.count) : 0
  z.cost_usd = Math.round(z.cost_usd * 10000) / 10000
  return z
}

export default function DevStats({ designId, kinds, label, refreshKey }: {
  designId: number
  kinds: string[]                 // 이 화면이 부르는 호출 종류 (KIND_LABEL 의 키)
  label: string                   // 화면 이름 (예: '카피 생성')
  refreshKey?: unknown            // 바뀌면 다시 불러와 '이번 작업' 증가분을 계산한다
}) {
  const [data, setData] = useState<ApiCalls | null>(null)
  const [delta, setDelta] = useState<KindAgg | null>(null)
  const [open, setOpen] = useState(false)
  const prev = useRef<KindAgg | null>(null)
  const kindsKey = kinds.join(',')

  const load = useCallback(() =>
    fetch(`/api/designs/${designId}/api-calls`).then((r) => r.json()).then((j: ApiCalls) => {
      if (!j || typeof j.total !== 'number') return
      const now = sum(j, kindsKey.split(','))
      if (prev.current && now.count + now.failed > prev.current.count + prev.current.failed) {
        const p = prev.current
        setDelta({ ...now, count: now.count - p.count, failed: now.failed - p.failed, ms_total: now.ms_total - p.ms_total,
          tokens_in: now.tokens_in - p.tokens_in, tokens_out: now.tokens_out - p.tokens_out,
          cost_usd: Math.round((now.cost_usd - p.cost_usd) * 10000) / 10000, cost_krw: (now.cost_krw ?? 0) - (p.cost_krw ?? 0),
          ms_avg: 0, unpriced: now.unpriced - p.unpriced })
      }
      prev.current = now
      setData(j)
    }).catch(() => undefined), [designId, kindsKey])
  useEffect(() => { load() }, [load, refreshKey])

  const t = sum(data, kinds)
  const chat = kinds.some((k) => ['copy', 'icon_names', 'icon_redraw'].includes(k))
  const est = t.estimated || t.unpriced > 0
  const rows = (data?.recent ?? []).filter((r) => kinds.includes(r.kind))
  return (
    <div className="devstats" title="개발자 지표: 이 시안의 외부 모델 호출. 비용은 기록된 토큰×단가(pricing.py)로 계산하며, *는 추정입니다">
      <div className="devstats-line">
        <span className="devstats-tag">개발자 지표</span>
        <span><strong>{label}</strong> 누적 호출 <strong>{t.count}</strong>회{t.failed ? ` (실패 ${t.failed})` : ''}</span>
        <span>· 비용 <strong>{usd(t.cost_usd)}</strong>{krw(t.cost_krw)}{est ? ' *' : ''}</span>
        {t.count > 0 && <span>· 평균 {sec(t.ms_avg)}/회, 합계 {sec(t.ms_total)}</span>}
        {chat && (t.tokens_in || t.tokens_out) ? <span>· 토큰 입력 {tok(t.tokens_in)} / 출력 {tok(t.tokens_out)}</span> : null}
        {delta && (
          <span className="devstats-delta">이번 작업 +{delta.count}회{delta.failed ? ` (실패 ${delta.failed})` : ''} · {usd(delta.cost_usd)}{krw(delta.cost_krw)} · {sec(delta.ms_total)}</span>
        )}
        <button type="button" className="linklike" onClick={() => setOpen((o) => !o)}>{open ? '내역 닫기' : '내역 보기'}</button>
      </div>
      {open && (
        <div className="devstats-detail">
          <div className="muted">
            종류별: {kinds.filter((k) => data?.by_kind[k]).map((k) => {
              const a = data!.by_kind[k]
              return `${KIND_LABEL[k] ?? k} ${a.count}회 ${usd(a.cost_usd)}${a.failed ? ` 실패 ${a.failed}` : ''}`
            }).join(' · ') || '아직 호출이 없습니다'}
            {' '}· 시안 전체 {data?.total ?? 0}회 {usd(data?.cost_usd)}{krw(data?.cost_krw)} · 환율 {data?.usd_krw ?? '-'}원/$
            {est && ' · * 토큰 기록이 없거나 요금표에 없는 호출은 기본 요율로 추정'}
          </div>
          {rows.length > 0 && (
            <table className="devstats-table">
              <thead><tr><th>시각</th><th>종류</th><th>API</th><th>모델</th><th>크기·품질 / 토큰</th><th>소요</th><th>비용</th><th>결과</th></tr></thead>
              <tbody>
                {rows.slice(0, 20).map((r, i) => (
                  <tr key={i} className={r.ok ? '' : 'bad'}>
                    <td title={r.ts}>{hhmmss(r.ts)}</td>
                    <td>{KIND_LABEL[r.kind] ?? r.kind}</td>
                    <td>{API_LABEL[r.api] ?? r.api}</td>
                    <td>{r.model}</td>
                    <td>{r.api === 'chat' ? `${tok(r.tokens_in ?? 0)} / ${tok(r.tokens_out ?? 0)}` : `${r.size ?? ''} ${r.quality ?? ''}`}</td>
                    <td>{r.ms != null ? sec(r.ms) : '-'}</td>
                    <td>{usd(r.cost_usd)}{r.estimated ? '*' : ''}</td>
                    <td>{r.ok ? '성공' : `실패 ${r.error ?? ''}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}
