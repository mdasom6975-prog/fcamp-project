import { useCallback, useEffect, useState } from 'react'
import EditPanel from './EditPanel'
import ReviewPanel from './ReviewPanel'

type PresetLite = { id: number; name: string; status: string; production_width: number | null; production_height: number | null }
type PlatformLite = { name: string; media_only: boolean; ad_types: { name: string; presets: PresetLite[] }[] }
type Design = {
  id: number; kind: string; status: string
  brief: { product_name: string; description: string; key_message: string; target: string; icon?: { purpose: string; width: number | null; height: number | null } }
  presets: { id: number; platform: string; name: string; width: number | null; height: number | null; status: string }[]
  brand: { id: number; name: string; version_no: number; current_version_no: number }
  brand_outdated: boolean; created_at: string
}

async function post(url: string, body: unknown) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) {
    const d = j.detail
    throw new Error(Array.isArray(d) ? d.map((x: { msg: string }) => x.msg).join(', ') : d || '요청에 실패했습니다')
  }
  return j
}

type Violation = { type: string; field: string; detail: string; severity: string }
type Variant = { id: number; variant_no: number; headline: string; body: string; cta: string; violations: Violation[]; attempts: number; selected: boolean }
type ImgOut = { id: number; width: number; height: number; layout: { warnings?: string[]; template?: string; margin_source?: string; separate_assets?: boolean } } | null
type ImgRow = { preset_id: number; background: ImgOut; composite: ImgOut }
type Limit = { field: string; min: number | null; max: number | null; level: string | null } | null
type CopyGroup = {
  preset_id: number; platform: string; name: string; batch_no: number | null
  content_kind: string; width: number | null; height: number | null
  limits: { headline: Limit; body: Limit; cta: Limit; skipped: { field: string; reason: string }[] }
  variants: Variant[]
}

function CopyPanel({ designId }: { designId: number }) {
  const [groups, setGroups] = useState<CopyGroup[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [imgs, setImgs] = useState<Record<number, ImgRow>>({})
  const [imgBusy, setImgBusy] = useState<number | null>(null)
  const [imgErr, setImgErr] = useState<Record<number, string>>({})

  const loadImgs = useCallback(() =>
    fetch(`/api/designs/${designId}/images`).then((r) => r.json()).then((rows: ImgRow[]) => {
      if (Array.isArray(rows)) setImgs(Object.fromEntries(rows.map((x) => [x.preset_id, x])))
    }), [designId])

  useEffect(() => {
    fetch(`/api/designs/${designId}/copy`).then((r) => r.json()).then((g) => Array.isArray(g) && setGroups(g))
    loadImgs()
  }, [designId, loadImgs])

  const choose = async (presetId: number, variantId: number) => {
    await post(`/api/designs/${designId}/copy/${variantId}/select`, {})
    setGroups((gs) => gs.map((g) => g.preset_id !== presetId ? g
      : { ...g, variants: g.variants.map((v) => ({ ...v, selected: v.id === variantId })) }))
  }

  const makeImage = async (presetId: number, regenerate: boolean) => {
    setImgBusy(presetId); setImgErr((e) => ({ ...e, [presetId]: '' }))
    try {
      await post(`/api/designs/${designId}/presets/${presetId}/image`, { regenerate_background: regenerate })
      await loadImgs()
    } catch (e) {
      setImgErr((x) => ({ ...x, [presetId]: (e as Error).message }))
    } finally {
      setImgBusy(null)
    }
  }

  const generate = async () => {
    setBusy(true); setErr(null)
    try {
      const j = await post(`/api/designs/${designId}/copy`, {})
      setGroups(j.groups)
      if (j.failures?.length) setErr(`일부 지면의 생성에 실패했습니다: ${j.failures.join(', ')}`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const lim = (l: Limit) => (l ? `${l.field} ${l.min != null ? `${l.min}~` : ''}${l.max ?? ''}자` : '제한 정보 없음')
  const has = groups.some((g) => g.variants.length > 0)

  return (
    <div className="copy">
      <div className="row">
        <button className="primary" disabled={busy} onClick={generate}>{has ? '카피 다시 생성' : '카피 생성'}</button>
        {busy && <span className="muted">생성 중… 지면 수에 따라 수십 초 걸릴 수 있습니다.</span>}
      </div>
      {err && <div className="notice err">{err}</div>}
      {groups.some((g) => g.content_kind === '완성형' && imgs[g.preset_id]?.composite) && (
        <CommonText designId={designId} onApplied={loadImgs} />
      )}
      {groups.filter((g) => g.variants.length > 0).map((g) => (
        <div key={g.preset_id} className="copy-group">
          <h4>{g.platform} · {g.name}</h4>
          <p className="muted">
            적용한 글자 수 제한 — 제목: {lim(g.limits.headline)} · 본문: {lim(g.limits.body)} · CTA: {lim(g.limits.cta)}
            {g.limits.skipped.length > 0 && ` · 이번 단계 제외: ${g.limits.skipped.map((s) => `${s.field}(${s.reason})`).join(', ')}`}
          </p>
          {g.variants.map((v) => (
            <div key={v.id} className={`variant ${v.violations.length ? 'bad' : ''}`}>
              <div className="muted">
                <label style={{ display: 'inline-flex', gap: 6, marginRight: 8 }}>
                  <input type="radio" name={`sel-${g.preset_id}`} checked={v.selected} onChange={() => choose(g.preset_id, v.id)} /> 이 카피 사용
                </label>
                시안 {v.variant_no}{v.attempts > 1 ? ' · 규칙 위반으로 1회 재생성됨' : ''}
              </div>
              <div><strong>제목</strong> {v.headline} <span className="muted">({v.headline.length}자)</span></div>
              <div><strong>본문</strong> {v.body} <span className="muted">({v.body.length}자)</span></div>
              <div><strong>CTA</strong> {v.cta} <span className="muted">({v.cta.length}자)</span></div>
              {v.violations.map((x, i) => (
                <span key={i} className={`badge ${x.severity === '필수' || x.severity === '강제' || x.severity === '수정 필요' ? 'b-need' : 'b-warn'}`}>
                  {x.severity} · {x.field}: {x.detail}
                </span>
              ))}
            </div>
          ))}
          <ImageBlock g={g} row={imgs[g.preset_id]} busy={imgBusy === g.preset_id} err={imgErr[g.preset_id]}
            canMake={g.content_kind !== '완성형' || g.variants.some((v) => v.selected)} onMake={makeImage}
            designId={designId} onEdited={loadImgs} />
        </div>
      ))}
    </div>
  )
}

function CommonText({ designId, onApplied }: { designId: number; onApplied: () => void }) {
  const [text, setText] = useState('')
  const [res, setRes] = useState<{ applied: number[]; skipped: { name: string; reason: string }[] } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const apply = async () => {
    setErr(null); setRes(null)
    try { setRes(await post(`/api/designs/${designId}/edit/common-text`, { text })); onApplied() }
    catch (e) { setErr((e as Error).message) }
  }
  return (
    <div className="imgblock">
      <strong>공통 문구 수정</strong>
      <p className="muted">입력한 문구를 모든 완성형 지면의 이미지 문구에 적용합니다. 잠겼거나 지면에서 따로 수정한 문구는 바꾸지 않습니다.</p>
      <div className="row">
        <input style={{ flex: 1, minWidth: 200 }} value={text} maxLength={300} onChange={(e) => setText(e.target.value)} placeholder="모든 지면에 쓸 문구" />
        <button className="primary" disabled={!text.trim()} onClick={apply}>모든 지면에 적용</button>
      </div>
      {err && <div className="notice err">{err}</div>}
      {res && (
        <div className="notice ok">
          {res.applied.length}개 지면에 적용했습니다.
          {res.skipped.map((s, i) => <div key={i}>건너뜀 · {s.name}: {s.reason}</div>)}
        </div>
      )}
    </div>
  )
}

function ImageBlock({ g, row, busy, err, canMake, onMake, designId, onEdited }: {
  g: CopyGroup; row?: ImgRow; busy: boolean; err?: string; canMake: boolean
  onMake: (presetId: number, regenerate: boolean) => void; designId: number; onEdited: () => void
}) {
  const comp = row?.composite
  const sep = g.content_kind === '분리 자산형'
  const [editing, setEditing] = useState(false)
  return (
    <div className="imgblock">
      <div className="row">
        <button className="primary" disabled={busy || !canMake} onClick={() => onMake(g.preset_id, !row?.background)}>
          {comp ? (sep ? '이미지 다시 만들기(새로 생성)' : '문구 다시 합성(배경 유지)') : '이미지 생성'}
        </button>
        {comp && !sep && <button disabled={busy || !canMake} onClick={() => onMake(g.preset_id, true)}>배경 새로 생성</button>}
        {comp && !sep && <button onClick={() => setEditing((v) => !v)}>{editing ? '편집 닫기' : '편집'}</button>}
        {busy && <span className="muted">이미지 생성 중… 1~2분 걸릴 수 있습니다. 비용이 발생합니다.</span>}
        {!canMake && <span className="muted">완성형 지면은 사용할 카피를 먼저 선택해 주세요.</span>}
      </div>
      {sep && <p className="muted">분리 자산형: 텍스트 없는 이미지만 만들고, 로고·카피는 내보내기에서 별도 파일로 제공합니다.</p>}
      {err && <div className="notice err">{err}</div>}
      {editing && <EditPanel designId={designId} presetId={g.preset_id} onClose={() => setEditing(false)} onSaved={onEdited} />}
      {comp && (
        <div className="preview">
          <img src={`/api/generated/${comp.id}/file?v=${comp.id}`} alt={`${g.name} 미리보기`} />
          <div className="muted">
            {comp.width}×{comp.height}px{comp.layout.template ? ` · ${comp.layout.template}` : ''}
            {comp.layout.margin_source ? ` · ${comp.layout.margin_source}` : ''}
          </div>
          {(comp.layout.warnings ?? []).map((w, i) => <div key={i} className="badge b-warn">{w}</div>)}
        </div>
      )}
    </div>
  )
}

type IconItem = {
  id: number; position: number; name: string; width: number; height: number
  style: { stroke_width: number; corner: string; fill: string; color: string; bbox?: number[] }
  violations: { detail: string }[]
}

function IconPanel({ designId }: { designId: number }) {
  const [items, setItems] = useState<IconItem[]>([])
  const [busy, setBusy] = useState(false)
  const [one, setOne] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [ver, setVer] = useState(0)   // 이미지 캐시 무효화

  useEffect(() => {
    fetch(`/api/designs/${designId}/icons`).then((r) => r.json()).then((l) => Array.isArray(l) && setItems(l))
  }, [designId])

  const generate = async () => {
    setBusy(true); setErr(null); setNote(null)
    try {
      const j = await post(`/api/designs/${designId}/icons`, {})
      setItems(j.icons); setVer((v) => v + 1)
      if (!j.complete) setNote(`목표 ${j.requested}개 중 ${j.made}개만 만들어졌습니다. 다시 생성해 주세요.`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const redraw = async (id: number) => {
    setOne(id); setErr(null)
    try {
      const a = await post(`/api/designs/${designId}/icons/${id}/regenerate`, {})
      setItems((l) => l.map((x) => (x.id === id ? a : x))); setVer((v) => v + 1)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setOne(null)
    }
  }

  const st = items[0]?.style
  return (
    <div className="copy">
      <div className="row">
        <button className="primary" disabled={busy} onClick={generate}>{items.length ? '아이콘 세트 다시 생성' : '아이콘 세트 생성'}</button>
        {busy && <span className="muted">기능을 정하고 아이콘을 그리는 중…</span>}
      </div>
      <p className="muted">기능명 10개는 상품 설명을 바탕으로 AI가 정하며 추가·삭제할 수 없습니다. 선 굵기·모서리·색·여백은 브랜드 값으로 모든 아이콘에 동일하게 적용됩니다.</p>
      {err && <div className="notice err">{err}</div>}
      {note && <div className="notice err">{note}</div>}
      {st && (
        <p className="muted">
          적용 스타일 — 선 굵기 {st.stroke_width} · 모서리 {st.corner} · {st.fill} · 색 {st.color} · 크기 {items[0].width}×{items[0].height}px
        </p>
      )}
      <div className="icongrid">
        {items.map((a) => (
          <figure key={a.id} className="iconcell">
            <img src={`/api/icons/${a.id}/png?v=${ver}`} alt={a.name} />
            <figcaption>{a.position}. {a.name}</figcaption>
            {a.violations.map((v, i) => <span key={i} className="badge b-need">{v.detail}</span>)}
            <div className="row">
              <button disabled={one === a.id} onClick={() => redraw(a.id)}>{one === a.id ? '그리는 중…' : '다시 그리기'}</button>
            </div>
            <div className="muted"><a href={`/api/icons/${a.id}/svg`} target="_blank" rel="noreferrer">SVG</a> · <a href={`/api/icons/${a.id}/png`} target="_blank" rel="noreferrer">PNG</a></div>
          </figure>
        ))}
      </div>
    </div>
  )
}

export default function DesignPage() {
  const [brands, setBrands] = useState<{ id: number; name: string }[]>([])
  const [platforms, setPlatforms] = useState<PlatformLite[]>([])
  const [designs, setDesigns] = useState<Design[]>([])
  const [brandId, setBrandId] = useState<number | ''>('')
  const [kind, setKind] = useState<'광고 이미지' | '아이콘 세트'>('광고 이미지')
  const [picked, setPicked] = useState<number[]>([])
  const [purpose, setPurpose] = useState<'웹용' | '사용자 지정'>('웹용')
  const [size, setSize] = useState({ width: '', height: '' })
  const [brief, setBrief] = useState({ product_name: '', description: '', key_message: '', target: '' })
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const loadDesigns = useCallback(() => fetch('/api/designs').then((r) => r.json()).then(setDesigns), [])

  useEffect(() => {
    fetch('/api/brands').then((r) => r.json()).then((b) => { setBrands(b); if (b.length) setBrandId(b[0].id) })
    fetch('/api/presets').then((r) => r.json()).then(setPlatforms)
    loadDesigns()
  }, [loadDesigns])

  const toggle = (id: number) => setPicked((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  const submit = async () => {
    setBusy(true); setMsg(null)
    try {
      await post('/api/designs', {
        brand_id: brandId, kind, brief,
        preset_version_ids: kind === '광고 이미지' ? picked : [],
        icon: kind === '아이콘 세트'
          ? { purpose, width: purpose === '사용자 지정' ? Number(size.width) : null, height: purpose === '사용자 지정' ? Number(size.height) : null }
          : null,
      })
      setMsg({ ok: true, text: '시안을 만들었습니다.' })
      setBrief({ product_name: '', description: '', key_message: '', target: '' })
      setPicked([])
      await loadDesigns()
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  if (brands.length === 0)
    return <main className="page"><h1>시안</h1><p className="muted">먼저 '브랜드' 탭에서 브랜드를 등록해 주세요.</p></main>

  return (
    <main className="page wide">
      <h1>시안 만들기</h1>

      <section className="card">
        <header><h3>1. 브랜드 · 종류</h3></header>
        <div className="row">
          <label>브랜드
            <select value={brandId} onChange={(e) => setBrandId(Number(e.target.value))}>
              {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </label>
          <label>시안 종류
            <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
              <option>광고 이미지</option><option>아이콘 세트</option>
            </select>
          </label>
        </div>
        <p className="muted">시안은 저장 시점의 브랜드 현재 버전에 연결됩니다.</p>
      </section>

      {kind === '광고 이미지' ? (
        <section className="card">
          <header><h3>2. 매체·지면 선택 (여러 개 가능)</h3></header>
          {platforms.map((pl) => (
            <div key={pl.name}>
              <h4>{pl.name}</h4>
              {pl.media_only ? <p className="muted">확인 필요 · 검수 불가 — 선택할 수 없습니다.</p> :
                pl.ad_types.flatMap((a) => a.presets).map((p) => (
                  <label key={p.id} className="preset">
                    <input type="checkbox" checked={picked.includes(p.id)} onChange={() => toggle(p.id)} />
                    <span style={{ flex: 1 }}>{p.name} <span className="muted">{p.production_width}×{p.production_height}</span></span>
                    {p.status !== '확인됨' && <span className="badge b-warn">{p.status}</span>}
                  </label>
                ))}
            </div>
          ))}
        </section>
      ) : (
        <section className="card">
          <header><h3>2. 아이콘 용도</h3></header>
          <div className="row">
            <label><input type="radio" checked={purpose === '웹용'} onChange={() => setPurpose('웹용')} /> 웹용</label>
            <label><input type="radio" checked={purpose === '사용자 지정'} onChange={() => setPurpose('사용자 지정')} /> 사용자 지정</label>
          </div>
          {purpose === '웹용'
            ? <p className="muted">웹용 아이콘의 기본 크기는 아이콘 생성 단계에서 확정합니다.</p>
            : <div className="row">
                <label>가로(px) <input type="number" min={16} max={4096} value={size.width} onChange={(e) => setSize({ ...size, width: e.target.value })} /></label>
                <label>세로(px) <input type="number" min={16} max={4096} value={size.height} onChange={(e) => setSize({ ...size, height: e.target.value })} /></label>
              </div>}
        </section>
      )}

      <section className="card">
        <header><h3>3. 상품 · 메시지</h3></header>
        <label>상품/서비스명 (필수)
          <input value={brief.product_name} maxLength={100} onChange={(e) => setBrief({ ...brief, product_name: e.target.value })} />
        </label>
        <label>상품 설명
          <textarea rows={3} value={brief.description} maxLength={1000} onChange={(e) => setBrief({ ...brief, description: e.target.value })} />
        </label>
        <label>핵심 메시지
          <input value={brief.key_message} maxLength={200} onChange={(e) => setBrief({ ...brief, key_message: e.target.value })} />
        </label>
        <label>타깃 고객
          <input value={brief.target} maxLength={300} onChange={(e) => setBrief({ ...brief, target: e.target.value })} />
        </label>
      </section>

      {msg && <div className={msg.ok ? 'notice ok' : 'notice err'}>{msg.text}</div>}
      <div className="actions">
        <button className="primary" disabled={busy || !brandId || !brief.product_name.trim()} onClick={submit}>시안 만들기</button>
      </div>

      <h2>시안 목록</h2>
      {designs.length === 0 && <p className="muted">만든 시안이 없습니다.</p>}
      {designs.map((d) => (
        <section className="card" key={d.id}>
          <header>
            <h3>#{d.id} {d.brief.product_name} <span className="muted">· {d.kind}</span></h3>
            <span className="badge b-need">{d.status}</span>
          </header>
          <p>
            브랜드 {d.brand.name} (버전 {d.brand.version_no})
            {d.brand_outdated && <span className="badge b-warn" style={{ marginLeft: 6 }}>브랜드가 버전 {d.brand.current_version_no}로 변경됨</span>}
          </p>
          {d.kind === '광고 이미지'
            ? <ul className="plain">{d.presets.map((p) => <li key={p.id}>{p.platform} · {p.name} — {p.width}×{p.height}px</li>)}</ul>
            : <p>용도: {d.brief.icon?.purpose} ({d.brief.icon?.width ? `${d.brief.icon.width}×${d.brief.icon.height}` : '256×256'}px)</p>}
          {d.brief.key_message && <p className="muted">핵심 메시지: {d.brief.key_message}</p>}
          {d.kind === '광고 이미지' && <CopyPanel designId={d.id} />}
          {d.kind === '아이콘 세트' && <IconPanel designId={d.id} />}
          <ReviewPanel designId={d.id} />
        </section>
      ))}
    </main>
  )
}
