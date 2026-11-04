import { useCallback, useEffect, useState } from 'react'
import { api, type KnowledgeChunk, type KnowledgeDoc, type KnowledgeDocDetail, type KnowledgeSearch, type KnowledgeSummary } from './api'

/* 지식 브라우저 — 아모레퍼시픽 입장의 첫 화면(2026-11 재기획, docs/wireframes 아티팩트 2·3번).
   좌: 층(규제·그룹·브랜드·제품·캠페인)과 건수 + 검색 미리보기. 가운데: 문서 목록. 우: 조각 목록(펼치면 본문과 '이 조각이 쓰인 카피').
   브랜드·제품·캠페인 층은 상단에 브랜드 필터. 조회는 과금 없음, 검색 미리보기만 임베딩 1회. */

const LAYERS = ['규제', '그룹', '브랜드', '제품', '캠페인'] as const
const BRAND_LAYERS = new Set(['브랜드', '제품', '캠페인'])
const LAYER_HINT: Record<string, string> = {
  규제: '화장품법·시행규칙·별표. 업종이 감지되면 검색 없이 전부 프롬프트에 들어간다',
  그룹: '그룹 공통 자료(성분·원료·안전·연구). 소속 브랜드 모두가 공유한다',
  브랜드: '브랜드 소개·철학. 카피의 어조와 배경 연출의 근거',
  제품: '조각 하나가 제품 하나. 제품명·별칭이 브리프와 맞으면 유사도보다 먼저 쓰인다',
  캠페인: '이 앱에서 만든 카피·검수 결과를 다시 지식으로(예정)',
}

export default function KnowledgePage({ groupId, onMakeCampaign }: { groupId: number | null; onMakeCampaign?: (brandId: number, productName: string) => void }) {
  const [summary, setSummary] = useState<KnowledgeSummary | null>(null)
  const [layer, setLayer] = useState<string>('규제')
  const [brandId, setBrandId] = useState<number | null>(null)
  const [docs, setDocs] = useState<KnowledgeDoc[]>([])
  const [docId, setDocId] = useState<number | null>(null)
  const [detail, setDetail] = useState<{ doc: KnowledgeDocDetail; chunks: KnowledgeChunk[] } | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => { api.knowledgeSummary(groupId).then(setSummary).catch((e) => setErr(e.message)) }, [groupId])
  useEffect(() => {
    setDocId(null); setDetail(null); setOpen(null)
    api.knowledgeDocs(layer, groupId, BRAND_LAYERS.has(layer) ? brandId : null).then((d) => { setDocs(d); if (d.length) setDocId(d[0].id) }).catch((e) => setErr(e.message))
  }, [layer, brandId, groupId])
  useEffect(() => {
    if (docId === null) return
    api.knowledgeChunks(docId, groupId).then((d) => { setDetail(d); setOpen(d.chunks.find((c) => c.used.length)?.id ?? d.chunks[0]?.id ?? null) }).catch((e) => setErr(e.message))
  }, [docId, groupId])

  const counts = Object.fromEntries((summary?.layers ?? []).map((l) => [l.layer, l]))
  const brands = summary?.brands ?? []

  return (
    <div className="kb">
      <aside className="kb-side">
        <div className="kb-sum">
          <strong>{summary?.group?.name ?? '그룹'} · 지식</strong>
          <span className="muted">문서 {summary?.docs ?? '…'} · 조각 {summary?.chunks ?? '…'}{summary?.last_registered ? ` · 마지막 등록 ${summary.last_registered}` : ''}</span>
        </div>
        <h4>층</h4>
        <ul>
          {LAYERS.map((l) => (
            <li key={l}>
              <button className={layer === l ? 'on' : ''} disabled={(counts[l]?.docs ?? 0) === 0 && l === '캠페인'} onClick={() => { setLayer(l); setBrandId(null) }} title={LAYER_HINT[l]}>
                <span>{l}</span><em>{counts[l]?.docs ?? '…'}</em>
              </button>
            </li>
          ))}
        </ul>
        <SearchProbe groupId={groupId} brands={brands} onPick={(l, d) => { setLayer(l); setBrandId(null); setDocId(d) }} />
      </aside>
      <section className="kb-main">
        {err && <p className="error">{err}</p>}
        {BRAND_LAYERS.has(layer) && (
          <div className="kb-brands">
            <span className="muted">브랜드</span>
            <button className={brandId === null ? 'on' : ''} onClick={() => setBrandId(null)}>전체 {docs.length && brandId === null ? docs.length : ''}</button>
            {brands.map((b) => <button key={b.id} className={brandId === b.id ? 'on' : ''} onClick={() => setBrandId(b.id)}>{b.name}</button>)}
            {layer === '제품' && <span className="muted kb-note">제품 등록은 brands/*/sources.json + 수동 요약 (화면 등록은 후순위)</span>}
          </div>
        )}
        <div className="kb-cols">
          <div className="kb-docs">
            <header><strong>문서 {docs.length}</strong><span className="muted">{LAYER_HINT[layer]}</span></header>
            {docs.length === 0 && <p className="muted">이 층에 등록된 문서가 없습니다</p>}
            {docs.map((d) => (
              <button key={d.id} className={`kb-doc ${docId === d.id ? 'on' : ''}`} onClick={() => setDocId(d.id)}>
                <strong>{d.title}</strong>
                <span className="muted">
                  {d.brand ? `${d.brand} · ` : ''}조각 {d.chunks}{d.industry ? ` · 업종 ${d.industry}` : ''}{d.archived ? ` · 이전 판 ${d.archived}(보관)` : ''}{d.used_designs ? ` · 캠페인 ${d.used_designs}` : ''}
                </span>
              </button>
            ))}
          </div>
          <div className="kb-chunks">
            {detail ? (
              <>
                <header>
                  <strong>{detail.doc.title} · 조각 {detail.chunks.length}</strong>
                  <span className="muted">
                    {detail.doc.created_at ? `등록 ${detail.doc.created_at}` : ''}{detail.doc.fetched_at ? ` · 수집 ${detail.doc.fetched_at}` : ''}
                    {detail.chunks[0]?.embed_model ? ` · 임베딩 ${detail.chunks[0].embed_model}` : ''}
                    {detail.doc.source_url ? <> · <a href={detail.doc.source_url} target="_blank" rel="noreferrer">출처</a></> : null}
                  </span>
                </header>
                {(detail.doc.motifs || detail.doc.directions.length > 0) && (
                  <p className="kb-visual muted">시각 소재: {detail.doc.motifs || '—'}{detail.doc.directions.length ? ` · 연출: ${detail.doc.directions.join(' / ')}` : ''}</p>
                )}
                {detail.chunks.map((c) => (
                  <article key={c.id} className={`kb-chunk ${open === c.id ? 'open' : ''}`}>
                    <button className="kb-chunk-head" onClick={() => setOpen(open === c.id ? null : c.id)}>
                      <span>{c.section || `조각 ${c.seq}`}</span>
                      <em>{c.used.length ? `캠페인 ${c.used.length}` : '카피 0'}{c.has_vector ? '' : ' · 벡터 없음'}</em>
                    </button>
                    {open === c.id && (
                      <div className="kb-chunk-body">
                        {(c.product_name || c.aliases.length > 0 || c.functional.length > 0) && (
                          <div className="entry-chips">
                            {c.product_name && <em>제품명 {c.product_name}</em>}
                            {c.aliases.length > 0 && <em>별칭 {c.aliases.join(' · ')}</em>}
                            <em>기능성 {c.functional.length ? c.functional.join(' · ') : '없음'}</em>
                            <em>{c.chars}자</em>
                          </div>
                        )}
                        <p className="kb-text">{c.text}</p>
                        <h5>이 조각이 쓰인 카피</h5>
                        {c.used.length === 0
                          ? <p className="muted kb-empty">아직 이 조각을 근거로 만든 카피가 없습니다</p>
                          : c.used.map((u) => (
                            <div key={`${u.design_id}-${u.role}`} className="kb-use">
                              <em className={u.blocked ? 'bad' : u.role === '규제' ? 'law' : 'ok'}>{u.blocked ? '차단' : u.role}</em>
                              <span>{u.brand}{u.product_name ? ` · ${u.product_name}` : ''}{u.headline ? ` · “${u.headline}”` : ' · 카피 없음'}{u.violations ? ` · 위반 ${u.violations}` : ''}</span>
                              <span className="muted">캠페인 #{u.design_id} · {u.status}</span>
                            </div>
                          ))}
                        {layer === '제품' && c.product_name && (
                          <div className="kb-cta">
                            <div>
                              <strong>이 제품으로 시안 만들기</strong>
                              <span className="muted">제품명·별칭·기능성이 브리프에 자동으로 들어가고 캠페인 화면으로 넘어갑니다 (후순위)</span>
                            </div>
                            <button disabled={!onMakeCampaign || !detail.doc} onClick={() => {
                              const d = docs.find((x) => x.id === detail.doc.id)
                              if (onMakeCampaign && d?.brand_id) onMakeCampaign(d.brand_id, c.product_name!)
                            }}>캠페인 만들기 →</button>
                          </div>
                        )}
                      </div>
                    )}
                  </article>
                ))}
              </>
            ) : <p className="muted">문서를 고르면 조각이 보입니다</p>}
          </div>
        </div>
      </section>
    </div>
  )
}

function SearchProbe({ groupId, brands, onPick }: { groupId: number | null; brands: { id: number; name: string }[]; onPick: (layer: string, docId: number) => void }) {
  const [brandId, setBrandId] = useState<number | null>(null)
  const [text, setText] = useState('')
  const [product, setProduct] = useState('')
  const [res, setRes] = useState<KnowledgeSearch | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const run = useCallback(() => {
    if (!text.trim() && !product.trim()) return
    setBusy(true); setErr('')
    api.knowledgeSearch({ group_id: groupId, brand_id: brandId, product_name: product.trim(), description: text.trim(), key_message: '', target: '' })
      .then(setRes).catch((e) => setErr(e.message)).finally(() => setBusy(false))
  }, [text, product, brandId, groupId])
  return (
    <div className="kb-probe">
      <h4>검색 미리보기</h4>
      <p className="muted">브리프를 넣으면 카피 생성이 찾는 조각을 보여 줍니다 (임베딩 1회)</p>
      <select value={brandId ?? ''} onChange={(e) => setBrandId(e.target.value ? Number(e.target.value) : null)} aria-label="브랜드">
        <option value="">브랜드 없음(규제·그룹만)</option>
        {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
      </select>
      <input value={product} onChange={(e) => setProduct(e.target.value)} placeholder="상품명 (선택)" aria-label="상품명" />
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} placeholder="예: 48시간 만에 콜라겐을 회복시키는 크림" aria-label="브리프" />
      <button className="dark" disabled={busy} onClick={run}>{busy ? '찾는 중…' : '찾아보기'}</button>
      {err && <p className="error">{err}</p>}
      {res && (
        <div className="kb-probe-res">
          <span className="muted">업종 {res.industries.join(', ') || '감지 안 됨'}{res.industries.length ? ` → 규제 ${res.law_all.length}조각 전부 투입` : ''} · {res.hybrid ? '하이브리드' : '벡터'} 검색</span>
          {res.layers.map((l) => (
            <div key={l.layer} className="kb-probe-layer">
              <strong>{l.layer} <em className="muted">k={l.k}</em></strong>
              {l.rows.length === 0 && <span className="muted">없음</span>}
              {l.rows.map((r) => (
                <button key={r.chunk_id} className="kb-probe-row" onClick={() => onPick(l.layer, r.doc_id)} title={r.doc_title}>
                  <span>{r.section || r.doc_title}</span><em>{r.exact_product ? '정확 일치' : r.score.toFixed(3)}</em>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
