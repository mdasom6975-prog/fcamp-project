import { useCallback, useEffect, useState } from 'react'
import { busy } from './Busy'
import { KIND_LABEL, type ApiCalls } from './DevStats'

type Preset = { id: string; name: string; desc: string; tags: string[]; colors: string[] }
type Item = { preset_id: number; name: string; image: { id: number; file: string } | null }
type LegalHit = { phrase: string; reason: string; law: string; suggest: string; grounded: boolean; severity: string }
type Campaign = { design_id: number; legal: LegalHit[]; key_message: string; preset_id: string | null; has_photo: boolean; items: Item[] }

async function j<T>(r: Response): Promise<T> {
  const body = await r.json().catch(() => ({}))
  if (!r.ok) {
    const d = body.detail
    throw new Error((typeof d === 'string' ? d : d?.message) || '요청에 실패했습니다')
  }
  return body as T
}
const post = (url: string, body?: unknown, method = 'POST') =>
  fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) }).then((r) => j<any>(r))

type Price = {
  usd_krw: number; image_model: string; image_quality: string; chat_model: string
  image_prices: { group: string; size: string; usd: number | null; krw: number | null }[]
  chat_rates_per_million: { in: number; out: number } | null
  plan: { step: string; kind: string; calls: string; note: string }[]
  image_total_usd: number; image_total_krw: number | null
  restyle: { calls: number; usd: number; krw: number | null; note: string }
}
const won = (v: number | null | undefined) => (v == null ? '—' : `${v.toLocaleString()}원`)

// 호출 정보·가격표: 만들기 전에는 호출 계획(예상), 만든 뒤에는 이 캠페인이 실제로 부른 호출·비용을 보여 준다.
function CallInfo({ designId, refreshKey }: { designId?: number; refreshKey?: unknown }) {
  const [price, setPrice] = useState<Price | null>(null)
  const [calls, setCalls] = useState<ApiCalls | null>(null)
  const [open, setOpen] = useState(false)       // 하단 고정 패널이라 열고 닫아도 본문 높이는 바뀌지 않는다
  useEffect(() => { fetch('/api/smallbiz/pricing').then((r) => j<Price>(r)).then(setPrice).catch(() => {}) }, [])
  useEffect(() => {
    if (!designId) { setCalls(null); return }
    fetch(`/api/designs/${designId}/api-calls`).then((r) => j<ApiCalls>(r)).then(setCalls).catch(() => {})
  }, [designId, refreshKey])
  if (!price) return null
  return (
    <div className="sb-dock">
      <button className="sb-dock-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        호출 정보·가격표 {open ? '▼' : '▲'}
        {calls && <span className="muted"> · 실제 {calls.total}회 · ${calls.cost_usd.toFixed(3)} ({won(calls.cost_krw)})</span>}
      </button>
      {open && <div className="sb-calls">
      {calls && (
        <>
          <h4>이 캠페인이 실제로 부른 호출</h4>
          <table>
            <thead><tr><th>종류</th><th>횟수</th><th>실패</th><th>비용</th></tr></thead>
            <tbody>
              {Object.entries(calls.by_kind).map(([k, a]) => (
                <tr key={k}><td>{KIND_LABEL[k] ?? k}</td><td>{a.count}</td><td>{a.failed}</td>
                  <td>${a.cost_usd.toFixed(4)} ({won(a.cost_krw)}){a.estimated && '*'}</td></tr>
              ))}
              <tr className="sum"><td>합계</td><td>{calls.total}</td><td>{calls.failed}</td>
                <td>${calls.cost_usd.toFixed(4)} ({won(calls.cost_krw)}){calls.estimated && '*'}</td></tr>
            </tbody>
          </table>
          <p className="muted">LLM {calls.chats}회 · 이미지 {calls.images}회. *는 토큰 기록이 없거나 요금표에 없는 모델이라 추정한 값</p>
        </>
      )}
      <h4>호출 계획 (처음 만들 때)</h4>
      <table>
        <thead><tr><th>단계</th><th>종류</th><th>횟수</th><th>비고</th></tr></thead>
        <tbody>{price.plan.map((r) => <tr key={r.step}><td>{r.step}</td><td>{r.kind}</td><td>{r.calls}</td><td>{r.note}</td></tr>)}</tbody>
      </table>
      <h4>가격표 (추정)</h4>
      <table>
        <thead><tr><th>항목</th><th>단가</th></tr></thead>
        <tbody>
          {price.image_prices.map((r) => (
            <tr key={r.group}><td>이미지 {price.image_model} · {price.image_quality} · {r.group} {r.size}</td>
              <td>장당 ${r.usd?.toFixed(3) ?? '—'} ({won(r.krw)})</td></tr>
          ))}
          {price.chat_rates_per_million && (
            <tr><td>카피 {price.chat_model}</td><td>100만 토큰당 입력 ${price.chat_rates_per_million.in} · 출력 ${price.chat_rates_per_million.out}</td></tr>
          )}
          <tr className="sum"><td>처음 만들 때 이미지 합계 (카피 제외)</td><td>${price.image_total_usd.toFixed(3)} ({won(price.image_total_krw)})</td></tr>
          <tr><td>스타일 바꾸기·배경 다시 만들기 1회</td><td>${price.restyle.usd.toFixed(3)} ({won(price.restyle.krw)}) · 이미지 {price.restyle.calls}회</td></tr>
        </tbody>
      </table>
      <p className="muted">환율 1달러 = {price.usd_krw.toLocaleString()}원. 이미지 단가는 장당 표 기준 추정이고 실제 과금은 토큰 기준이라 위 실제 호출 표가 더 정확합니다.</p>
      </div>}
    </div>
  )
}

const DRAFT_KEY = 'smallbizDraft'       // 입력 임시저장(브라우저)
const CAMP_KEY = 'smallbizCampaign'     // 마지막으로 만든 캠페인 id (새로고침해도 결과를 다시 연다)
const store = {
  get<T>(k: string): T | null { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) as T : null } catch { return null } },
  set(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)) } catch { /* 저장 실패는 무시 */ } },
  del(k: string) { try { localStorage.removeItem(k) } catch { /* 무시 */ } },
}
type Draft = { what: string; message: string; shop: string; presetId: string | null; photo: { id: number; warnings: string[]; cutout?: boolean } | null }

// 이미지 크게 보기: 배경을 누르면 닫히고, ←/→ 로 다른 규격, Esc 로 닫는다.
function Lightbox({ items, index, onIndex, onClose }: { items: Item[]; index: number; onIndex: (i: number) => void; onClose: () => void }) {
  const shown = items.filter((i) => i.image)
  const cur = items[index]
  const move = useCallback((d: number) => {
    const at = shown.findIndex((i) => i.preset_id === cur.preset_id)
    onIndex(items.indexOf(shown[(at + d + shown.length) % shown.length]))
  }, [items, shown, cur, onIndex])
  useEffect(() => {
    const on = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); if (e.key === 'ArrowRight') move(1); if (e.key === 'ArrowLeft') move(-1) }
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [move, onClose])
  if (!cur?.image) return null
  return (
    <div className="sb-lightbox" role="dialog" aria-modal="true" aria-label={`${cur.name} 크게 보기`} onClick={onClose}>
      <img src={`/api/generated/${cur.image.id}/file?v=${cur.image.file}`} alt={cur.name} onClick={(e) => e.stopPropagation()} />
      <div className="sb-lb-bar" onClick={(e) => e.stopPropagation()}>
        {shown.length > 1 && <button onClick={() => move(-1)} aria-label="이전">←</button>}
        <span>{cur.name}</span>
        {shown.length > 1 && <button onClick={() => move(1)} aria-label="다음">→</button>}
        <button onClick={onClose}>닫기</button>
      </div>
    </div>
  )
}

// 소상공인용 화면: 품목 글 + 사진(선택) + 스타일 하나만 고르면 광고 이미지를 만든다. 기존 화면과 따로 동작한다.
export default function SmallBizPage() {
  const [presets, setPresets] = useState<Preset[]>([])
  const draft = useState(() => store.get<Draft>(DRAFT_KEY))[0]
  const [what, setWhat] = useState(draft?.what ?? '')
  const [message, setMessage] = useState(draft?.message ?? '')
  const [shop, setShop] = useState(draft?.shop ?? '')
  const [presetId, setPresetId] = useState<string | null>(draft?.presetId ?? null)
  const [photo, setPhoto] = useState<{ id: number; warnings: string[]; cutout?: boolean; url: string } | null>(
    draft?.photo ? { ...draft.photo, url: `/api/assets/${draft.photo.id}/file` } : null)
  const [camp, setCamp] = useState<Campaign | null>(null)
  const [zoom, setZoom] = useState<number | null>(null)
  const [saved, setSaved] = useState(!!draft)
  const [err, setErr] = useState('')
  const [rec, setRec] = useState<string[]>([])

  useEffect(() => { fetch('/api/smallbiz/presets').then((r) => j<{ presets: Preset[] }>(r)).then((d) => setPresets(d.presets)).catch((e) => setErr(e.message)) }, [])
  useEffect(() => {
    if (what.trim().length < 2) { setRec([]); return }
    const t = setTimeout(() => { post('/api/smallbiz/recommend', { text: what }).then((d) => setRec(d.preset_ids)).catch(() => {}) }, 400)
    return () => clearTimeout(t)
  }, [what])

  // 입력이 바뀌면 자동 임시저장(비어 있으면 지움)
  useEffect(() => {
    if (!what && !message && !shop && !photo && !presetId) { store.del(DRAFT_KEY); setSaved(false); return }
    store.set(DRAFT_KEY, { what, message, shop, presetId, photo: photo ? { id: photo.id, warnings: photo.warnings, cutout: photo.cutout } : null } satisfies Draft)
    setSaved(true)
  }, [what, message, shop, presetId, photo])
  // 마지막 캠페인이 있으면 새로고침 뒤에도 결과 화면을 연다
  useEffect(() => {
    const id = store.get<number>(CAMP_KEY)
    if (id) fetch(`/api/smallbiz/campaigns/${id}`).then((r) => j<Campaign>(r)).then(setCamp).catch(() => store.del(CAMP_KEY))
  }, [])
  const clearDraft = () => { setWhat(''); setMessage(''); setShop(''); setPresetId(null); setPhoto(null); setErr('') }

  const chosen = presetId ?? rec[0] ?? null
  const refresh = (id: number) => fetch(`/api/smallbiz/campaigns/${id}`).then((r) => j<Campaign>(r)).then((c) => { store.set(CAMP_KEY, id); setCamp(c) })
  const run = async (label: string, fn: () => Promise<void>) => {
    setErr('')
    try { await busy.run(label, fn) } catch (e) { setErr((e as Error).message) }
  }

  const onPhoto = async (f: File | undefined) => {
    if (!f) return
    await run('사진을 올리는 중…', async () => {
      const form = new FormData(); form.append('file', f)
      const d = await fetch('/api/smallbiz/photo', { method: 'POST', body: form }).then((r) => j<{ id: number; warnings: string[]; cutout: boolean }>(r))
      setPhoto({ id: d.id, warnings: d.warnings, cutout: d.cutout, url: URL.createObjectURL(f) })
    })
  }

  const create = () => run('광고 이미지를 만드는 중… (1~2분)', async () => {
    const c = await post('/api/smallbiz/campaigns', { what, message, shop_name: shop, photo_asset_id: photo?.id ?? null, preset_id: chosen })
    await post(`/api/smallbiz/campaigns/${c.design_id}/images`, { new_background: false })
    await refresh(c.design_id)
  })
  const again = (newStyle?: string) => run('다시 만드는 중… (1~2분)', async () => {
    if (!camp) return
    if (newStyle) await post(`/api/smallbiz/campaigns/${camp.design_id}/style`, { preset_id: newStyle }, 'PUT')
    await post(`/api/smallbiz/campaigns/${camp.design_id}/images`, { new_background: true })
    await refresh(camp.design_id)
  })
  const download = () => run('내려받을 파일을 만드는 중…', async () => {
    if (!camp) return
    const e = await post(`/api/smallbiz/campaigns/${camp.design_id}/export`)
    window.location.href = e.download_url
  })

  if (camp) {
    const others = presets.filter((p) => p.id !== camp.preset_id)
    return (
      <main className="sb-wrap">
        <h2>광고 이미지가 만들어졌어요</h2>
        {err && <p className="sb-err">{err}</p>}
        <p className="muted">문구: {camp.key_message}</p>
        {camp.legal.length === 0
          ? <p className="muted" title="표시·광고법 등 법적 금지·주의 표현 사전 기준의 참고용 자동 검사입니다. 법률 자문이 아닙니다.">✓ 법적 표현 점검 통과 (참고용 자동 검사)</p>
          : camp.legal.map((h) => (
            <p key={h.phrase} className={h.severity === '필수' ? 'sb-err' : 'sb-warn'}>
              {h.severity === '필수' ? '✕' : '△'} 법적 표현 '{h.phrase}' — {h.reason}<br />
              <small>근거: {h.law}{h.suggest ? ` · 제안: ${h.suggest}` : ''}{h.grounded ? ' · 입력에 있던 표현이라 근거 자료 확인 필요' : ''}</small>
            </p>))}
        <div className="sb-results">
          {camp.items.map((it, idx) => (
            <figure key={it.preset_id}>
              {it.image ? <img className="sb-zoomable" src={`/api/generated/${it.image.id}/file?v=${it.image.file}`} alt={it.name}
                title="눌러서 크게 보기" onClick={() => setZoom(idx)} /> : <div className="sb-empty">이미지 없음</div>}
              <figcaption>{it.name}</figcaption>
            </figure>
          ))}
        </div>
        {zoom !== null && <Lightbox items={camp.items} index={zoom} onIndex={setZoom} onClose={() => setZoom(null)} />}
        {!camp.has_photo && <p className="muted">사진 없이 만든 상품 그림은 예시 이미지예요. 실제 상품과 다를 수 있어요.</p>}
        <div className="sb-actions">
          <button className="primary" onClick={download}>내려받기</button>
          <button onClick={() => again()}>배경 다시 만들기</button>
          <button onClick={() => { store.del(CAMP_KEY); setCamp(null) }}>입력 고쳐서 다시 만들기</button>
        </div>
        <CallInfo designId={camp.design_id} refreshKey={camp.items.map((i) => i.image?.file).join()} />
        <h3>다른 스타일로 보기</h3>
        <div className="sb-presets">
          {others.map((p) => (
            <button key={p.id} className="sb-preset" onClick={() => again(p.id)}>
              <span className="sb-sw">{p.colors.slice(0, 3).map((c) => <i key={c} style={{ background: c }} />)}</span>
              <strong>{p.name}</strong><small>{p.desc}</small>
            </button>
          ))}
        </div>
      </main>
    )
  }

  return (
    <main className="sb-wrap">
      <h2>우리 가게 광고 이미지 만들기</h2>
      {err && <p className="sb-err">{err}</p>}
      <label>무엇을 홍보하나요? (가격·행사가 있으면 함께 적어 주세요)
        <textarea value={what} onChange={(e) => setWhat(e.target.value)} rows={3} placeholder="예) 동네 수제 딸기 케이크, 오늘만 1만원" />
      </label>
      <label>상품 사진 (선택)
        <input type="file" accept="image/*" onChange={(e) => onPhoto(e.target.files?.[0])} />
      </label>
      {photo && (
        <div className="sb-photo">
          <img src={photo.url} alt="올린 사진" />
          {photo.cutout && <p className="sb-ok">사진 속 배경을 지웠어요. 상품 모양은 그대로 광고에 넣어요</p>}
          {photo.warnings.map((w) => <p key={w} className="sb-warn">{w}</p>)}
        </div>
      )}
      <label>한마디 (선택, 비우면 AI 가 제안해요)
        <input value={message} onChange={(e) => setMessage(e.target.value)} maxLength={200} />
      </label>
      <label>가게 이름 (선택)
        <input value={shop} onChange={(e) => setShop(e.target.value)} maxLength={60} />
      </label>
      <h3>스타일</h3>
      <div className="sb-presets">
        {presets.map((p) => (
          <button key={p.id} className={`sb-preset${chosen === p.id ? ' on' : ''}`} onClick={() => setPresetId(p.id)}>
            <span className="sb-sw">{p.colors.slice(0, 3).map((c) => <i key={c} style={{ background: c }} />)}</span>
            <strong>{p.name}{rec.includes(p.id) && ' · 추천'}</strong><small>{p.desc}</small>
          </button>
        ))}
      </div>
      <div className="sb-actions">
        <button className="primary" disabled={!what.trim() || !chosen} onClick={create}>광고 이미지 만들기</button>
        <button onClick={clearDraft} disabled={!saved}>입력 지우기</button>
        <span className="muted">{saved ? '입력 내용은 이 브라우저에 자동 임시저장돼요' : ''}</span>
      </div>
      <CallInfo />
    </main>
  )
}
