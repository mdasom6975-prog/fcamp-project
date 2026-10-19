import { useCallback, useEffect, useRef, useState } from 'react'
import EditPanel from './EditPanel'
import ReviewPanel from './ReviewPanel'

type PresetLite = {
  id: number; name: string; status: string; production_width: number | null; production_height: number | null
  ratio?: string | null; content_kind?: string; checked_at?: string | null; version_no?: number
  conditions?: { name: string; value: string | null }[]
  text_limits?: { field: string; max_chars?: number | null; level: string }[]
}
type PlatformLite = { name: string; media_only: boolean; ad_types: { name: string; presets: PresetLite[] }[] }
type Design = {
  id: number; kind: string; status: string
  brief: { product_name: string; description: string; key_message: string; target: string; product_image_asset_id?: number | null; icon?: { purpose: string; width: number | null; height: number | null } }
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
type ImgUsage = { date: string; provider?: string; used: number; limit: number; remaining: number; exhausted: boolean }
type ImgRow = { preset_id: number; background: ImgOut; composite: ImgOut }
type Limit = { field: string; min: number | null; max: number | null; level: string | null } | null
type CopyGroup = {
  preset_id: number; platform: string; name: string; batch_no: number | null
  content_kind: string; width: number | null; height: number | null
  limits: { headline: Limit; body: Limit; cta: Limit; skipped: { field: string; reason: string }[] }
  variants: Variant[]
}

const LETTERS = ['A', 'B', 'C', 'D', 'E']

function CopyPanel({ designId, brandName, versionNo }: { designId: number; brandName: string; versionNo: number | null }) {
  const [groups, setGroups] = useState<CopyGroup[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [imgs, setImgs] = useState<Record<number, ImgRow>>({})
  const [imgBusy, setImgBusy] = useState<number | null>(null)
  const [usage, setUsage] = useState<ImgUsage | null>(null)
  const [imgTab, setImgTab] = useState<number | null>(null)
  const [bgBusy, setBgBusy] = useState(false)
  const [bgMsg, setBgMsg] = useState<string | null>(null)
  const [imgErr, setImgErr] = useState<Record<number, string>>({})
  const [tab, setTab] = useState<'ads' | 'copy'>('ads')
  const [picking, setPicking] = useState(false)

  const loadImgs = useCallback(() =>
    fetch(`/api/designs/${designId}/images`).then((r) => r.json()).then((rows: ImgRow[]) => {
      if (Array.isArray(rows)) setImgs(Object.fromEntries(rows.map((x) => [x.preset_id, x])))
    }), [designId])

  const loadUsage = useCallback(() =>
    fetch('/api/image-usage').then((r) => r.json()).then((u: ImgUsage) => u && typeof u.used === 'number' && setUsage(u))
      .catch(() => {}), [])

  useEffect(() => {
    loadUsage()
    fetch(`/api/designs/${designId}/copy`).then((r) => r.json()).then((g) => Array.isArray(g) && setGroups(g))
    loadImgs()
  }, [designId, loadImgs])

  const choose = async (presetId: number, variantId: number) => {
    await post(`/api/designs/${designId}/copy/${variantId}/select`, {})
    setGroups((gs) => gs.map((g) => g.preset_id !== presetId ? g
      : { ...g, variants: g.variants.map((v) => ({ ...v, selected: v.id === variantId })) }))
  }

  // 시안 A/B/C 선택: 모든 지면에서 같은 번호의 카피를 고른다
  const chooseNo = async (no: number) => {
    setPicking(true); setErr(null)
    try {
      for (const g of groups) {
        const v = g.variants.find((x) => x.variant_no === no)
        if (v && !v.selected) await choose(g.preset_id, v.id)
      }
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setPicking(false)
    }
  }

  const makeOne = async (presetId: number, regenerate: boolean) => {
    await post(`/api/designs/${designId}/presets/${presetId}/image`, { regenerate_background: regenerate })
  }

  // 배경은 시안당 1회만 생성하고, 아직 합성이 없는 나머지 매체도 같은 배경을 각 규격 크기로 맞춰 이어서 합성한다(추가 생성 비용 없음).
  const makeImage = async (presetId: number, regenerate: boolean) => {
    setImgBusy(presetId); setImgErr({})
    try {
      await makeOne(presetId, regenerate)
      for (const g of withCopy) {
        if (g.preset_id === presetId || imgs[g.preset_id]?.composite) continue
        if (g.content_kind === '완성형' && !g.variants.some((v) => v.selected)) continue
        try {
          await makeOne(g.preset_id, false)
        } catch (e) {
          setImgErr((x) => ({ ...x, [g.preset_id]: (e as Error).message }))
        }
      }
      await loadImgs()
    } catch (e) {
      setImgErr((x) => ({ ...x, [presetId]: (e as Error).message }))
    } finally {
      loadUsage()
      setImgBusy(null)
    }
  }

  // 배경은 모든 매체가 공유하므로 한 번만 새로 만들고, 이미 합성된 매체는 새 배경으로 다시 합성한다(추가 생성 비용 없음).
  const regenBackground = async () => {
    setBgBusy(true); setBgMsg(null)
    try {
      await post(`/api/designs/${designId}/background/regenerate`, {})
      const failed: string[] = []
      for (const g of withCopy) {
        if (!imgs[g.preset_id]?.composite) continue
        try {
          await post(`/api/designs/${designId}/presets/${g.preset_id}/image`, { regenerate_background: false })
        } catch (e) {
          failed.push(`${g.name}: ${(e as Error).message}`)
        }
      }
      if (failed.length) setBgMsg(`새 배경은 만들었지만 일부 매체는 다시 합성하지 못했습니다 — ${failed.join(' / ')}`)
      await loadImgs()
    } catch (e) {
      setBgMsg((e as Error).message)
    } finally {
      loadUsage()
      setBgBusy(false)
    }
  }

  // 이미지 생성 API 테스트용: 생성 결과(배경·합성·편집 내용·검수)만 지우고 브랜드·규격·입력·카피는 유지한다
  const resetGenerated = async () => {
    if (!window.confirm('이 시안의 생성 이미지, 편집 내용, 검수 결과를 모두 지웁니다. (브랜드·규격·입력 내용·카피는 유지)\n계속할까요?')) return
    setBgBusy(true); setBgMsg(null)
    try {
      const j = await post(`/api/designs/${designId}/reset-generated`, { include_copy: false })
      setBgMsg(`생성 이미지 ${j.deleted_images}건을 초기화했습니다.`)
      await loadImgs()
    } catch (e) {
      setBgMsg((e as Error).message)
    } finally {
      setBgBusy(false)
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
  const withCopy = groups.filter((g) => g.variants.length > 0)
  const has = withCopy.length > 0
  const preview = withCopy[0]
  const count = preview ? preview.variants.length : 0
  const selected = preview?.variants.find((v) => v.selected) ?? null
  const letter = (v: Variant) => LETTERS[v.variant_no - 1] ?? String(v.variant_no)
  const goEdit = () => document.getElementById('sec-images')?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  return (
    <div className="copy gen">
      <div className="gen-top">
        <div className="gen-tabs" role="tablist">
          <button role="tab" aria-selected={tab === 'ads'} className={tab === 'ads' ? 'on' : ''} onClick={() => setTab('ads')}>
            광고 시안 {count}개
          </button>
          <button role="tab" aria-selected={tab === 'copy'} className={tab === 'copy' ? 'on' : ''} onClick={() => setTab('copy')}>카피 후보</button>
        </div>
        <div className="gen-actions">
          {busy && <span className="muted">생성 중… 지면 수에 따라 수십 초 걸릴 수 있습니다.</span>}
          <button className="primary" disabled={busy} onClick={generate}>{has ? '카피 다시 생성' : '카피 생성'}</button>
        </div>
      </div>
      {err && <div className="notice err">{err}</div>}

      {!has && <p className="muted gen-empty">카피 생성을 누르면 선택한 규격의 글자 수 제한에 맞춰 시안 문구를 만듭니다.</p>}

      {has && tab === 'ads' && (
        <>
          <p className="muted gen-note">
            미리보기는 <strong>{preview.platform} {preview.name}</strong> 기준이며, 고른 시안은 선택한 규격 {withCopy.length}개에 각 지면의 글자 수 제한에 맞춰 적용됩니다.
          </p>
          <div className="ad-grid">
            {preview.variants.map((v) => {
              const reqOk = !v.violations.some((x) => x.type === 'required_missing')
              const banOk = !v.violations.some((x) => x.type === 'forbidden')
              const others = v.violations.filter((x) => x.type !== 'required_missing' && x.type !== 'forbidden')
              return (
                <article key={v.id} className={`ad-card ${v.selected ? 'on' : ''}`}>
                  <h4>시안 {letter(v)}</h4>
                  <div className="ad-preview">
                    <span className="ad-brand">{brandName}</span>
                    <div className="ad-headline">{v.headline}</div>
                    {v.body && <div className="ad-body">{v.body}</div>}
                    {v.cta && <span className="ad-cta">{v.cta}</span>}
                  </div>
                  <ul className="ad-checks">
                    <li className={reqOk ? 'ok' : 'ng'}>{reqOk ? '✓ 필수 표현 포함' : '✕ 필수 표현 누락'}</li>
                    <li className={banOk ? 'ok' : 'ng'}>{banOk ? '✓ 금지 표현 없음' : '✕ 금지 표현 포함'}</li>
                    {others.map((x, i) => <li key={i} className="ng">✕ {x.field}: {x.detail}</li>)}
                  </ul>
                  {v.selected
                    ? <div className="ad-picked">✓ 선택됨</div>
                    : <button disabled={picking} onClick={() => chooseNo(v.variant_no)}>이 시안 선택</button>}
                </article>
              )
            })}
          </div>

          {groups.some((g) => g.content_kind === '완성형' && imgs[g.preset_id]?.composite) && (
            <CommonText designId={designId} onApplied={loadImgs} />
          )}

          <div id="sec-images" className="gen-images">
            <h4 className="gen-h"><span className="step-no">④</span> 이미지 생성·편집</h4>
            {usage && (
              <p className={usage.exhausted ? 'notice err' : 'muted'}>
                오늘 이미지 생성{usage.provider ? `(${usage.provider})` : ''} {usage.used}/{usage.limit}회 사용 · 남은 횟수 약 {usage.remaining}회
                {usage.exhausted ? ' · 오늘 설정한 생성 한도를 모두 사용했습니다' : ''}
                {' '}(호출 횟수 기준, UTC 0시 초기화) · 배경은 시안당 1회 생성 후 매체별 크기로 자동 조정됩니다.
              </p>
            )}
            <div className="row">
              <button disabled={bgBusy || !Object.values(imgs).some((r) => r.background)} onClick={regenBackground}>
                배경 새로 생성 (모든 매체에 적용)
              </button>
              <button disabled={bgBusy || imgBusy !== null} onClick={resetGenerated}>시안 이미지 초기화</button>
              {bgBusy && <span className="muted">배경 생성 후 합성된 매체를 다시 합성하는 중… 1~2분 걸릴 수 있습니다.</span>}
            </div>
            {bgMsg && <div className="notice err">{bgMsg}</div>}
            {(() => {
              const cur = withCopy.find((x) => x.preset_id === imgTab) ?? withCopy[0]
              if (!cur) return null
              return (
                <>
                  <div className="gen-tabs" role="tablist">
                    {withCopy.map((x) => (
                      <button key={x.preset_id} role="tab" aria-selected={x.preset_id === cur.preset_id}
                        className={x.preset_id === cur.preset_id ? 'on' : ''} onClick={() => setImgTab(x.preset_id)}>
                        {x.platform} · {x.name}{imgs[x.preset_id]?.composite ? ' ✓' : ''}
                      </button>
                    ))}
                  </div>
                  <div key={cur.preset_id} className="copy-group">
                    <ImageBlock g={cur} row={imgs[cur.preset_id]} busy={imgBusy === cur.preset_id} err={imgErr[cur.preset_id]}
                      canMake={cur.content_kind !== '완성형' || cur.variants.some((v) => v.selected)} onMake={makeImage}
                      designId={designId} onEdited={loadImgs} />
                  </div>
                </>
              )
            })()}
          </div>

          <IconPanel designId={designId} versionNo={versionNo} ask
            ready={groups.some((g) => imgs[g.preset_id]?.composite)} />

          <div className="pick-bar">
            <span className="pick-text">
              {selected ? <>선택: <strong>시안 {letter(selected)}</strong> · {selected.headline}</> : <span className="muted">사용할 시안을 선택해 주세요.</span>}
            </span>
            <button className="primary" disabled={!selected} onClick={goEdit}>편집하러 가기 →</button>
          </div>
        </>
      )}

      {has && tab === 'copy' && withCopy.map((g) => (
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
                시안 {letter(v)}{v.attempts > 1 ? ' · 규칙 위반으로 1회 재생성됨' : ''}
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
      {sep && <p className="muted">분리 자산형: 텍스트 없는 이미지만 만들고, 로고·카피는 내보내기에서 별도 파일로 제공합니다.</p>}
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
      <div className="row">
        <button className="primary" disabled={busy || !canMake} onClick={() => onMake(g.preset_id, false)}>
          {comp ? (sep ? '이미지 다시 만들기(새로 생성)' : '문구 다시 합성(배경 유지)') : '이미지 생성'}
        </button>
        {comp && !sep && <button onClick={() => setEditing((v) => !v)}>{editing ? '편집 닫기' : '편집'}</button>}
        {busy && <span className="muted">이미지 생성 중… 1~2분 걸릴 수 있습니다. 비용이 발생합니다.</span>}
        {!canMake && <span className="muted">완성형 지면은 사용할 카피를 먼저 선택해 주세요.</span>}
      </div>
      {err && <div className="notice err">{err}</div>}
      {editing && <EditPanel designId={designId} presetId={g.preset_id} onClose={() => setEditing(false)} onSaved={onEdited} />}
    </div>
  )
}

type IconItem = {
  id: number; position: number; name: string; concept?: string; width: number; height: number
  render: '3d' | 'svg'; has_svg: boolean; has_source: boolean
  style: { render?: string; stroke_width?: number; corner: string; fill?: string; color?: string; colors?: string[]; model?: string; quality?: string; bbox?: number[] }
  violations: { detail: string }[]
}

function IconPanel({ designId, versionNo, ready, ask }: { designId: number; versionNo: number | null; ready: boolean; ask: boolean }) {
  const [items, setItems] = useState<IconItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [want, setWant] = useState(false)      // 아이콘도 생성할지 여부
  const [busy, setBusy] = useState(false)
  const [one, setOne] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [ver, setVer] = useState(0)   // 이미지 캐시 무효화

  useEffect(() => {
    fetch(`/api/designs/${designId}/icons`).then((r) => r.json()).then((l) => { if (Array.isArray(l)) setItems(l); setLoaded(true) })
  }, [designId])

  const generate = async () => {
    setBusy(true); setErr(null); setNote(null)
    try {
      const j = await post(`/api/designs/${designId}/icons`, {})
      setItems(j.icons); setVer((v) => v + 1)
      if (!j.complete) setNote(`목표 ${j.requested}개 중 ${j.made}개만 만들어졌습니다. 부족한 아이콘은 개별로 다시 그릴 수 있습니다.`)
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

  if (!loaded) return null
  const st = items[0]?.style
  const vLabel = versionNo ? `브랜드 규칙 v${versionNo}` : '이 브랜드 규칙 버전'

  // 아직 세트가 없을 때: 광고 이미지가 있어야 생성 여부를 묻는다
  if (items.length === 0) {
    if (!ready) {
      return (
        <div className="imgblock icon-step">
          <strong>아이콘 세트</strong>
          <p className="muted">광고 이미지를 먼저 생성하면 {vLabel}에 맞춘 아이콘 세트도 만들 수 있습니다. 아이콘 세트는 규칙 버전마다 한 세트만 만들어집니다.</p>
        </div>
      )
    }
    return (
      <div className="imgblock icon-step">
        <strong>아이콘 세트</strong>
        <label className="icon-ask">
          <input type="checkbox" checked={want} disabled={busy} onChange={(e) => setWant(e.target.checked)} />
          {vLabel}의 아이콘 세트도 함께 만들기
        </label>
        <p className="muted">
          브랜드에 어울리는 3D 아이콘 세트입니다. 기능명 10개는 상품 설명을 바탕으로 AI가 정하고, 아이콘마다 투명 배경 PNG 를 렌더합니다.
          재질·광원은 브랜드 이미지 톤, 색은 주조·보조·강조색, 모서리는 아이콘 스타일 값으로 모든 아이콘에 동일하게 적용됩니다.
          이 규칙 버전의 아이콘 세트는 한 번만 만들 수 있고, 이후에는 개별 아이콘만 다시 그릴 수 있습니다. 아이콘 1개당 이미지 1장 비용이 발생합니다.
        </p>
        {want && (
          <div className="row">
            <button className="primary" disabled={busy} onClick={generate}>아이콘 세트 생성</button>
            {busy && <span className="muted">기능을 정하고 아이콘 10개를 렌더하는 중… 2~3분 걸릴 수 있습니다.</span>}
          </div>
        )}
        {err && <div className="notice err">{err}</div>}
      </div>
    )
  }

  return (
    <div className="imgblock icon-step">
      <strong>아이콘 세트 · {vLabel}</strong>
      <p className="muted">
        이 규칙 버전의 아이콘 세트는 한 세트만 사용합니다. 마음에 들지 않는 아이콘은 개별로 다시 그릴 수 있습니다.
        {ask ? '' : ' 기능명 10개는 상품 설명을 바탕으로 AI가 정했으며 추가·삭제할 수 없습니다.'}
      </p>
      {err && <div className="notice err">{err}</div>}
      {note && <div className="notice err">{note}</div>}
      {st && items[0].render === '3d' ? (
        <p className="muted">
          적용 스타일 — 3D 렌더({st.model} {st.quality}) · 모서리 {st.corner} · 색 {(st.colors || []).join(', ')} · 투명 배경 · 크기 {items[0].width}×{items[0].height}px (원본 1024)
        </p>
      ) : st && (
        <p className="muted">
          적용 스타일 — 선 굵기 {st.stroke_width} · 모서리 {st.corner} · {st.fill} · 색 {st.color} · 크기 {items[0].width}×{items[0].height}px
        </p>
      )}
      <div className="icongrid">
        {items.map((a) => (
          <figure key={a.id} className="iconcell">
            <img src={`/api/icons/${a.id}/png?v=${ver}`} alt={a.name} title={a.concept || a.name} />
            <figcaption>{a.position}. {a.name}</figcaption>
            {a.concept && <div className="muted icon-concept">{a.concept}</div>}
            {a.violations.map((v, i) => <span key={i} className="badge b-need">{v.detail}</span>)}
            <div className="row">
              <button disabled={one === a.id} onClick={() => redraw(a.id)}>{one === a.id ? '그리는 중…' : '다시 그리기'}</button>
            </div>
            <div className="muted">
              {a.has_svg && <><a href={`/api/icons/${a.id}/svg`} target="_blank" rel="noreferrer">SVG</a> · </>}
              <a href={`/api/icons/${a.id}/png`} target="_blank" rel="noreferrer">PNG</a>
              {a.has_source && <> · <a href={`/api/icons/${a.id}/source`} target="_blank" rel="noreferrer">원본</a></>}
            </div>
          </figure>
        ))}
      </div>
    </div>
  )
}

const fmtDate = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
const campaignName = (d: Design) => d.brief.product_name
const campaignState = (d: Design) =>
  d.kind === '광고 이미지' && d.presets.length === 0 ? '규격 선택 중' : d.status
const campaignMeta = (d: Design) =>
  `${d.kind === '광고 이미지' ? `지면 ${d.presets.length}개` : '아이콘 세트'} · ${campaignState(d)} · ${fmtDate(d.created_at)}`

type Brief = { product_name: string; description: string; key_message: string; target: string; product_image_asset_id: number | null }

type BrandLite = { id: number; name: string; description?: string; current_version_no: number | null }

export default function DesignPage({ workBrandId, onWorkBrand, onGoBrand }: {
  workBrandId: number | null; onWorkBrand: (id: number | null) => void; onGoBrand?: () => void
}) {
  const [brands, setBrands] = useState<BrandLite[]>([])
  const [platforms, setPlatforms] = useState<PlatformLite[]>([])
  const [designs, setDesigns] = useState<Design[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)
  // 새 캠페인
  const [newName, setNewName] = useState('')
  // 입력 수정
  const [editing, setEditing] = useState(false)
  const [brief, setBrief] = useState<Brief>({ product_name: '', description: '', key_message: '', target: '', product_image_asset_id: null })
  const [picked, setPicked] = useState<number[]>([])
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [brandPanel, setBrandPanel] = useState(false)
  const productInput = useRef<HTMLInputElement>(null)
  const [hasCopy, setHasCopy] = useState<boolean | null>(null)   // 이 캠페인에 생성된 카피가 있는지
  const [copyKey, setCopyKey] = useState(0)                       // 카피 영역 다시 불러오기
  const [platTab, setPlatTab] = useState<string | null>(null)
  const [rules, setRules] = useState<{ req: number; ban: number } | null>(null)
  const [moveTo, setMoveTo] = useState<number | ''>('')

  const work = brands.find((b) => b.id === workBrandId) ?? null
  const workId = work?.id ?? null

  const loadDesigns = useCallback(() => {
    if (workId === null) { setDesigns([]); return Promise.resolve([] as Design[]) }
    return fetch(`/api/designs?brand_id=${workId}`).then((r) => r.json()).then((l: Design[]) => { setDesigns(l); return l })
  }, [workId])

  const loadBrands = useCallback(() =>
    fetch('/api/brands').then((r) => r.json()).then((b: BrandLite[]) => { setBrands(b); return b }), [])

  useEffect(() => {
    loadBrands().then((b) => {
      // 브랜드가 1개면 선택 화면을 건너뛰고 자동 선택, 저장된 선택이 사라졌으면 해제
      if (b.length === 1 && workBrandId !== b[0].id) onWorkBrand(b[0].id)
      else if (workBrandId !== null && !b.some((x) => x.id === workBrandId)) onWorkBrand(null)
      setLoaded(true)
    })
    fetch('/api/presets').then((r) => r.json()).then(setPlatforms)
  }, [loadBrands, workBrandId, onWorkBrand])

  useEffect(() => { setActiveId(null); setEditing(false); setBrandPanel(false); loadDesigns() }, [loadDesigns])

  const active = designs.find((d) => d.id === activeId) ?? null

  const openCampaign = (d: Design) => {
    setActiveId(d.id); setMsg(null)
    setBrief({ product_name: d.brief.product_name, description: d.brief.description ?? '', key_message: d.brief.key_message ?? '', target: d.brief.target ?? '', product_image_asset_id: d.brief.product_image_asset_id ?? null })
    setPicked(d.presets.map((p) => p.id))
    setEditing(false); setHasCopy(null); setRules(null)
    if (d.kind === '광고 이미지') {
      fetch(`/api/designs/${d.id}/copy`).then((r) => r.json())
        .then((g) => setHasCopy(Array.isArray(g) && g.some((x: { variants: unknown[] }) => x.variants.length > 0)))
        .catch(() => setHasCopy(false))
    } else setHasCopy(true)
    // 이 캠페인이 쓰는 브랜드 규칙 버전의 필수·금지 표현 수
    fetch(`/api/brands/${d.brand.id}`).then((r) => r.json()).then((b: { versions: { id: number; version_no: number }[] }) => {
      const vid = b.versions.find((v) => v.version_no === d.brand.version_no)?.id
      return fetch(`/api/brands/${d.brand.id}${vid ? `?version_id=${vid}` : ''}`).then((r) => r.json())
    }).then((b: { version: { required_phrases: string[]; forbidden_phrases: string[] } }) =>
      setRules({ req: b.version.required_phrases.length, ban: b.version.forbidden_phrases.length })).catch(() => setRules(null))
  }
  const backToList = () => { setActiveId(null); setEditing(false); setMsg(null) }
  const toggle = (id: number) => setPicked((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  const createCampaign = async () => {
    setBusy(true); setMsg(null)
    try {
      const d: Design = await post('/api/designs', {
        brand_id: workId, kind: '광고 이미지',
        brief: { product_name: newName.trim(), description: '', key_message: '', target: '' },
        preset_version_ids: [],
      })
      setNewName('')
      await loadDesigns()
      openCampaign(d)
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const patchDesign = async (id: number, body: unknown) => {
    const r = await fetch(`/api/designs/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const j = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(Array.isArray(j.detail) ? j.detail.map((x: { msg: string }) => x.msg).join(', ') : j.detail || '저장에 실패했습니다')
    return j as Design
  }

  // 브랜드 규칙 최신 버전 적용(같은 브랜드) 또는 브랜드 변경(다른 브랜드)
  const applyBrand = async (targetId: number) => {
    if (!active) return
    setBusy(true); setMsg(null)
    try {
      await patchDesign(active.id, { brand_id: targetId })
      const moved = targetId !== active.brand.id
      const name = brands.find((b) => b.id === targetId)?.name ?? ''
      const notice = `${moved ? `브랜드를 '${name}'(으)로 변경했습니다.` : '브랜드 규칙을 최신 버전으로 적용했습니다.'} 이미 만든 카피·이미지는 이전 규칙 기준이므로 다시 생성해 주세요.`
      await loadBrands()
      if (moved) { setMsg({ ok: true, text: notice }); setActiveId(null); onWorkBrand(targetId) }   // 목록이 새 브랜드 기준으로 바뀐다
      else { await loadDesigns(); setBrandPanel(false); setMsg({ ok: true, text: notice }) }
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const saveInput = async () => {
    if (!active) return
    setBusy(true); setMsg(null)
    try {
      await patchDesign(active.id, { brief, preset_version_ids: active.kind === '광고 이미지' ? picked : null })
      await loadDesigns()
      setEditing(false)
      setMsg({ ok: true, text: '입력을 저장했습니다.' })
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const generateCampaign = async () => {
    if (!active) return
    setBusy(true); setMsg(null)
    try {
      await patchDesign(active.id, { brief, preset_version_ids: picked })
      await loadDesigns()
      const j = await post(`/api/designs/${active.id}/copy`, {})
      setHasCopy(true); setEditing(false); setCopyKey((k) => k + 1)
      if (j.failures?.length) setMsg({ ok: false, text: `일부 지면의 생성에 실패했습니다: ${j.failures.join(', ')}` })
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const uploadProduct = async (file: File | undefined, input: HTMLInputElement) => {
    input.value = ''
    if (!file) return
    if (file.size > 200 * 1024) { setMsg({ ok: false, text: '제품 이미지는 200KB 이하여야 합니다' }); return }
    try {
      const f = new FormData()
      f.append('kind', 'product'); f.append('file', file)
      const r = await fetch('/api/assets', { method: 'POST', body: f })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.detail || '업로드에 실패했습니다')
      setBrief((b) => ({ ...b, product_image_asset_id: j.id })); setMsg(null)
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    }
  }

  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  if (brands.length === 0 && loaded)
    return <main className="page"><h1>규격·메시지·시안</h1><p className="muted">먼저 '브랜드' 탭에서 브랜드를 등록해 주세요.</p></main>

  // 브랜드가 2개 이상이고 아직 선택하지 않았으면 브랜드부터 고른다
  if (!work && loaded)
    return (
      <main className="page wide">
        <h1>규격·메시지·시안</h1>
        <section className="card">
          <header><h3>작업할 브랜드 선택</h3></header>
          <p className="muted camp-sub">시안은 브랜드 단위로 만들어지며, 선택한 브랜드의 규칙(현재 버전)이 적용됩니다.</p>
          <ul className="camp-list">
            {brands.map((b) => (
              <li key={b.id}>
                <button type="button" onClick={() => onWorkBrand(b.id)}>
                  <span><span className="camp-name">{b.name}</span>{b.description && <span className="camp-meta"> · {b.description}</span>}</span>
                  <span className="camp-meta">{b.current_version_no ? `규칙 v${b.current_version_no}` : ''}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </main>
    )
  if (!work) return <main className="page"><p>불러오는 중…</p></main>

  const BrandBar = (
    <div className="brand-bar">
      <span className="camp-label">작업 브랜드</span>
      <strong>{work.name}</strong>
      <span className="info-chip">규칙 v{work.current_version_no}</span>
      {brands.length > 1 && <button type="button" className="link" onClick={() => onWorkBrand(null)}>브랜드 변경</button>}
    </div>
  )

  const Stepper = (
    <nav className="steps" aria-label="진행 단계">
      <button type="button" onClick={onGoBrand}>1. 브랜드 규칙</button>
      <span className="sep">›</span>
      <button type="button" className="on">2. 규격·메시지·시안</button>
      <span className="sep">›</span>
      <button type="button" disabled={!active} onClick={() => scrollTo('sec-copy')}>3. 편집</button>
      <span className="sep">›</span>
      <button type="button" disabled={!active} onClick={() => scrollTo('sec-review')}>4. 검수·내보내기</button>
    </nav>
  )

  // ---------- 캠페인 선택 ----------
  if (!active)
    return (
      <>
        {Stepper}
        <main className="page wide">
          <h1>규격·메시지·시안</h1>
          {BrandBar}
          {msg && <div className={msg.ok ? 'notice ok' : 'notice err'}>{msg.text}</div>}

          <section className="card">
            <header><h3>작업할 캠페인 선택</h3></header>
            <p className="muted camp-sub">규격은 캠페인 단위로 저장됩니다.</p>
            {designs.length === 0 ? <p className="muted">만든 캠페인이 없습니다. 아래에서 새로 만드세요.</p> : (
              <ul className="camp-list">
                {designs.map((d) => (
                  <li key={d.id}>
                    <button type="button" onClick={() => openCampaign(d)}>
                      <span className="camp-name">{campaignName(d)}</span>
                      <span className="camp-meta">{campaignMeta(d)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card">
            <header><h3>새 캠페인</h3></header>
            <form className="camp-new" onSubmit={(e) => { e.preventDefault(); if (newName.trim() && workId && !busy) createCampaign() }}>
              <input value={newName} maxLength={100} placeholder="캠페인 이름" onChange={(e) => setNewName(e.target.value)} />
              <button type="submit" className="primary" disabled={busy || !newName.trim() || !workId}>만들기</button>
            </form>
            <p className="muted">캠페인은 광고 이미지를 만드는 용도이며, 만든 시점의 브랜드 규칙 버전에 연결됩니다. 광고 이미지를 만든 뒤 아이콘 세트도 만들 수 있습니다.</p>
          </section>
        </main>
      </>
    )

  // ---------- 캠페인 ----------
  const d = active
  const visiblePlats = platforms.filter((pl) => !pl.media_only)
  const curPlat = visiblePlats.find((p) => p.name === platTab) ?? visiblePlats[0]
  const allPresets = platforms.flatMap((pl) => pl.ad_types.flatMap((a) => a.presets.map((p) => ({ p, platform: pl.name }))))
  const pickedItems = picked.map((id) => allPresets.find((x) => x.p.id === id)).filter(Boolean) as { p: PresetLite; platform: string }[]
  const hasSeparate = pickedItems.some(({ p }) => p.content_kind && p.content_kind !== '완성형')
  const chip = (p: PresetLite) => {
    const fmt = p.conditions?.find((c) => c.name === '파일 형식')?.value
    const max = p.conditions?.find((c) => c.name === '최대 용량')?.value
    const head = p.text_limits?.find((t) => t.field === '제목' || t.field === 'headline')
    return { fmt, max, head }
  }
  const canGenerate = !busy && picked.length > 0 && brief.product_name.trim() !== '' && brief.key_message.trim() !== ''

  const setupSteps = (
    <div className="setup-grid">
      <div className="setup-main">
        <section className="card step-card">
          <header><h3><span className="step-no">①</span> 규격 선택</h3><span className="muted">선택 {picked.length}개</span></header>
          {visiblePlats.length === 0 ? <p className="muted">선택할 수 있는 규격이 없습니다.</p> : (
            <div className="spec-pick">
              <ul className="spec-plats">
                {visiblePlats.map((pl) => {
                  const ids = pl.ad_types.flatMap((a) => a.presets.map((p) => p.id))
                  const n = ids.filter((id) => picked.includes(id)).length
                  return (
                    <li key={pl.name}>
                      <button type="button" className={curPlat?.name === pl.name ? 'on' : ''} onClick={() => setPlatTab(pl.name)}>
                        {pl.name}{n > 0 && <span className="count">{n}</span>}
                      </button>
                    </li>
                  )
                })}
              </ul>
              <div className="spec-items">
                {curPlat?.ad_types.flatMap((a) => a.presets).map((p) => {
                  const c = chip(p)
                  return (
                    <label key={p.id} className={`spec-item ${picked.includes(p.id) ? 'on' : ''}`}>
                      <input type="checkbox" checked={picked.includes(p.id)} onChange={() => toggle(p.id)} />
                      <div>
                        <div className="spec-title">
                          {p.name}
                          <span className={`badge ${p.status === '확인됨' ? 'b-ok' : 'b-warn'}`}>{p.status === '확인됨' ? '✓ 공식 확인' : p.status}</span>
                        </div>
                        <div className="spec-chips">
                          {p.production_width && <span>{p.production_width}×{p.production_height}</span>}
                          {p.ratio && <span>{p.ratio}</span>}
                          {c.fmt && <span>{c.fmt.replace(/, /g, '·')}</span>}
                          {c.max && <span>{c.max} 이하</span>}
                        </div>
                        <div className="muted spec-sub">
                          {p.content_kind}{c.head?.max_chars ? ` · 제목 ${c.head.max_chars}자 ${c.head.level === '강제' ? '제한' : '권장'}` : ''}
                        </div>
                        <div className="muted spec-sub">
                          {p.version_no ? `규격 v${p.version_no}` : ''}{p.checked_at ? ` · 확인일 ${p.checked_at}` : ''}
                        </div>
                      </div>
                    </label>
                  )
                })}
              </div>
            </div>
          )}
          <p className="muted spec-foot">공식 문서로 확인한 규격만 검수 완료 내보내기가 가능합니다. 수치를 확인하지 못한 규격은 선택할 수 없습니다.</p>
        </section>

        <section className="card step-card">
          <header><h3><span className="step-no">②</span> 상품·메시지 입력</h3><span className="muted">광고 시안 + 카피</span></header>
          <div className="field-grid">
            <label>서비스·상품 <span className="req">*</span>
              <input value={brief.product_name} maxLength={100} placeholder="예: 신선식품 새벽배송 서비스" onChange={(e) => setBrief({ ...brief, product_name: e.target.value })} />
            </label>
            <label>타깃
              <input value={brief.target} maxLength={300} placeholder="예: 신혼부부, 1인 가구" onChange={(e) => setBrief({ ...brief, target: e.target.value })} />
            </label>
          </div>
          <label className="msg-field">핵심 메시지 <span className="req">*</span>
            <input value={brief.key_message} maxLength={200} placeholder="예: 오늘 주문하면 내일 아침 신선하게 도착합니다" onChange={(e) => setBrief({ ...brief, key_message: e.target.value })} />
            <span className="counter">{brief.key_message.length}/200</span>
          </label>
          <div className="msg-field">제품 이미지 <span className="opt">(선택)</span>
            <div className="product-img">
              <div className="product-box">
                {brief.product_image_asset_id
                  ? <img src={`/api/assets/${brief.product_image_asset_id}/file`} alt="제품 이미지" />
                  : <span className="muted">이미지 없음</span>}
              </div>
              <div>
                <div className="muted">누끼 이미지 권장 · PNG·JPG·WEBP, 200KB 이하</div>
                <input ref={productInput} type="file" hidden accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"
                  onChange={(e) => uploadProduct(e.target.files?.[0], e.target)} />
                <div className="row">
                  <button type="button" disabled={busy} onClick={() => productInput.current?.click()}>
                    {brief.product_image_asset_id ? '교체' : '업로드'}
                  </button>
                  {brief.product_image_asset_id && (
                    <button type="button" onClick={() => setBrief({ ...brief, product_image_asset_id: null })}>삭제</button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <aside className="setup-side">
        <section className="card">
          <header><h3>작업판 {picked.length}개</h3></header>
          {pickedItems.length === 0 ? <p className="muted">선택한 규격이 없습니다. ① 규격 선택에서 지면을 골라 주세요.</p> : (
            <ul className="board">
              {pickedItems.map(({ p, platform }) => (
                <li key={p.id}>
                  <div>
                    <div className="muted board-plat">{platform}</div>
                    <div className="board-name">{p.name}</div>
                    <div className="muted board-size">{p.production_width}×{p.production_height}{p.ratio ? ` · ${p.ratio}` : ''}</div>
                  </div>
                  <button type="button" className="x" aria-label={`${p.name} 제외`} onClick={() => toggle(p.id)}>✕</button>
                </li>
              ))}
            </ul>
          )}
          <p className="board-line"><strong>브랜드</strong> {d.brand.name} v{d.brand.version_no}{rules ? ` (필수 표현 ${rules.req} · 금지 표현 ${rules.ban})` : ''}</p>
          <p className="board-line"><strong>콘텐츠</strong> 광고 시안 + 카피</p>
          {hasSeparate && (
            <div className="sep-note">분리 자산형 지면은 이미지·로고·문구를 따로 제출합니다. 이미지에 문구와 로고를 합성하지 않고, 내보낼 때 파일을 나눠 담습니다.</div>
          )}
          <div className="board-actions">
            <button disabled={busy || !brief.product_name.trim()} onClick={saveInput}>임시 저장</button>
            <button className="primary" disabled={!canGenerate} onClick={generateCampaign}>{busy ? '생성 중…' : '시안 생성하기'}</button>
          </div>
          <p className="muted board-foot">브랜드 규칙 + 선택 규격을 함께 반영해 시안 {3}개를 만듭니다. 지면 수에 따라 수십 초 걸릴 수 있습니다.</p>
          {hasCopy && <button type="button" className="link" onClick={() => setEditing(false)}>입력 닫기</button>}
        </section>
      </aside>
    </div>
  )
  return (
    <>
      {Stepper}
      <main className="page wide">
        {BrandBar}
        <section className="card camp-head">
          <div>
            <div className="camp-label">캠페인</div>
            <div className="camp-title">{campaignName(d)}</div>
          </div>
          <div className="camp-chips">
            <span className="info-chip">브랜드 규칙 v{d.brand.version_no}</span>
            <span className="info-chip">{campaignState(d)}</span>
            {d.brand_outdated && <span className="badge b-warn">브랜드 규칙이 v{d.brand.current_version_no}로 변경됨</span>}
            <button type="button" className="link" onClick={() => setBrandPanel((v) => !v)}>{brandPanel ? '브랜드 설정 닫기' : '브랜드 설정'}</button>
            <button type="button" className="link" onClick={backToList}>다른 캠페인</button>
          </div>
          {brandPanel && (
            <div className="brand-panel">
              <div>
                <strong>{d.brand.name}</strong> · 적용 중인 규칙 v{d.brand.version_no}
                {d.brand_outdated
                  ? <> → 최신 v{d.brand.current_version_no}</>
                  : <span className="muted"> (최신 버전입니다)</span>}
              </div>
              <div className="row">
                <button disabled={busy || !d.brand_outdated} onClick={() => applyBrand(d.brand.id)}>
                  최신 규칙{d.brand.current_version_no ? ` v${d.brand.current_version_no}` : ''} 적용
                </button>
              </div>
              {brands.length > 1 && (
                <div className="row">
                  <label>다른 브랜드로 변경
                    <select value={moveTo} onChange={(e) => setMoveTo(e.target.value === '' ? '' : Number(e.target.value))}>
                      <option value="">브랜드 선택</option>
                      {brands.filter((b) => b.id !== d.brand.id).map((b) => <option key={b.id} value={b.id}>{b.name} (규칙 v{b.current_version_no})</option>)}
                    </select>
                  </label>
                  <button disabled={busy || moveTo === ''} onClick={() => moveTo !== '' && applyBrand(moveTo)}>변경</button>
                </div>
              )}
              <p className="muted">적용하면 이미 만든 카피·이미지는 이전 규칙 기준이 되므로 다시 생성해야 하고, 기존 검수 결과는 무효화됩니다.</p>
            </div>
          )}
        </section>

        {msg && <div className={msg.ok ? 'notice ok' : 'notice err'}>{msg.text}</div>}

        {d.kind !== '광고 이미지' ? (
          <section className="card" id="sec-copy">
            <header><h3>아이콘 세트</h3></header>
            <IconPanel designId={d.id} versionNo={d.brand.version_no} ready ask={false} />
          </section>
        ) : hasCopy === null ? (
          <p className="muted">불러오는 중…</p>
        ) : (
          <>
            {(!hasCopy || editing) ? (
              setupSteps
            ) : (
              <section className="card camp-input">
                <div className="camp-input-top">
                  <div>
                    <div className="camp-label">입력한 메시지</div>
                    <div className="camp-msg">{d.brief.key_message || d.brief.product_name}</div>
                  </div>
                  <button type="button" onClick={() => { setEditing(true) }}>입력 수정</button>
                </div>
                <div className="camp-chips left">
                  <span className="info-chip">서비스 {d.brief.product_name}{d.brief.description ? ` · ${d.brief.description}` : ''}</span>
                  {d.brief.target && <span className="info-chip">타깃 {d.brief.target}</span>}
                  <span className="info-chip">규격 {d.presets.length}개</span>
                  <span className="info-chip">광고 시안 + 카피</span>
                </div>
              </section>
            )}

            {hasCopy && (
              <>
                <section className="card step-card" id="sec-copy">
                  <header><h3><span className="step-no">③</span> 카피·시안 선택</h3></header>
                  <CopyPanel key={copyKey} designId={d.id} brandName={d.brand.name ?? ''} versionNo={d.brand.version_no} />
                </section>

                <section className="card step-card" id="sec-review">
                  <header><h3><span className="step-no">⑤</span> 검수·내보내기</h3></header>
                  <ReviewPanel designId={d.id} />
                </section>
              </>
            )}
          </>
        )}
      </main>
    </>
  )
}
