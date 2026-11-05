import { useCallback, useEffect, useState } from 'react'
import { api, type KnowledgeChunk, type KnowledgeDoc, type KnowledgeDocDetail, type KnowledgeSearch, type KnowledgeSummary, type ProductImportResult, type ProductsDiscover } from './api'

/* 지식 브라우저 — 아모레퍼시픽 입장의 첫 화면(2026-11 재기획, docs/wireframes 아티팩트 2·3번).
   참고·관리 화면(그룹 입구의 '지식' 탭, 첫 화면이 아니다 — 00 정의서 v2.3). 좌: 층과 건수 + 검색 미리보기. 가운데: 문서 목록. 우: 조각(본문과 '이 조각이 쓰인 카피').
   시안 만들기는 여기서 하지 않는다(3단계 메시지 입력의 '제품 고르기').
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

export default function KnowledgePage({ groupId }: { groupId: number | null }) {
  const [summary, setSummary] = useState<KnowledgeSummary | null>(null)
  const [layer, setLayer] = useState<string>('규제')
  const [brandId, setBrandId] = useState<number | null>(null)
  const [docs, setDocs] = useState<KnowledgeDoc[]>([])
  const [docId, setDocId] = useState<number | null>(null)
  const [detail, setDetail] = useState<{ doc: KnowledgeDocDetail; chunks: KnowledgeChunk[] } | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => { api.knowledgeSummary(groupId).then(setSummary).catch((e) => setErr(e.message)) }, [groupId])
  const reload = useCallback((keep?: number | null) => {
    api.knowledgeDocs(layer, groupId, BRAND_LAYERS.has(layer) ? brandId : null)
      .then((d) => { setDocs(d); setDocId(keep != null && d.some((x) => x.id === keep) ? keep : (d[0]?.id ?? null)) })
      .catch((e) => setErr(e.message))
    api.knowledgeSummary(groupId).then(setSummary).catch(() => undefined)
  }, [layer, brandId, groupId])
  useEffect(() => { setDocId(null); setDetail(null); setOpen(null); reload() }, [reload])
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
            {layer === '제품' && brandId !== null && <ImportPanel brandId={brandId} brandName={brands.find((b) => b.id === brandId)?.name ?? ''} onDone={(firstDoc) => reload(firstDoc)} />}
            {layer === '제품' && brandId === null && <span className="muted kb-note">브랜드를 고르면 공식 사이트에서 제품을 가져올 수 있습니다</span>}
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
                {detail.doc.image_asset_id && (
                  <div className="kb-product">
                    <img src={`/api/assets/${detail.doc.image_asset_id}/file`} alt={`${detail.doc.title} 제품 사진`} />
                    <div className="entry-chips">
                      {detail.doc.line && <em>{detail.doc.line}</em>}
                      {detail.doc.quantity && <em>{detail.doc.quantity}</em>}
                      {detail.doc.price != null && <em>{detail.doc.price.toLocaleString()}원</em>}
                      <em>공식 사이트에서 가져옴</em>
                    </div>
                  </div>
                )}
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


function ImportPanel({ brandId, brandName, onDone }: { brandId: number; brandName: string; onDone: (firstDoc?: number | null) => void }) {
  /* 제품 가져오기: 목록 발견(과금 없음) → 아직 없는 제품을 4개씩 가져오기(제품당 임베딩 1회) → 진행·문제 표시. 사용자 입력은 버튼 두 번. */
  const [open, setOpen] = useState(false)
  const [disc, setDisc] = useState<ProductsDiscover | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<ProductImportResult[]>([])
  const [err, setErr] = useState('')
  const discover = () => { setBusy(true); setErr(''); api.productsDiscover(brandId).then(setDisc).catch((e) => setErr(e.message)).finally(() => setBusy(false)) }
  const importAll = async () => {
    if (!disc) return
    const todo = disc.products.filter((p) => !p.doc_id).map((p) => p.seq)
    setBusy(true); setErr(''); setDone([])
    const acc: ProductImportResult[] = []
    try {
      for (let i = 0; i < todo.length; i += 4) {
        const r = await api.productsImport(brandId, todo.slice(i, i + 4))
        acc.push(...r.results); setDone([...acc])
      }
      const first = acc.find((r) => r.ok)?.doc_id ?? null
      onDone(first)
      setDisc(await api.productsDiscover(brandId))
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const missing = disc ? disc.products.filter((p) => !p.doc_id).length : 0
  return (
    <div className="kb-import">
      <button className={open ? 'on' : ''} onClick={() => { setOpen(!open); if (!open && !disc) discover() }}>제품 가져오기</button>
      {open && (
        <div className="kb-import-panel">
          {busy && !disc && <span className="muted">공식 사이트 목록을 읽는 중…</span>}
          {err && <p className="error">{err}</p>}
          {disc && !disc.supported && <span className="muted">{brandName} 은 아직 사이트 규칙이 없습니다 (products_import.SITES)</span>}
          {disc && disc.supported && (
            <>
              <span>{brandName} 공식 사이트 제품 {disc.products.length}개 · 이미 가져온 것 {disc.products.length - missing} · 새로 {missing}</span>
              <button className="dark" disabled={busy || missing === 0} onClick={importAll}>{busy ? `가져오는 중… ${done.length}/${missing}` : missing ? `새 제품 ${missing}개 가져오기` : '모두 가져왔습니다'}</button>
              {done.length > 0 && (
                <ul className="kb-import-log">
                  {done.map((r) => <li key={r.seq} className={r.ok ? 'ok' : 'bad'}>{r.ok ? '완료' : '실패'} · {r.name || r.seq}{r.problems.length ? ` — ${r.problems.join(', ')}` : ''}</li>)}
                </ul>
              )}
              <span className="muted">이미지는 제품 사진 큰 판을 받아 1024px WEBP 로 저장합니다. 기능성 표시 여부는 사이트에 없어 비워 둡니다.</span>
            </>
          )}
        </div>
      )}
    </div>
  )
}

