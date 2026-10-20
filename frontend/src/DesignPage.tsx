import { useCallback, useEffect, useRef, useState } from 'react'
import type React from 'react'
import EditPanel from './EditPanel'
import ReviewPanel from './ReviewPanel'
import { PhraseBox } from './BrandPage'
import ImageGrid, { fileUrl, type ImgRow } from './ImageGrid'
import { busy as overlay } from './Busy'

type PresetLite = {
  id: number; name: string; status: string; production_width: number | null; production_height: number | null
  ratio?: string | null; content_kind?: string; checked_at?: string | null; version_no?: number
  conditions?: { name: string; value: string | null }[]
  text_limits?: { field: string; max_chars?: number | null; level: string }[]
}
type PlatformLite = { name: string; media_only: boolean; ad_types: { name: string; presets: PresetLite[] }[] }
type Design = {
  id: number; kind: string; status: string
  brief: { product_name: string; description: string; key_message: string; target: string; product_image_asset_id?: number | null; image_style?: string; required_phrases?: string[]; forbidden_phrases?: string[]; icon?: { purpose: string; width: number | null; height: number | null } }
  // 실제 적용되는 필수·금지 표현 (캠페인 입력 우선, 비면 브랜드 규칙)
  effective_phrases?: { required_phrases: string[]; forbidden_phrases: string[]; required_source: string; forbidden_source: string } | null
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
// 배경 형태. 서버(imagegen.STYLES)와 같은 이름을 쓴다
const IMAGE_STYLES = ['자동', '실사 인물', '실사 제품·공간', '플랫 일러스트', '3D 렌더', '그래픽 패턴']
type ImgUsage = { date: string; provider?: string; used: number; limit: number; remaining: number; exhausted: boolean;
  calls?: { total: number; images: number; chats: number; by_kind: Record<string, number> }
  quality?: { draft: string; final: string } }
const CALL_KIND: Record<string, string> = { background: '배경', variant: '배경 변형', icon: '아이콘', copy: '카피', icon_names: '기능명', icon_redraw: '아이콘 재작성' }
type Limit = { field: string; min: number | null; max: number | null; level: string | null } | null
type CopyGroup = {
  preset_id: number; platform: string; name: string; batch_no: number | null
  content_kind: string; width: number | null; height: number | null
  limits: { headline: Limit; body: Limit; cta: Limit; skipped: { field: string; reason: string }[] }
  variants: Variant[]
}

const LETTERS = ['A', 'B', 'C', 'D', 'E']

type Screen = 'design' | 'edit' | 'review'

function CopyPanel({ designId, brandName, versionNo, imageStyle, onDesignChange, onEdit, onReview }: {
  designId: number; brandName: string; versionNo: number | null; imageStyle: string; onDesignChange: () => void
  onEdit: (presetId: number) => void; onReview: () => void
}) {
  const [groups, setGroups] = useState<CopyGroup[]>([])
  const [bgStyle, setBgStyle] = useState(imageStyle)
  useEffect(() => setBgStyle(imageStyle), [imageStyle])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [imgs, setImgs] = useState<Record<number, ImgRow>>({})
  const [imgBusy, setImgBusy] = useState<number | null>(null)
  const [usage, setUsage] = useState<ImgUsage | null>(null)
  const [imgTab, setImgTab] = useState<number | null>(null)
  const [bgBusy, setBgBusy] = useState(false)
  const [bgMsg, setBgMsg] = useState<string | null>(null)
  const hasDraftBg = Object.values(imgs).some((r) => r?.background?.layout.draft)   // 초안 품질 배경이 하나라도 있는가
  const qualitySplit = !usage?.quality || usage.quality.draft !== usage.quality.final   // 초안/확정 품질이 다르게 설정돼 있는가
  const [imgErr, setImgErr] = useState<Record<number, string>>({})
  const [tab, setTab] = useState<'ads' | 'copy'>('ads')
  const [picking, setPicking] = useState(false)
  const [iconKey, setIconKey] = useState(0)      // 아이콘 세트를 지운 뒤 패널 다시 불러오기
  const [lightbox, setLightbox] = useState(false)   // 카드를 누르면 크게 보기(스크롤 없이)

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

  // 시안 A/B/C 선택: 카피는 캠페인당 1회 생성해 모든 지면이 같은 문장을 쓰므로, 번호 하나를 모든 지면에 한 번에 적용한다
  const chooseNo = async (no: number) => {
    setPicking(true); setErr(null)
    try {
      await post(`/api/designs/${designId}/copy/select-variant`, { variant_no: no })
      setGroups((gs) => gs.map((g) => ({ ...g, variants: g.variants.map((v) => ({ ...v, selected: v.variant_no === no })) })))
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
  const makeImage = (presetId: number, regenerate: boolean) => overlay.run('이미지 만드는 중… 배경 생성과 지면 합성에 1~2분 걸립니다', () => makeImageInner(presetId, regenerate))
  const makeImageInner = async (presetId: number, regenerate: boolean) => {
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

  // 배경은 비율군(가로형/정사각/세로형)마다 1장 새로 만들고, 이미 합성된 매체는 새 배경으로 다시 합성한다(합성은 추가 생성 비용 없음).
  // final=false: 초안 품질(싸게, 형태·구도를 고르는 동안) / true: 확정 품질(내보낼 배경, 마음에 들 때 1회)
  const regenBackground = (final: boolean) => overlay.run(
    `${final ? '확정 품질로' : '초안 품질로'} 배경 새로 만드는 중… 비율군마다 1장 만든 뒤 매체를 다시 합성합니다 (1~3분)`, () => regenBackgroundInner(final))
  const regenBackgroundInner = async (final: boolean) => {
    setBgBusy(true); setBgMsg(null)
    try {
      await post(`/api/designs/${designId}/background/regenerate`, { style: bgStyle, final })
      onDesignChange()
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
    const withIcons = window.confirm('아이콘 세트도 함께 지울까요?\n아이콘 세트는 브랜드 규칙 버전에 속해 같은 버전을 쓰는 다른 캠페인에서도 사라집니다.\n(취소 = 아이콘은 유지)')
    setBgBusy(true); setBgMsg(null)
    try {
      const j = await post(`/api/designs/${designId}/reset-generated`, { include_copy: false, include_icons: withIcons })
      setBgMsg(`생성 이미지 ${j.deleted_images}건을 초기화했습니다.${withIcons ? ` 아이콘 ${j.icons_deleted}개도 지웠습니다.` : ''}`)
      if (withIcons) setIconKey((k) => k + 1)
      await loadImgs()
    } catch (e) {
      setBgMsg((e as Error).message)
    } finally {
      setBgBusy(false)
    }
  }

  const generate = () => overlay.run('카피 만드는 중… 수십 초 걸립니다', generateInner)
  const generateInner = async () => {
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
  const goImages = () => document.getElementById('sec-images')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  const firstComposite = withCopy.find((g) => imgs[g.preset_id]?.composite)?.preset_id ?? null
  const hasComposite = firstComposite !== null
  // 같은 번호 시안의 지면별 글자 수 위반(문장은 모든 지면이 같으므로 필수·금지 표현 위반은 한 번만 센다)
  const lengthIssues = (no: number) => withCopy.flatMap((g) =>
    (g.variants.find((v) => v.variant_no === no)?.violations ?? [])
      .filter((x) => x.type === 'length' || x.type === 'min_length')
      .map((x) => ({ ...x, placement: g.name })))

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
            카피는 선택한 규격 {withCopy.length}개의 글자 수 제한 중 가장 엄격한 값으로 한 번에 만들어, 고른 시안의 문장이 <strong>모든 지면에 동일하게</strong> 적용됩니다.
          </p>
          <div className="ad-grid">
            {preview.variants.map((v) => {
              const reqOk = !v.violations.some((x) => x.type === 'required_missing')
              const banOk = !v.violations.some((x) => x.type === 'forbidden')
              const others = [
                ...v.violations.filter((x) => !['required_missing', 'forbidden', 'length', 'min_length'].includes(x.type)),
                ...lengthIssues(v.variant_no).map((x) => ({ ...x, field: `${x.placement} ${x.field}` })),
              ]
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
                {usage.calls && usage.calls.total > 0 && (
                  <> · 오늘 호출 내역: {Object.entries(usage.calls.by_kind).map(([k, n]) => `${CALL_KIND[k] ?? k} ${n}`).join(', ')} (이미지 {usage.calls.images}, 채팅 {usage.calls.chats})</>
                )}
                {' '}(호출 횟수 기준, UTC 0시 초기화) · 배경은 첫 비율군 1장을 만든 뒤 나머지 비율군(세로형·정사각·가로형)은 같은 장면을 그 비율 구도로 변형해 모든 매체가 한 장면을 씁니다.
              </p>
            )}
            <div className="row">
              <label className="muted">배경 형태{' '}
                <select value={bgStyle} disabled={bgBusy} onChange={(e) => setBgStyle(e.target.value)}>
                  {IMAGE_STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
              <button disabled={bgBusy || !Object.values(imgs).some((r) => r.background)} onClick={() => regenBackground(false)}
                title="형태·구도를 고르는 동안은 초안 품질로 만듭니다 (비용 약 1/4)">
                배경 새로 생성 ({qualitySplit ? '초안 ' : ''}{usage?.quality ? `${usage.quality.draft} · ` : ''}비율군마다 1장, 모든 매체에 적용)
              </button>
              {qualitySplit && <button disabled={bgBusy || !Object.values(imgs).some((r) => r.background) || !hasDraftBg} onClick={() => regenBackground(true)}
                title="지금 형태·구도가 마음에 들면 한 번만 확정 품질로 다시 만듭니다. 내보내기 전에 실행하세요">
                확정 품질로 배경 생성{usage?.quality ? ` (${usage.quality.final})` : ''}
              </button>}
              <button disabled={bgBusy || imgBusy !== null} onClick={resetGenerated}>시안 이미지 초기화</button>
              {bgBusy && <span className="muted">배경 생성 후 합성된 매체를 다시 합성하는 중… 1~2분 걸릴 수 있습니다.</span>}
            </div>
            {bgMsg && <div className="notice err">{bgMsg}</div>}
            {qualitySplit && hasDraftBg && !bgBusy && (
              <p className="muted">현재 배경은 초안 품질입니다. 형태·구도가 정해지면 '확정 품질로 배경 생성'을 한 번 실행한 뒤 내보내세요. 검수에서도 경고로 표시됩니다.</p>
            )}
            {(() => {
              const cur = withCopy.find((x) => x.preset_id === imgTab) ?? withCopy[0]
              if (!cur) return null
              const idx = withCopy.findIndex((x) => x.preset_id === cur.preset_id)
              const made = withCopy.filter((x) => imgs[x.preset_id]?.composite).length
              // 첫 '이미지 생성'은 카피를 고른 완성형(또는 분리 자산형) 지면 하나에서 시작하면 나머지를 이어서 만든다
              const firstEligible = withCopy.find((x) => x.content_kind !== '완성형' || x.variants.some((v) => v.selected))?.preset_id ?? null
              return (
                <>
                  <div className="row">
                    <p className="muted" style={{ margin: 0 }}>
                      {made === 0 ? '아직 만든 이미지가 없습니다. "모든 지면 이미지 생성"을 누르면 선택한 지면 전체를 이어서 만듭니다.'
                        : `합성본 ${made}/${withCopy.length}개. 카드를 누르면 크게 보고 다시 합성하거나 편집할 수 있습니다.`}
                    </p>
                    {made === 0 && firstEligible !== null && (
                      <button className="primary" disabled={imgBusy !== null || bgBusy} onClick={() => makeImage(firstEligible, false)}>
                        모든 지면 이미지 생성
                      </button>
                    )}
                  </div>
                  <ImageGrid items={withCopy} rows={imgs} selected={cur.preset_id} onSelect={(id) => { setImgTab(id); setLightbox(true) }}
                    badge={(it, row) => row?.composite
                      ? <span className="badge b-ok">합성됨</span>
                      : it.content_kind === '완성형' && !groups.find((g) => g.preset_id === it.preset_id)?.variants.some((v) => v.selected)
                        ? <span className="badge b-warn">카피 선택 필요</span>
                        : <span className="badge b-warn">미생성</span>} />
                  {lightbox && (
                    <Lightbox title={`${cur.platform} · ${cur.name}`} subtitle={`${cur.width}×${cur.height}`}
                      onClose={() => setLightbox(false)}
                      onPrev={idx > 0 ? () => setImgTab(withCopy[idx - 1].preset_id) : undefined}
                      onNext={idx < withCopy.length - 1 ? () => setImgTab(withCopy[idx + 1].preset_id) : undefined}
                      position={`${idx + 1} / ${withCopy.length}`}>
                      <ImageBlock key={cur.preset_id} g={cur} row={imgs[cur.preset_id]} busy={imgBusy === cur.preset_id} err={imgErr[cur.preset_id]}
                        canMake={cur.content_kind !== '완성형' || cur.variants.some((v) => v.selected)} onMake={makeImage}
                        onEdit={(id) => { setLightbox(false); onEdit(id) }} />
                    </Lightbox>
                  )}
                </>
              )
            })()}
          </div>

          <IconPanel key={iconKey} designId={designId} versionNo={versionNo} ask
            ready={groups.some((g) => imgs[g.preset_id]?.composite)} />

          <div className="pick-bar">
            <span className="pick-text">
              {selected ? <>선택: <strong>시안 {letter(selected)}</strong> · {selected.headline}</> : <span className="muted">사용할 시안을 선택해 주세요.</span>}
            </span>
            <span className="row">
              {hasComposite
                ? <button onClick={() => onEdit(firstComposite!)}>편집 화면으로 →</button>
                : <button className="primary" disabled={!selected} onClick={goImages}>이미지 생성으로 →</button>}
              <button className="primary" disabled={!hasComposite} onClick={onReview}>검수·내보내기 →</button>
            </span>
          </div>
        </>
      )}

      {has && tab === 'copy' && (
        <div className="copy-group">
          <h4>모든 지면 공통 카피 {count}개</h4>
          <p className="muted">문장은 모든 지면이 같습니다. 지면별로 적용한 글자 수 제한과, 그 제한을 넘는 시안만 아래에 표시합니다.</p>
          <ul className="muted">
            {withCopy.map((g) => (
              <li key={g.preset_id}>
                <strong>{g.platform} · {g.name}</strong> — 제목: {lim(g.limits.headline)} · 본문: {lim(g.limits.body)} · CTA: {lim(g.limits.cta)}
                {g.limits.skipped.length > 0 && ` · 이번 단계 제외: ${g.limits.skipped.map((s) => `${s.field}(${s.reason})`).join(', ')}`}
              </li>
            ))}
          </ul>
          {preview.variants.map((v) => {
            const issues = [
              ...v.violations.filter((x) => x.type !== 'length' && x.type !== 'min_length').map((x) => ({ ...x, placement: '' })),
              ...lengthIssues(v.variant_no),
            ]
            return (
              <div key={v.id} className={`variant ${issues.length ? 'bad' : ''}`}>
                <div className="muted">
                  <label style={{ display: 'inline-flex', gap: 6, marginRight: 8 }}>
                    <input type="radio" name="sel-all" checked={v.selected} disabled={picking} onChange={() => chooseNo(v.variant_no)} /> 모든 지면에 이 카피 사용
                  </label>
                  시안 {letter(v)}{v.attempts > 1 ? ' · 규칙 위반으로 1회 재생성됨' : ''}
                </div>
                <div><strong>제목</strong> {v.headline} <span className="muted">({v.headline.length}자)</span></div>
                <div><strong>본문</strong> {v.body} <span className="muted">({v.body.length}자)</span></div>
                <div><strong>CTA</strong> {v.cta} <span className="muted">({v.cta.length}자)</span></div>
                {issues.map((x, i) => (
                  <span key={i} className={`badge ${x.severity === '필수' || x.severity === '강제' || x.severity === '수정 필요' ? 'b-need' : 'b-warn'}`}>
                    {x.severity} · {x.placement ? `${x.placement} ` : ''}{x.field}: {x.detail}
                  </span>
                ))}
              </div>
            )
          })}
        </div>
      )}
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

// 카드 클릭 시 스크롤 없이 크게 보는 창. Esc·배경 클릭으로 닫고, ←/→ 로 다른 지면으로 옮긴다
function Lightbox({ title, subtitle, position, onClose, onPrev, onNext, children }: {
  title: string; subtitle?: string; position?: string; onClose: () => void
  onPrev?: () => void; onNext?: () => void; children: React.ReactNode
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft' && onPrev) onPrev()
      else if (e.key === 'ArrowRight' && onNext) onNext()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, onPrev, onNext])
  return (
    <div className="lightbox" onClick={onClose} role="dialog" aria-modal="true" aria-label={title}>
      <div className="lightbox-box" onClick={(e) => e.stopPropagation()}>
        <div className="lightbox-head">
          <div>
            <strong>{title}</strong> {subtitle && <span className="muted">{subtitle}</span>}
            {position && <span className="muted"> · {position}</span>}
          </div>
          <div className="row">
            <button type="button" disabled={!onPrev} onClick={onPrev} aria-label="이전 지면">←</button>
            <button type="button" disabled={!onNext} onClick={onNext} aria-label="다음 지면">→</button>
            <button type="button" onClick={onClose} aria-label="닫기">닫기 (Esc)</button>
          </div>
        </div>
        {children}
      </div>
    </div>
  )
}

function ImageBlock({ g, row, busy, err, canMake, onMake, onEdit }: {
  g: CopyGroup; row?: ImgRow; busy: boolean; err?: string; canMake: boolean
  onMake: (presetId: number, regenerate: boolean) => void; onEdit: (presetId: number) => void
}) {
  const comp = row?.composite
  const sep = g.content_kind === '분리 자산형'
  return (
    <div className="imgblock">
      {sep && <p className="muted">분리 자산형: 텍스트 없는 이미지만 만들고, 로고·카피는 내보내기에서 별도 파일로 제공합니다.</p>}
      {comp && (
        <div className="preview">
          <img src={fileUrl(comp)} alt={`${g.name} 미리보기`} />
          <div className="muted">
            {comp.width}×{comp.height}px{comp.layout.template ? ` · ${comp.layout.template}` : ''}
            {comp.layout.margin_source ? ` · ${comp.layout.margin_source}` : ''}
            {comp.layout.logo_placement ? ` · 로고 ${comp.layout.logo_placement.corner}` : ''}
            {row?.background ? ` · 배경 ${row.background.width}×${row.background.height}${row.background.layout.style ? ` · ${row.background.layout.style}` : ''}` : ''}
          </div>
          {(comp.layout.warnings ?? []).map((w, i) => <div key={i} className="badge b-warn">{w}</div>)}
        </div>
      )}
      <div className="row">
        <button className="primary" disabled={busy || !canMake} onClick={() => onMake(g.preset_id, false)}>
          {comp ? (sep ? '이미지 다시 만들기(새로 생성)' : '문구 다시 합성(배경 유지)') : '이미지 생성'}
        </button>
        {comp && !sep && <button onClick={() => onEdit(g.preset_id)}>편집 화면에서 열기 →</button>}
        {busy && <span className="muted">이미지 생성 중… 1~2분 걸릴 수 있습니다. 비용이 발생합니다.</span>}
        {!canMake && <span className="muted">완성형 지면은 사용할 카피를 먼저 선택해 주세요.</span>}
      </div>
      {err && <div className="notice err">{err}</div>}
    </div>
  )
}

// ---------- 편집 화면 (시안·검수와 분리) ----------
function EditScreen({ design, presetId, onPick, onBack, onReview }: {
  design: Design; presetId: number | null; onPick: (id: number) => void; onBack: () => void; onReview: () => void
}) {
  const [imgs, setImgs] = useState<Record<number, ImgRow>>({})
  const [loaded, setLoaded] = useState(false)
  const load = useCallback(() =>
    fetch(`/api/designs/${design.id}/images`).then((r) => r.json()).then((rows: ImgRow[]) => {
      if (Array.isArray(rows)) setImgs(Object.fromEntries(rows.map((x) => [x.preset_id, x])))
    }).finally(() => setLoaded(true)), [design.id])
  useEffect(() => { load() }, [load])

  // 편집은 완성형(로고·문구를 합성한) 지면의 합성 이미지가 있어야 가능하다
  const editable = design.presets.filter((p) => imgs[p.id]?.composite && !imgs[p.id]?.composite?.layout.separate_assets)
  const cur = editable.find((p) => p.id === presetId) ?? editable[0] ?? null
  useEffect(() => { if (cur && cur.id !== presetId) onPick(cur.id) }, [cur, presetId, onPick])

  return (
    <section className="card step-card" id="sec-edit">
      <header>
        <h3><span className="step-no">④</span> 이미지 편집</h3>
        <div className="row">
          <button type="button" onClick={onBack}>← 시안으로</button>
          <button type="button" className="primary" disabled={editable.length === 0} onClick={onReview}>검수·내보내기 →</button>
        </div>
      </header>
      {!loaded ? <p className="muted">불러오는 중…</p> : editable.length === 0 ? (
        <p className="muted">편집할 합성 이미지가 없습니다. 시안 화면의 ④ 이미지 생성에서 먼저 이미지를 만들어 주세요. (분리 자산형 지면은 문구·로고를 합성하지 않아 편집 대상이 아닙니다)</p>
      ) : (
        <>
          <ImageGrid
            items={design.presets.map((p) => ({ preset_id: p.id, platform: p.platform, name: p.name, width: p.width, height: p.height }))}
            rows={imgs} selected={cur?.id ?? null} onSelect={onPick}
            dimmed={(_it, row) => !row?.composite ? '합성 이미지 없음' : row.composite.layout.separate_assets ? '분리 자산형 — 문구·로고를 합성하지 않아 편집 대상이 아닙니다' : null}
            badge={(it, row) => row?.composite && !row.composite.layout.separate_assets
              ? <span className={`badge ${cur?.id === it.preset_id ? 'b-ok' : 'b-warn'}`}>{cur?.id === it.preset_id ? '편집 중' : '편집 가능'}</span>
              : null} />
          <p className="muted">카드를 누르면 그 지면을 편집합니다. 잠긴 요소(로고)는 바꿀 수 없고, 저장하면 검수 결과가 무효화되어 다시 검수해야 합니다.</p>
          {cur && <EditPanel key={cur.id} designId={design.id} presetId={cur.id} onClose={onBack} onSaved={load} />}
        </>
      )}
    </section>
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
  const [target, setTarget] = useState(10)   // 세트 목표 개수 (서버 설정)
  const [filling, setFilling] = useState(false)

  useEffect(() => {
    fetch(`/api/designs/${designId}/icons`).then((r) => r.json()).then((l) => { if (Array.isArray(l)) setItems(l); setLoaded(true) })
    fetch('/api/icon-settings').then((r) => r.json()).then((j) => { if (j?.count) setTarget(j.count) }).catch(() => undefined)
  }, [designId])

  const generate = () => overlay.run('아이콘 세트 만드는 중… 기능명을 정하고 10개를 렌더합니다 (2~3분)', generateInner)
  const generateInner = async () => {
    setBusy(true); setErr(null); setNote(null)
    try {
      const j = await post(`/api/designs/${designId}/icons`, {})
      setItems(j.icons); setVer((v) => v + 1)
      if (!j.complete) setNote(`목표 ${j.requested}개 중 ${j.made}개만 만들어졌습니다. 아래 '부족한 아이콘 채우기'로 나머지를 이어서 만들 수 있습니다.`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // 세트가 목표 개수에 못 미칠 때(크레딧 소진 등) 빠진 만큼만 이어서 만든다. 아이콘 1개당 이미지 1장 비용
  const fill = () => overlay.run('부족한 아이콘 채우는 중… (1개당 10~20초)', () => fillInner())
  const fillInner = async () => {
    setFilling(true); setErr(null); setNote(null)
    try {
      const j = await post(`/api/designs/${designId}/icons/fill`, {})
      setItems(j.icons); setVer((v) => v + 1)
      if (!j.complete) setNote(`${j.made}개를 채웠지만 아직 목표 ${j.requested}개 중 ${j.icons.length}개입니다. 다시 시도할 수 있습니다.`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setFilling(false)
    }
  }

  const redraw = (id: number) => overlay.run('아이콘 다시 그리는 중… (10~20초)', () => redrawInner(id))
  const redrawInner = async (id: number) => {
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
      {items.length < target && (
        <div className="row">
          <span className="muted">목표 {target}개 중 {items.length}개 — 세트가 아직 채워지지 않아 검수에서 '기능 수' 항목이 차단됩니다.</span>
          <button disabled={filling || one !== null} onClick={fill}>
            {filling ? `부족한 아이콘 ${target - items.length}개를 렌더하는 중…` : `부족한 아이콘 ${target - items.length}개 채우기`}
          </button>
          {!filling && <span className="muted">(아이콘 1개당 이미지 1장 비용)</span>}
        </div>
      )}
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

type Brief = {
  product_name: string; description: string; key_message: string; target: string; product_image_asset_id: number | null; image_style: string
  required_phrases: string[]; forbidden_phrases: string[]
}
const EMPTY_BRIEF: Brief = { product_name: '', description: '', key_message: '', target: '', product_image_asset_id: null, image_style: '자동', required_phrases: [], forbidden_phrases: [] }

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
  const [brief, setBrief] = useState<Brief>(EMPTY_BRIEF)
  const [picked, setPicked] = useState<number[]>([])
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [brandPanel, setBrandPanel] = useState(false)
  const productInput = useRef<HTMLInputElement>(null)
  const [hasCopy, setHasCopy] = useState<boolean | null>(null)   // 이 캠페인에 생성된 카피가 있는지
  const [copyKey, setCopyKey] = useState(0)                       // 카피 영역 다시 불러오기
  // 캠페인 화면 분리: 시안(입력·카피·이미지 생성) / 편집(이미지 편집기) / 검수·내보내기
  const [screen, setScreen] = useState<Screen>('design')
  const [editPreset, setEditPreset] = useState<number | null>(null)   // 편집 화면에서 열어 둔 지면
  const goEdit = (presetId: number | null = null) => { setEditPreset(presetId); setScreen('edit'); window.scrollTo(0, 0) }
  const goScreen = (s: Screen) => { setScreen(s); window.scrollTo(0, 0) }
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
    setBrief({ product_name: d.brief.product_name, description: d.brief.description ?? '', key_message: d.brief.key_message ?? '', target: d.brief.target ?? '', product_image_asset_id: d.brief.product_image_asset_id ?? null, image_style: d.brief.image_style ?? '자동',
      required_phrases: d.brief.required_phrases ?? [], forbidden_phrases: d.brief.forbidden_phrases ?? [] })
    setPicked(d.presets.map((p) => p.id))
    setEditing(false); setHasCopy(null); setRules(null); setScreen('design'); setEditPreset(null)
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
        brief: { ...EMPTY_BRIEF, product_name: newName.trim() },
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

  const generateCampaign = () => overlay.run('카피 만드는 중… 수십 초 걸립니다', () => generateCampaignInner())
  const generateCampaignInner = async () => {
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

  // 3·4단계는 캠페인에 카피가 있어야 열린다 (편집은 합성 이미지, 검수는 카피·이미지 기준)
  const stepReady = !!active && active.kind === '광고 이미지' && hasCopy === true
  const Stepper = (
    <nav className="steps" aria-label="진행 단계">
      <button type="button" onClick={onGoBrand}>1. 브랜드 규칙</button>
      <span className="sep">›</span>
      <button type="button" className={!active || screen === 'design' ? 'on' : ''} onClick={() => goScreen('design')}>2. 규격·메시지·시안</button>
      <span className="sep">›</span>
      <button type="button" className={active && screen === 'edit' ? 'on' : ''} disabled={!stepReady} onClick={() => goEdit(editPreset)}>3. 편집</button>
      <span className="sep">›</span>
      <button type="button" className={active && screen === 'review' ? 'on' : ''} disabled={!stepReady} onClick={() => goScreen('review')}>4. 검수·내보내기</button>
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
          <label className="msg-field">배경 형태 <span className="opt">(선택)</span>
            <select value={brief.image_style} onChange={(e) => setBrief({ ...brief, image_style: e.target.value })}>
              {IMAGE_STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <span className="muted">자동이면 브랜드 이미지 톤·상품 설명의 단서(일러스트, 3D, 패턴, 인물)로 고르고, 단서가 없으면 사람 없는 실사 제품·공간으로 만듭니다.</span>
          </label>
          <div className="msg-field">필수 / 금지 표현 <span className="opt">(선택)</span>
            <span className="muted">이 캠페인의 카피 생성·검수에 씁니다. 비워 두면 브랜드 규칙의 표현(필수 {rules?.req ?? '-'}개 · 금지 {rules?.ban ?? '-'}개)이 적용됩니다.</span>
            <div className="field-grid">
              <PhraseBox label={`필수 표현${brief.required_phrases.length ? ' (캠페인 우선)' : ` (브랜드 규칙 ${rules?.req ?? 0}개 적용 중)`}`}
                hint="카피에 반드시 포함" tone="req" items={brief.required_phrases}
                onChange={(v) => setBrief({ ...brief, required_phrases: v })} />
              <PhraseBox label={`금지 표현${brief.forbidden_phrases.length ? ' (캠페인 우선)' : ` (브랜드 규칙 ${rules?.ban ?? 0}개 적용 중)`}`}
                hint="카피에 포함되면 수정 필요" tone="ban" items={brief.forbidden_phrases}
                onChange={(v) => setBrief({ ...brief, forbidden_phrases: v })} />
            </div>
          </div>
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
        ) : screen === 'edit' && hasCopy ? (
          <EditScreen key={d.id} design={d} presetId={editPreset} onPick={setEditPreset}
            onBack={() => goScreen('design')} onReview={() => goScreen('review')} />
        ) : screen === 'review' && hasCopy ? (
          <section className="card step-card" id="sec-review">
            <header>
              <h3><span className="step-no">⑤</span> 검수·내보내기</h3>
              <div className="row">
                <button type="button" onClick={() => goEdit(editPreset)}>← 편집으로</button>
                <button type="button" onClick={() => goScreen('design')}>시안으로</button>
              </div>
            </header>
            <ReviewPanel designId={d.id} />
          </section>
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
                  {d.effective_phrases && (
                    <span className="info-chip" title={`필수: ${d.effective_phrases.required_phrases.join(', ') || '없음'} / 금지: ${d.effective_phrases.forbidden_phrases.join(', ') || '없음'}`}>
                      필수 표현 {d.effective_phrases.required_phrases.length}개({d.effective_phrases.required_source}) · 금지 표현 {d.effective_phrases.forbidden_phrases.length}개({d.effective_phrases.forbidden_source})
                    </span>
                  )}
                  <span className="info-chip">규격 {d.presets.length}개</span>
                  <span className="info-chip">광고 시안 + 카피</span>
                </div>
              </section>
            )}

            {hasCopy && (
              <section className="card step-card" id="sec-copy">
                <header><h3><span className="step-no">③</span> 카피·시안 선택</h3></header>
                <CopyPanel key={copyKey} designId={d.id} brandName={d.brand.name ?? ''} versionNo={d.brand.version_no}
                  imageStyle={d.brief.image_style ?? '자동'} onDesignChange={() => { loadDesigns() }}
                  onEdit={goEdit} onReview={() => goScreen('review')} />
              </section>
            )}
          </>
        )}
      </main>
    </>
  )
}
