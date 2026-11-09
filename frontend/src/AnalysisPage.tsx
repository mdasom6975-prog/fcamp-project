import { useCallback, useEffect, useMemo, useState } from 'react'
import { busy } from './Busy'
import { KIND_LABEL } from './DevStats'
import { inScope, type BrandScope } from './api'

/* 분석(학습·개발용) — 검수 화면과 별개로, 캠페인 하나에 대해 세 가지를 한 페이지에 본다.
   ① 브랜드 규칙·캠페인 입력이 어느 단계에서 반영되는지(표) + 생성 배경·아이콘의 주요 색과 브랜드 색의 거리(ΔE) + Claude 비전 채점(과금)
   ② 시안 생성(카피)·배경·아이콘 생성 전에 보낸 프롬프트 전문(기록 / DB 저장분 / 재구성)과 입력에서 온 값의 표시
   ③ 단계별 호출 수·토큰·추정 비용과 호출 내역 전체
   데이터는 GET /api/designs/{id}/analysis 하나로 온다 (backend/app/analysis.py). */

type Pal = { hex: string; share: number }
type Match = { per_color: { role: string; hex: string; nearest: string; delta_e: number; share: number }[]; coverage: number | null; coverage_roles: string[]; near_de: number }
type Bg = {
  id: number; file: string; width: number; height: number; template: string | null; style: string | null; kind: string
  reference: { background_id: number; template?: string } | null
  quality: string | null; provider: string | null; model: string | null; style_reason: string | null; created_at: string | null
  palette: Pal[]; match: Match; exists: boolean
}
type Composite = { preset: { id: number; platform: string; name: string; width: number | null; height: number | null } | null; id: number; file: string; width: number; height: number; layout: { edited?: boolean; crop?: { mode?: string } } }
type Icon = { id: number; name: string; concept: string; position: number; style: { model?: string; quality?: string; corner?: string; material?: string; colors?: string[] }; violations: unknown[]; palette: Pal[]; match: Match; exists: boolean }
type Source = 'recorded' | 'stored' | 'reconstructed' | 'note'
type Entry = {
  stage: 'copy' | 'background' | 'icon' | 'judge'; kind: string; source: Source; ts: string | null
  provider?: string | null; model?: string | null; ok?: boolean; error?: string | null; ms?: number | null; size?: string | null; quality?: string | null
  tokens_in?: number | null; tokens_out?: number | null; cost_usd?: number | null
  system?: string | null; user?: string | null; prompt?: string | null; refs?: string[]; note?: string
  image?: { id: number; file: string; template: string | null; style: string | null }; icon?: { id: number; name: string; concept: string }
}
type Agg = { count: number; failed: number; ms_total: number; ms_avg: number; tokens_in: number; tokens_out: number; cost_usd: number; cost_krw: number | null; unpriced: number; estimated: boolean }
type Row = { ts: string; api: string; kind: string; provider: string; model: string; ok: boolean; ms: number | null; size?: string; quality?: string; tokens_in?: number; tokens_out?: number; error?: string; cost_usd: number | null; cost_krw: number | null; estimated: boolean; prompt_id?: string; served_by?: string }
type Judgment = { ts: string; background_id: number | null; file: string; template: string | null; style: string; model: string; scores: { item: string; score: number; reason: string }[]; overall: number; summary: string; cost_usd: number | null; tokens_in: number | null; tokens_out: number | null }
type Mapping = { key: string; item: string; value: string; stages: Record<'copy' | 'background' | 'icon' | 'compose' | 'review', string | null> }
type KChunk = { chunk_id: number; source_id: string; doc_title: string; layer: string; section: string; text: string; score: number | null; product_name: string | null; functional: string[] }
type Knowledge = { industries: string[]; law: KChunk[]; guide: KChunk[]; error: string | null; functional_mismatch: string | null; functional: string[] | null; embed_model: string | null; product_note: string | null; motifs: string | null; directions: string[]; variants: { variant_no: number; angle: string; evidence: string }[] }
type Analysis = {
  design: { id: number; kind: string; status: string; created_at: string; brief: Record<string, unknown>; presets: { id: number; platform: string; name: string; width: number | null; height: number | null }[]; settings: { model: string; bg_mode: string }; style: string; style_reason: string }
  brand: { id: number; name: string; description: string; version_no: number; colors: { role: string; hex: string }[]; fonts: { role: string; name?: string | null }[]; icon_style: { corner?: string; material?: string; stroke_width?: number; fill?: string }; image_tone: { description?: string }; voice: { description?: string; headline_max_chars?: number | null; polite?: boolean }; levels: Record<string, string>; logo_position: string | null; logo_margin: { percent?: number } | null; effective: { required_phrases: string[]; forbidden_phrases: string[]; required_source: string; forbidden_source: string } }
  mapping: Mapping[]
  results: { backgrounds: Bg[]; composites: Composite[]; icons: Icon[]; copy: { selected: { variant_no: number; headline: string; body: string; cta: string } | null; variants: { variant_no: number; headline: string; body: string; cta: string; selected: boolean }[]; text_assets: Record<string, string[]> } }
  prompts: Entry[]
  knowledge: Knowledge | null
  variables: Record<string, string>
  cost: { summary: { total: number; ok: number; failed: number; cost_usd: number; cost_krw: number | null; estimated: boolean; unpriced: number; usd_krw: number; tokens_in: number; tokens_out: number; ms_total: number }; stages: Record<string, Agg>; stage_label: Record<string, string>; rows: Row[] }
  judgments: Judgment[]; judge_items: string[]; judge_estimate: { usd: number | null; krw: number | null; model: string }
}
type BrandLite = { id: number; name: string; group_id: number | null }
type DesignLite = { id: number; kind: string; status: string; brief: { product_name?: string }; brand: { name: string; version_no: number }; created_at: string }

const STAGE_LABEL: Record<Entry['stage'], string> = { copy: '시안 생성(카피)', background: '배경 이미지 생성', icon: '아이콘 생성', judge: '비전 채점' }
const KIND: Record<string, string> = { ...KIND_LABEL, judge: '비전 채점' }
const SOURCE_LABEL: Record<Source, string> = { recorded: '보낸 전문(기록)', stored: '저장된 전문(DB)', reconstructed: '재구성', note: '안내' }
const SOURCE_HINT: Record<Source, string> = {
  recorded: '호출 시점에 실제로 보낸 문장을 그대로 기록한 것입니다',
  stored: '배경·아이콘 행에 저장된 프롬프트입니다. 전문 기록을 시작하기 전의 호출이라 기록 파일은 없지만 보낸 문장과 같습니다',
  reconstructed: '전문이 어디에도 남지 않은 호출을 현재 규칙·입력·프롬프트 파일로 다시 조립한 것입니다. 보낸 당시와 다를 수 있습니다',
  note: '호출 자체가 없었음을 설명합니다',
}
const COL: { key: keyof Mapping['stages']; label: string; hint: string }[] = [
  { key: 'copy', label: '카피 프롬프트', hint: 'prompts/copy/*.md — 시안 생성 호출' },
  { key: 'background', label: '배경 프롬프트', hint: 'prompts/image/*.md — 배경 생성 호출' },
  { key: 'icon', label: '아이콘 프롬프트', hint: 'prompts/icons/*.md — 기능명 제안·렌더 호출' },
  { key: 'compose', label: '합성(코드)', hint: 'compose.py — 모델 호출 없이 코드로 반영' },
  { key: 'review', label: '검수(코드)', hint: 'review.py — 결과를 검사' },
]

const usd = (v: number | null | undefined) => (v == null ? '—' : `$${v.toFixed(v < 0.1 ? 4 : 3)}`)
const krw = (v: number | null | undefined) => (v == null ? '' : ` (약 ${v.toLocaleString()}원)`)
const tok = (n: number | null | undefined) => (n == null ? '-' : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))
const sec = (ms: number | null | undefined) => (ms == null ? '-' : ms >= 1000 ? `${(ms / 1000).toFixed(1)}초` : `${ms}ms`)
const when = (iso: string | null | undefined) => {
  if (!iso) return '시각 없음'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const deClass = (de: number, near: number) => (de <= near / 2 ? 'de-ok' : de <= near ? 'de-near' : 'de-far')
const deLabel = (de: number, near: number) => (de <= near / 2 ? '거의 같은 색' : de <= near ? '같은 계열' : '다른 색')
const thumb = (id: number, file: string) => `/api/generated/${id}/thumb?v=${file}`
const full = (id: number, file: string) => `/api/generated/${id}/file?v=${file}`

/* 프롬프트 전문에서 '입력에서 온 값'을 표시한다. 긴 값부터 찾아 겹침을 줄이고, 나머지 글은 그대로 둔다. */
function Highlight({ text, vars }: { text: string; vars: Record<string, string> }) {
  const parts = useMemo(() => {
    const vals = Object.entries(vars).filter(([, v]) => v.length >= 2).sort((a, b) => b[1].length - a[1].length)
    const out: { t: string; k?: string }[] = []
    let rest = text
    while (rest.length) {
      let best: { i: number; k: string; v: string } | null = null
      for (const [k, v] of vals) {
        const i = rest.indexOf(v)
        if (i >= 0 && (!best || i < best.i)) best = { i, k, v }
      }
      if (!best) { out.push({ t: rest }); break }
      if (best.i > 0) out.push({ t: rest.slice(0, best.i) })
      out.push({ t: best.v, k: best.k })
      rest = rest.slice(best.i + best.v.length)
    }
    return out
  }, [text, vars])
  return <>{parts.map((p, i) => (p.k ? <mark key={i} className="var" title={`입력 값: ${p.k}`}>{p.t}</mark> : <span key={i}>{p.t}</span>))}</>
}

function Swatch({ hex, title }: { hex: string; title?: string }) {
  return <span className="swatch" style={{ background: hex }} title={title ?? hex} />
}

function PaletteBar({ pal }: { pal: Pal[] }) {
  if (!pal.length) return <span className="muted">색을 뽑지 못했습니다</span>
  return (
    <div className="pal-bar" title="이미지 주요 색 (비율순)">
      {pal.map((p) => <span key={p.hex} style={{ background: p.hex, flex: p.share }} title={`${p.hex} ${pct(p.share)}`} />)}
    </div>
  )
}

function MatchTable({ m }: { m: Match }) {
  if (!m.per_color.length) return <p className="muted">브랜드 색이 없어 비교할 수 없습니다.</p>
  return (
    <table className="an-table tight">
      <thead><tr><th>브랜드 색</th><th>가장 가까운 이미지 색</th><th>ΔE</th><th>그 색의 비율</th></tr></thead>
      <tbody>
        {m.per_color.map((c) => (
          <tr key={c.role + c.hex}>
            <td><Swatch hex={c.hex} /> {c.role} <code>{c.hex}</code></td>
            <td><Swatch hex={c.nearest} /> <code>{c.nearest}</code></td>
            <td className={deClass(c.delta_e, m.near_de)}>{c.delta_e} <small>{deLabel(c.delta_e, m.near_de)}</small></td>
            <td>{pct(c.share)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Scores({ j, items }: { j: Judgment; items: string[] }) {
  const by = Object.fromEntries(j.scores.map((s) => [s.item, s]))
  return (
    <div className="judge-box">
      <div className="judge-head">
        <strong>종합 {j.overall}/5</strong> · {j.summary}
        <span className="muted"> · {when(j.ts)} · {j.model} · {usd(j.cost_usd)}{j.tokens_in != null ? ` · 토큰 ${tok(j.tokens_in)}/${tok(j.tokens_out)}` : ''}</span>
      </div>
      <ul className="judge-list">
        {items.map((it) => {
          const s = by[it]
          return <li key={it}><span className={`score s${s?.score ?? 0}`}>{s?.score ?? '-'}</span> <strong>{it}</strong> <span className="muted">{s?.reason ?? '응답에 없음'}</span></li>
        })}
      </ul>
    </div>
  )
}

type Archived = { id: number; kind: string; preset: string; width: number; height: number; created_at: string; prompt: string | null
  model?: string | null; quality?: string | null; style?: string | null; mode?: string | null; template?: string | null; provider?: string | null }
type ArchivedBatch = { batch: string; archived_at: string; items: Archived[] }

// ④ 초기화한 이미지 보관본: '시안 이미지 초기화'로 사용자 화면에서 지운 이미지. 파일은 보존되어 있어 품질 비교에 쓴다
function ArchivedSection({ designId }: { designId: number }) {
  const [batches, setBatches] = useState<ArchivedBatch[]>([])
  const [picked, setPicked] = useState<number[]>([])   // 비교할 보관본 id (최대 2장, 오래된 선택부터 밀려난다)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    setPicked([]); setErr(null)
    fetch(`/api/designs/${designId}/analysis/archived`).then(async (r) => {
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || '보관본을 불러오지 못했습니다')
      setBatches(j.batches ?? [])
    }).catch((e) => setErr((e as Error).message))
  }, [designId])
  const all = useMemo(() => batches.flatMap((b) => b.items.map((i) => ({ ...i, batch: b.batch, archived_at: b.archived_at }))), [batches])
  const sel = picked.map((id) => all.find((x) => x.id === id)).filter((x): x is NonNullable<typeof x> => !!x)
  const toggle = (id: number) => setPicked((p) => p.includes(id) ? p.filter((x) => x !== id) : [...p, id].slice(-2))
  const meta = (a: Archived) => [a.model, a.quality, a.style, a.mode, a.template].filter(Boolean).join(' · ') || '-'
  return (
    <section className="card">
      <header><h3><span className="step-no">④</span> 초기화한 이미지 보관본</h3><span className="muted">{all.length}장 · 사용자 화면에서는 지워졌고 분석에서만 보입니다 · 2장을 골라 나란히 비교</span></header>
      {err && <p className="an-err">{err}</p>}
      {!err && !all.length && <p className="muted">보관된 이미지가 없습니다. '시안 이미지 초기화'를 하면 그 시점의 이미지가 여기에 남습니다.</p>}
      {sel.length > 0 && (
        <div className="an-arch-compare">
          {sel.map((a) => (
            <figure key={a.id}>
              <img src={`/api/archived-images/${a.id}/file`} alt={`${a.preset} ${a.kind}`} />
              <figcaption><strong>{a.kind === 'background' ? '배경' : '합성'}</strong> · {a.preset} · {a.width}×{a.height}<br /><span className="muted">{meta(a)}</span></figcaption>
            </figure>
          ))}
          {sel.length === 1 && <p className="muted">비교할 이미지를 하나 더 고르세요.</p>}
        </div>
      )}
      {batches.map((b) => (
        <div key={b.batch} className="an-arch-batch">
          <h4>초기화 {when(b.archived_at)} <span className="muted">· {b.items.length}장</span></h4>
          <div className="an-arch-grid">
            {b.items.map((a) => (
              <label key={a.id} className={`an-arch-card${picked.includes(a.id) ? ' on' : ''}`}>
                <input type="checkbox" checked={picked.includes(a.id)} onChange={() => toggle(a.id)} />
                <img src={`/api/archived-images/${a.id}/thumb`} alt="" loading="lazy" />
                <span><strong>{a.kind === 'background' ? '배경' : '합성'}</strong> · {a.kind === 'background' ? a.template ?? '' : a.preset}</span>
                <span className="muted">{a.width}×{a.height} · {meta(a)}</span>
                {a.prompt && <details onClick={(e) => e.stopPropagation()}><summary>프롬프트</summary><pre className="prompt">{a.prompt}</pre></details>}
              </label>
            ))}
          </div>
        </div>
      ))}
    </section>
  )
}

export default function AnalysisPage({ scope, workBrandId }: { scope: BrandScope; workBrandId: number | null }) {
  const [brands, setBrands] = useState<BrandLite[]>([])
  const [brandId, setBrandId] = useState<number | null>(workBrandId)
  const [designs, setDesigns] = useState<DesignLite[]>([])
  const [designId, setDesignId] = useState<number | null>(null)
  const [data, setData] = useState<Analysis | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [stageFilter, setStageFilter] = useState<Entry['stage'] | 'all'>('all')
  const [judgeMsg, setJudgeMsg] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/brands').then((r) => r.json()).then((all: BrandLite[]) => {
      const b = inScope(all, scope)
      setBrands(b)
      if (brandId === null || !b.some((x) => x.id === brandId)) setBrandId(b[0]?.id ?? null)
    }).catch(() => setErr('브랜드 목록을 불러오지 못했습니다'))
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (brandId === null) return
    setDesigns([]); setDesignId(null); setData(null)
    fetch(`/api/designs?brand_id=${brandId}`).then((r) => r.json()).then((l: DesignLite[]) => {
      setDesigns(l)
      setDesignId(l[0]?.id ?? null)
    }).catch(() => setErr('캠페인 목록을 불러오지 못했습니다'))
  }, [brandId])

  const load = useCallback(() => {
    if (designId === null) return
    setLoading(true); setErr(null)
    fetch(`/api/designs/${designId}/analysis`).then(async (r) => {
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || '분석 데이터를 불러오지 못했습니다')
      setData(j as Analysis)
    }).catch((e) => setErr((e as Error).message)).finally(() => setLoading(false))
  }, [designId])
  useEffect(() => { setJudgeMsg(null); load() }, [load])

  const judge = async (bgId: number) => {
    if (!data) return
    setJudgeMsg(null)
    try {
      await busy.run('Claude 가 배경을 보고 채점하는 중… (채팅 1회 과금)', async () => {
        const r = await fetch(`/api/designs/${data.design.id}/analysis/judge`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ background_id: bgId }) })
        const j = await r.json().catch(() => ({}))
        if (!r.ok) throw new Error(j.detail || '채점에 실패했습니다')
      })
      load()
    } catch (e) {
      setJudgeMsg((e as Error).message)
    }
  }

  const entries = useMemo(() => (data?.prompts ?? []).filter((e) => stageFilter === 'all' || e.stage === stageFilter), [data, stageFilter])
  const stageCounts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const e of data?.prompts ?? []) c[e.stage] = (c[e.stage] ?? 0) + 1
    return c
  }, [data])

  return (
    <main className="page an-page">
      <h1>분석 <span className="muted an-sub">학습·개발용 — 검수와 무관</span></h1>
      <p className="muted an-lead">브랜드 입력이 결과에 어떻게 닿는지, 모델에 실제로 무엇을 보냈는지, 얼마가 들었는지를 캠페인 하나에 대해 한 화면에서 봅니다.</p>

      <div className="an-pick card">
        <label>브랜드
          <select value={brandId ?? ''} onChange={(e) => setBrandId(Number(e.target.value))}>
            {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </label>
        <label>캠페인
          <select value={designId ?? ''} onChange={(e) => setDesignId(Number(e.target.value))} disabled={!designs.length}>
            {designs.map((d) => <option key={d.id} value={d.id}>#{d.id} {d.brief.product_name || '(이름 없음)'} · 규칙 v{d.brand.version_no} · {d.status}</option>)}
            {!designs.length && <option value="">캠페인이 없습니다</option>}
          </select>
        </label>
        <button type="button" className="ghost" onClick={load} disabled={designId === null || loading}>{loading ? '불러오는 중…' : '다시 불러오기'}</button>
      </div>
      {err && <p className="an-err">{err}</p>}

      {data && (
        <>
          <div className="an-strip">
            <span>호출 <strong>{data.cost.summary.ok}</strong>회{data.cost.summary.failed ? ` (실패 ${data.cost.summary.failed})` : ''}</span>
            <span>추정 비용 <strong>{usd(data.cost.summary.cost_usd)}</strong>{krw(data.cost.summary.cost_krw)}</span>
            <span>배경 <strong>{data.results.backgrounds.length}</strong>장 · 합성 {data.results.composites.length} · 아이콘 {data.results.icons.length}</span>
            <span>카피 {data.results.copy.selected ? `시안 ${data.results.copy.selected.variant_no} 선택` : '미선택'}</span>
            <span>이미지 모델 {data.design.settings.model} · 배경 방식 {data.design.settings.bg_mode}</span>
          </div>

          {/* ① 입력 ↔ 결과 */}
          <section className="card">
            <header><h3><span className="step-no">①</span> 브랜드 입력 ↔ 생성 결과</h3><span className="muted">입력이 어느 단계에서 쓰이는지 · 색 거리(ΔE) · 비전 채점</span></header>
            <div className="an-grid2">
              <div className="an-col">
                <h4>입력</h4>
                <dl className="an-kv">
                  <dt>브랜드</dt><dd>{data.brand.name} <span className="muted">규칙 v{data.brand.version_no}</span>{data.brand.description && <div className="muted">{data.brand.description}</div>}</dd>
                  <dt>허용 색상 <small className="muted">{data.brand.levels.colors}</small></dt>
                  <dd className="an-colors">{data.brand.colors.map((c) => <span key={c.role + c.hex}><Swatch hex={c.hex} /> {c.role} <code>{c.hex}</code></span>)}{!data.brand.colors.length && <span className="muted">없음</span>}</dd>
                  <dt>이미지 톤 <small className="muted">{data.brand.levels.image_tone}</small></dt><dd className="an-long">{data.brand.image_tone.description || <span className="muted">없음</span>}</dd>
                  <dt>문구 어조 <small className="muted">{data.brand.levels.voice}</small></dt><dd>{data.brand.voice.description || <span className="muted">없음</span>}{data.brand.voice.polite ? ' · 존댓말' : ''}{data.brand.voice.headline_max_chars ? ` · 헤드라인 ${data.brand.voice.headline_max_chars}자` : ''}</dd>
                  <dt>아이콘 스타일 <small className="muted">{data.brand.levels.icon_style}</small></dt><dd>모서리 {data.brand.icon_style.corner ?? '둥근'} · 재질 {data.brand.icon_style.material ?? '자동'}</dd>
                  <dt>폰트 <small className="muted">{data.brand.levels.fonts}</small></dt><dd>{data.brand.fonts.map((f) => `${f.role} ${f.name ?? '업로드'}`).join(', ') || <span className="muted">없음</span>}</dd>
                  <dt>로고</dt><dd>자리 {data.brand.logo_position ?? '좌상단(기본)'} · 여백 {data.brand.logo_margin?.percent ?? '-'}%</dd>
                  <dt>필수 표현 <small className="muted">{data.brand.effective.required_source}</small></dt><dd>{data.brand.effective.required_phrases.join(', ') || <span className="muted">없음</span>}</dd>
                  <dt>금지 표현 <small className="muted">{data.brand.effective.forbidden_source}</small></dt><dd className="an-long">{data.brand.effective.forbidden_phrases.join(', ') || <span className="muted">없음</span>}</dd>
                </dl>
                <h4>캠페인 입력</h4>
                <dl className="an-kv">
                  <dt>상품/서비스</dt><dd>{String(data.design.brief.product_name ?? '')}</dd>
                  <dt>설명</dt><dd>{String(data.design.brief.description ?? '') || <span className="muted">없음</span>}</dd>
                  <dt>핵심 메시지</dt><dd>{String(data.design.brief.key_message ?? '') || <span className="muted">없음</span>}</dd>
                  <dt>타깃</dt><dd>{String(data.design.brief.target ?? '') || <span className="muted">없음</span>}</dd>
                  <dt>배경 형태</dt><dd>{data.design.style} <span className="muted">({data.design.style_reason})</span></dd>
                  <dt>지면</dt><dd>{data.design.presets.map((p) => `${p.platform} ${p.name}`).join(' · ') || <span className="muted">없음</span>}</dd>
                </dl>
              </div>
              <div className="an-col">
                <h4>결과</h4>
                {!data.results.backgrounds.length && <p className="muted">아직 생성한 배경이 없습니다.</p>}
                {data.results.backgrounds.map((bg) => {
                  const js = data.judgments.filter((j) => j.background_id === bg.id || (j.background_id == null && j.file === bg.file))   // 자동 채점(소상공인)은 파일 이름으로 짝짓는다
                  return (
                    <div key={bg.id} className="an-bg">
                      <div className="an-bg-row">
                        {bg.exists ? <a href={full(bg.id, bg.file)} target="_blank" rel="noreferrer"><img className="an-thumb" src={thumb(bg.id, bg.file)} alt={`배경 ${bg.id}`} loading="lazy" /></a>
                          : <div className="an-thumb missing muted">파일 없음</div>}
                        <div className="an-bg-meta">
                          <div><strong>배경 #{bg.id}</strong> · {KIND[bg.kind] ?? bg.kind} · {bg.template} {bg.width}×{bg.height} · {bg.style}</div>
                          <div className="muted">{bg.model ?? bg.provider} · 품질 {bg.quality ?? '-'} · {when(bg.created_at)}{bg.reference ? ` · 참조 #${bg.reference.background_id}` : ''}</div>
                          <PaletteBar pal={bg.palette} />
                          <div>{(bg.match.coverage_roles ?? []).join('·') || '브랜드 색'}과 가까운 면적 <strong>{pct(bg.match.coverage)}</strong> <span className="muted">(ΔE ≤ {bg.match.near_de})</span></div>
                        </div>
                      </div>
                      <MatchTable m={bg.match} />
                      <div className="an-judge-line">
                        <button type="button" onClick={() => judge(bg.id)} disabled={!bg.exists}>Claude 비전 채점</button>
                        <span className="muted">{data.judge_estimate.model} · 1회 약 {usd(data.judge_estimate.usd)}{krw(data.judge_estimate.krw)} 과금 · 참고값(검수 아님)</span>
                      </div>
                      {js.map((j) => <Scores key={j.ts} j={j} items={data.judge_items} />)}
                    </div>
                  )
                })}
                {judgeMsg && <p className="an-err">{judgeMsg}</p>}
                <p className="muted an-note">배경 프롬프트는 강조색을 <em>포인트(화면의 15% 이내)</em>로만 쓰고 큰 면은 브랜드 배경색으로 두게 하므로, 배경의 브랜드 색 면적은 낮게 나올 수 있습니다. 작은 포인트 색은 주요 색 6개로 줄이는 과정에서 주변 색과 섞여 잡히지 않기도 합니다. 브랜드 색을 그대로 칠하는 것은 아이콘 프롬프트와 합성 단계(제목·CTA·스크림)입니다.</p>

                {data.results.icons.length > 0 && (
                  <>
                    <h4>아이콘 세트 <span className="muted">(색 지시 포함)</span></h4>
                    <div className="an-icons">
                      {data.results.icons.map((ic) => (
                        <div key={ic.id} className="an-icon" title={`${ic.concept}\n모델 ${ic.style.model ?? '-'} · ${ic.style.quality ?? ''}`}>
                          <img src={`/api/icons/${ic.id}/png`} alt={ic.name} loading="lazy" />
                          <div className="an-icon-name">{ic.name}</div>
                          <PaletteBar pal={ic.palette} />
                          <div className={`an-icon-cov ${ic.match.coverage != null && ic.match.coverage >= 0.7 ? 'de-ok' : ic.match.coverage != null && ic.match.coverage >= 0.4 ? 'de-near' : 'de-far'}`}>{pct(ic.match.coverage)}</div>
                        </div>
                      ))}
                    </div>
                    <p className="muted an-note">숫자는 아이콘 면적 중 브랜드 주조·보조·강조색(ΔE ≤ 20)에 가까운 비율. 명암으로 표현된 부분은 멀어질 수 있습니다.</p>
                  </>
                )}

                {data.results.composites.length > 0 && (
                  <>
                    <h4>지면 합성본</h4>
                    <div className="an-comps">
                      {data.results.composites.map((c) => (
                        <a key={c.id} href={full(c.id, c.file)} target="_blank" rel="noreferrer" className="an-comp" title={`${c.preset?.platform ?? ''} ${c.preset?.name ?? ''} ${c.width}×${c.height}${c.layout.edited ? ' · 편집됨' : ''}`}>
                          <img src={thumb(c.id, c.file)} alt={c.preset?.name ?? `합성 ${c.id}`} loading="lazy" />
                          <div className="muted">{c.preset?.name ?? '-'}</div>
                        </a>
                      ))}
                    </div>
                  </>
                )}

                <h4>카피</h4>
                {data.results.copy.variants.length ? (
                  <ul className="an-copy">
                    {data.results.copy.variants.map((v) => (
                      <li key={v.variant_no} className={v.selected ? 'on' : ''}>
                        <strong>시안 {v.variant_no}{v.selected ? ' (선택)' : ''}</strong> {v.headline} <span className="muted">— {v.body} / {v.cta}</span>
                      </li>
                    ))}
                  </ul>
                ) : <p className="muted">생성한 카피가 없습니다.</p>}
              </div>
            </div>

            <h4>입력 항목이 반영되는 단계</h4>
            <table className="an-table an-map">
              <thead>
                <tr><th>입력 항목</th><th>값</th>{COL.map((c) => <th key={c.key} title={c.hint}>{c.label}</th>)}</tr>
              </thead>
              <tbody>
                {data.mapping.map((m) => (
                  <tr key={m.key}>
                    <td><strong>{m.item}</strong></td>
                    <td className="an-map-val" title={m.value}>{m.value}</td>
                    {COL.map((c) => <td key={c.key} className={m.stages[c.key] ? 'yes' : 'no'}>{m.stages[c.key] ?? '—'}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted an-note">'—' 는 그 단계에 들어가지 않는다는 뜻입니다. 예: 폰트는 배경 프롬프트에 없고 합성 코드에서만 쓰입니다. 허용 색상은 배경 프롬프트에 포인트 색·큰 면의 색으로 들어가고, 합성 단계에서 글자·버튼 색으로 다시 쓰입니다.</p>
          </section>

          {/* 참고한 자료(RAG) */}
          {data.knowledge && <KnowledgeCard k={data.knowledge} />}

          {/* ② 프롬프트 */}
          <section className="card">
            <header><h3><span className="step-no">②</span> 모델에 보낸 프롬프트</h3><span className="muted">{data.prompts.length}건 · 노란 표시 = 입력에서 온 값</span></header>
            <div className="an-chips">
              <button type="button" className={stageFilter === 'all' ? 'on' : ''} onClick={() => setStageFilter('all')}>전체 {data.prompts.length}</button>
              {(['copy', 'background', 'icon', 'judge'] as Entry['stage'][]).map((s) => (
                <button key={s} type="button" className={stageFilter === s ? 'on' : ''} onClick={() => setStageFilter(s)} disabled={!stageCounts[s]}>{STAGE_LABEL[s]} {stageCounts[s] ?? 0}</button>
              ))}
            </div>
            {!entries.length && <p className="muted">표시할 프롬프트가 없습니다.</p>}
            {entries.map((e, i) => (
              <details key={i} className="an-entry" open={i === 0}>
                <summary>
                  <span className={`src ${e.source}`} title={SOURCE_HINT[e.source]}>{SOURCE_LABEL[e.source]}</span>
                  <strong>{STAGE_LABEL[e.stage]}</strong> · {KIND[e.kind] ?? e.kind}
                  {e.icon && <> · "{e.icon.name}"</>}
                  {e.image && <> · {e.image.template} #{e.image.id}</>}
                  <span className="muted"> · {when(e.ts)}{e.model ? ` · ${e.model}` : ''}{e.size ? ` · ${e.size} ${e.quality ?? ''}` : ''}{e.tokens_in != null ? ` · 토큰 ${tok(e.tokens_in)}/${tok(e.tokens_out)}` : ''}{e.cost_usd != null ? ` · ${usd(e.cost_usd)}` : ''}{e.ok === false ? ` · 실패 ${e.error ?? ''}` : ''}</span>
                </summary>
                <div className="an-entry-body">
                  {e.note && <p className="muted an-note">{e.note}</p>}
                  {e.refs && e.refs.length > 0 && <div className="muted an-refs">프롬프트 파일: {e.refs.map((r) => <code key={r}>{r}</code>)}</div>}
                  <div className="an-entry-grid">
                    <div className="an-prompts">
                      {e.system && <><div className="an-plabel">SYSTEM <span className="muted">고정 규칙</span></div><pre className="prompt"><Highlight text={e.system} vars={data.variables} /></pre></>}
                      {e.user && <><div className="an-plabel">USER <span className="muted">이번 입력</span></div><pre className="prompt"><Highlight text={e.user} vars={data.variables} /></pre></>}
                      {e.prompt && <><div className="an-plabel">IMAGE PROMPT{e.kind !== 'background' && e.stage === 'background' ? <span className="muted"> + 참조 이미지 1장</span> : null}</div><pre className="prompt"><Highlight text={e.prompt} vars={data.variables} /></pre></>}
                    </div>
                    {e.image && <a href={full(e.image.id, e.image.file)} target="_blank" rel="noreferrer"><img className="an-thumb" src={thumb(e.image.id, e.image.file)} alt="결과 배경" loading="lazy" /></a>}
                    {e.icon && <img className="an-thumb icon" src={`/api/icons/${e.icon.id}/png`} alt={e.icon.name} loading="lazy" />}
                  </div>
                </div>
              </details>
            ))}
          </section>

          {/* ③ 비용 */}
          <section className="card">
            <header><h3><span className="step-no">③</span> 최종 비용</h3><span className="muted">pricing.py 요율로 계산한 추정치 · 환율 {data.cost.summary.usd_krw}원/$</span></header>
            <table className="an-table">
              <thead><tr><th>단계</th><th>호출</th><th>실패</th><th>토큰 입력/출력</th><th>소요 합계</th><th>평균</th><th>추정 비용</th></tr></thead>
              <tbody>
                {Object.entries(data.cost.stages).map(([k, a]) => (
                  <tr key={k} className={a.count + a.failed === 0 ? 'muted' : ''}>
                    <td><strong>{data.cost.stage_label[k] ?? k}</strong></td>
                    <td>{a.count}</td><td>{a.failed || '-'}</td>
                    <td>{a.tokens_in || a.tokens_out ? `${tok(a.tokens_in)} / ${tok(a.tokens_out)}` : '-'}</td>
                    <td>{a.count ? sec(a.ms_total) : '-'}</td><td>{a.count ? sec(a.ms_avg) : '-'}</td>
                    <td>{usd(a.cost_usd)}{krw(a.cost_krw)}{a.estimated || a.unpriced ? ' *' : ''}</td>
                  </tr>
                ))}
                <tr className="total">
                  <td><strong>합계</strong></td>
                  <td>{data.cost.summary.ok}</td><td>{data.cost.summary.failed || '-'}</td>
                  <td>{tok(data.cost.summary.tokens_in)} / {tok(data.cost.summary.tokens_out)}</td>
                  <td>{sec(data.cost.summary.ms_total)}</td><td></td>
                  <td><strong>{usd(data.cost.summary.cost_usd)}</strong>{krw(data.cost.summary.cost_krw)}{data.cost.summary.estimated || data.cost.summary.unpriced ? ' *' : ''}</td>
                </tr>
              </tbody>
            </table>
            <p className="muted an-note">실패한 호출은 0 으로 봅니다. * 는 요금표에 없는 모델·크기를 기본 요율로 추정했거나(요금표: backend/data/pricing.json 으로 덮어쓰기) 토큰을 기록하지 않은 예전 호출이 섞였다는 표시입니다.</p>
            <details className="an-entry">
              <summary><strong>호출 내역 전체</strong> <span className="muted">{data.cost.rows.length}건 (최근 순)</span></summary>
              <table className="an-table tight">
                <thead><tr><th>시각</th><th>단계</th><th>종류</th><th>API</th><th>모델</th><th>크기·품질 / 토큰</th><th>소요</th><th>비용</th><th>전문</th><th>결과</th></tr></thead>
                <tbody>
                  {data.cost.rows.map((r, i) => (
                    <tr key={i} className={r.ok ? '' : 'bad'}>
                      <td title={r.ts}>{when(r.ts)}</td>
                      <td>{data.cost.stage_label[({ copy: 'copy', copy_fit: 'copy', copy_extras: 'copy', background: 'background', variant: 'background', product: 'background', icon: 'icon', icon_names: 'icon', icon_redraw: 'icon', pin: 'icon', judge: 'judge' } as Record<string, string>)[r.kind] ?? ''] ?? '-'}</td>
                      <td>{KIND[r.kind] ?? r.kind}</td><td>{r.api}</td><td>{r.model}{r.served_by ? ` → ${r.served_by}` : ''}</td>
                      <td>{r.api === 'chat' ? `${tok(r.tokens_in ?? 0)} / ${tok(r.tokens_out ?? 0)}` : `${r.size ?? ''} ${r.quality ?? ''}`}</td>
                      <td>{sec(r.ms)}</td><td>{usd(r.cost_usd)}{r.estimated ? '*' : ''}</td>
                      <td>{r.prompt_id ? '기록' : '-'}</td>
                      <td>{r.ok ? '성공' : `실패 ${r.error ?? ''}`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </section>

          {/* ④ 초기화한 이미지 보관본 */}
          <ArchivedSection designId={data.design.id} />
        </>
      )}
    </main>
  )
}


/* 최근 카피 생성에 넣은 규제 원문(업종 감지 시 전부)과 브리프로 검색한 브랜드 지식(RAG). 점수는 질의와의 코사인 유사도 */
function KnowledgeCard({ k }: { k: Knowledge }) {
  const fn = k.functional == null ? '입력 안 함' : k.functional.length ? k.functional.join(', ') : '기능성 아님'
  return (
    <section className="card">
      <header><h3>카피에 참고한 자료 (RAG)</h3>
        <span className="muted">규제 {k.law.length}조각 · 브랜드 지식 {k.guide.length}조각{k.embed_model ? ` · 임베딩 ${k.embed_model}` : ''}</span></header>
      {k.functional_mismatch && <div className="notice err">{k.functional_mismatch}</div>}
      {k.product_note && <div className="notice err">{k.product_note}</div>}
      {k.error && <div className="notice err">브랜드 지식 검색에 실패해 지식 없이 만들었습니다 ({k.error})</div>}
      <p className="muted an-note">기능성 입력: {fn}. 규제는 검색하지 않고, 품목에서 감지한 업종({k.industries.join(', ') || '없음'})의 조문을 전부 넣습니다.
        브랜드 지식은 상품명·설명·핵심 메시지·타깃으로 검색한 제품 2 · 브랜드 2 · 그룹 1 조각이며, 같은 브랜드·그룹 자료만 찾습니다.</p>
      {(k.motifs || k.directions.length > 0) && <p className="muted an-note">배경 이미지에 쓰는 지식 — 제품 소재: {k.motifs ?? '없음(상품명이 등록 제품과 다름)'} · 브랜드 연출 후보 {k.directions.length}개</p>}
      {k.variants.length > 0 && (
        <table className="an-table">
          <thead><tr><th>시안</th><th>각도</th><th>카피에 쓴 근거</th></tr></thead>
          <tbody>{k.variants.map((v) => <tr key={v.variant_no}><td>{v.variant_no}</td><td>{v.angle}</td><td>{v.evidence}</td></tr>)}</tbody>
        </table>
      )}
      <table className="an-table">
        <thead><tr><th>층</th><th>출처 · 절</th><th>유사도</th><th>내용</th></tr></thead>
        <tbody>
          {[...k.guide, ...k.law].map((r) => (
            <tr key={r.chunk_id}>
              <td>{r.layer}</td>
              <td>{r.doc_title}<div className="muted">{r.section}{r.functional?.length ? ` · 기능성 ${r.functional.join(', ')}` : ''}</div></td>
              <td>{r.score == null ? '—' : r.score.toFixed(3)}</td>
              <td><details><summary>{r.text.replace(/^\[[^\[\]]*\]\s*/, '').slice(0, 60)}…</summary><pre className="prompt">{r.text}</pre></details></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
