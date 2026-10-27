import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type React from 'react'
import EditPanel from './EditPanel'
import ReviewPanel from './ReviewPanel'
import { PhraseBox } from './BrandPage'
import ImageGrid, { fileUrl, inImageChanged, inImageDefault, type ImgRow, type InImage } from './ImageGrid'
import DevStats from './DevStats'
import MasterPreview from './MasterPreview'
import { useWorkspace } from './useWorkspace'
import { busy as overlay, type Order, type QuizItem, type SketchCard } from './Busy'
import { applyVariant } from './variants'

type PresetLite = {
  id: number; name: string; status: string; production_width: number | null; production_height: number | null
  ratio?: string | null; content_kind?: string; checked_at?: string | null; version_no?: number
  conditions?: { name: string; value: string | null }[]
  text_limits?: { field: string; max_chars?: number | null; level: string }[]
}
type PlatformLite = { name: string; media_only: boolean; ad_types: { name: string; presets: PresetLite[] }[] }
type Design = {
  id: number; kind: string; status: string
  brief: { product_name: string; description: string; key_message: string; target: string; product_image_asset_id?: number | null; image_style?: string; image_model?: string | null; bg_mode?: string | null; text_assets?: Record<string, string[]>; required_phrases?: string[]; forbidden_phrases?: string[]; icon?: { purpose: string; width: number | null; height: number | null } }
  // 실제 적용되는 필수·금지 표현 (캠페인 입력 우선, 비면 브랜드 규칙)
  effective_phrases?: { required_phrases: string[]; forbidden_phrases: string[]; required_source: string; forbidden_source: string } | null
  presets: { id: number; platform: string; name: string; width: number | null; height: number | null; status: string; in_image?: InImage }[]
  brand: { id: number; name: string; version_no: number; current_version_no: number }
  brand_outdated: boolean; created_at: string
  text_asset_fields?: string[]    // 문구를 여러 개 요구하는 지면(PMax 등)의 필드. 있으면 '추가 문구 자산 생성' 버튼
}

async function post(url: string, body: unknown, method: 'POST' | 'PUT' = 'POST') {
  const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) {
    const d = j.detail
    throw new Error(Array.isArray(d) ? d.map((x: { msg: string }) => x.msg).join(', ') : d || '요청에 실패했습니다')
  }
  return j
}

type Violation = { type: string; field: string; detail: string; severity: string; phrase?: string; law?: string; reason?: string; suggest?: string; grounded?: boolean }
type Variant = { id: number; variant_no: number; headline: string; body: string; cta: string; violations: Violation[]; attempts: number; selected: boolean }
// 배경 형태. 서버(imagegen.STYLES)와 같은 이름을 쓴다
const IMAGE_STYLES = ['자동', '실사 인물', '실사 제품·공간', '플랫 일러스트', '3D 렌더', '그래픽 패턴']
type ImgUsage = { date: string; provider?: string; used: number; limit: number; remaining: number; exhausted: boolean;
  calls?: { total: number; images: number; chats: number; by_kind: Record<string, { count: number }> }
  quality?: string }
const CALL_KIND: Record<string, string> = { background: '배경', variant: '배경 변형', product: '배경(제품 참조)', icon: '아이콘', copy: '카피', copy_fit: '카피 지면 맞춤', icon_names: '기능명', icon_redraw: '아이콘 재작성', pin: '핀 뱃지' }
type ImgSettings = {
  models: { model: string; default: boolean; usd: number | null; estimated: boolean; input_fidelity: boolean }[]
  modes: string[]; default_mode: string; quality: string; provider: string; usd_krw: number
}
const MODE_LABEL: Record<string, string> = { per_ratio: '비율군마다 1장 (1~3회, 변형은 images.edit)', single: '1장을 모든 지면이 잘라 씀 (손실 큰 비율군만 1장 더)' }
const usdKrw = (v: number | null | undefined, rate: number) => (v == null ? '요율 없음' : `$${v.toFixed(3)} (약 ${Math.round(v * rate).toLocaleString()}원)`)
type Limit = { field: string; min: number | null; max: number | null; level: string | null } | null
type CopyGroup = {
  preset_id: number; platform: string; name: string; batch_no: number | null
  content_kind: string; width: number | null; height: number | null
  limits: { headline: Limit; body: Limit; cta: Limit; skipped: { field: string; reason: string }[] }
  variants: Variant[]
}

const LETTERS = ['A', 'B', 'C', 'D', 'E']

type Screen = 'design' | 'edit' | 'review'
type View = 'master' | 'setup' | 'copy' | 'images' | 'icons' | 'edit' | 'review'

type PanelView = 'copy' | 'images' | 'icons'
// 대기 화면의 'AI 주문서': 이 지면(없으면 첫 지면)의 배경 프롬프트 미리보기(모델 호출 없음). 못 받으면 null
const orderPreview = (designId: number, presetId?: number): Promise<Order | null> =>
  fetch(`/api/designs/${designId}/background/prompt-preview${presetId != null ? `?preset_id=${presetId}` : ''}`)
    .then((r) => (r.ok ? r.json() : null)).catch(() => null)

function CopyPanel({ designId, brandName, versionNo, locked, imageStyle, genSettings, textAssets, assetFields, inImage: inImageProp, view, onDesignChange, onEdit, onReview, onGoImages }: {
  designId: number; brandName: string; versionNo: number | null; imageStyle: string
  locked: boolean    // 검수 완료된 캠페인: 이미지 재생성·생성 설정 변경 불가
  inImage: Record<number, InImage | undefined>    // 지면별 '이미지 안 문구' 설정(서버가 계산한 현재값·기본 여부)
  view: PanelView    // 3열 화면의 가운데에 무엇을 보일지: 카피 / 이미지 / 아이콘 (상태는 한 인스턴스가 공유)
  genSettings: { image_model?: string | null; bg_mode?: string | null }    // 브리프에 저장된 생성 설정(없으면 서버 기본)
  textAssets?: Record<string, string[]>    // PMax 등 문구를 여러 개 요구하는 지면용 추가 문구 자산(카피 생성 때 함께 받음)
  assetFields?: string[]                   // 추가 문구를 요구하는 필드 목록(비면 버튼 숨김)
  onDesignChange: () => void
  onEdit: (presetId: number) => void; onReview: () => void; onGoImages: () => void
}) {
  const [groups, setGroups] = useState<CopyGroup[]>([])
  const [bgStyle, setBgStyle] = useState(imageStyle)
  useEffect(() => setBgStyle(imageStyle), [imageStyle])
  // 생성 설정(이미지 모델·배경 방식): 서버 목록을 받아 고르고, 바꾸면 브리프에 저장한다. 다음 배경 생성부터 적용.
  const [imgSettings, setImgSettings] = useState<ImgSettings | null>(null)
  const [settingsMsg, setSettingsMsg] = useState<string | null>(null)
  useEffect(() => { fetch('/api/image-settings').then((r) => r.json()).then((j: ImgSettings) => j?.models && setImgSettings(j)).catch(() => undefined) }, [])
  const curModel = genSettings.image_model ?? imgSettings?.models.find((m) => m.default)?.model ?? ''
  const curMode = genSettings.bg_mode ?? imgSettings?.default_mode ?? 'per_ratio'
  const saveSettings = async (patch: { image_model?: string; bg_mode?: string }) => {
    setSettingsMsg(null)
    try {
      await fetch(`/api/designs/${designId}/image-settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) })
        .then(async (r) => { if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || '저장 실패') })
      onDesignChange()
      setSettingsMsg('저장했습니다. 다음 배경 생성·재생성부터 적용됩니다 (기존 배경은 그대로).')
    } catch (e) { setSettingsMsg((e as Error).message) }
  }
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [imgs, setImgs] = useState<Record<number, ImgRow>>({})
  const [imgBusy, setImgBusy] = useState<number | null>(null)
  const [usage, setUsage] = useState<ImgUsage | null>(null)
  const [imgTab, setImgTab] = useState<number | null>(null)
  const [bgBusy, setBgBusy] = useState(false)
  const [bgMsg, setBgMsg] = useState<string | null>(null)
  const [bgNote, setBgNote] = useState<string | null>(null)   // 재생성 뒤 안내(미뤄 둔 비율군 등)
  const [imgErr, setImgErr] = useState<Record<number, string>>({})
  const [tab, setTab] = useState<'ads' | 'copy'>('ads')
  const [picking, setPicking] = useState(false)
  const [iconKey] = useState(0)      // 아이콘 세트를 지운 뒤 패널 다시 불러오기
  const [iconCount, setIconCount] = useState(0)   // 아이콘 세트 개수가 바뀌면 핀 뱃지 주제를 다시 불러온다
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
  // 이미 만든 합성본이 있으면 새 시안 문구로 다시 합성한다(배경 유지). 직접 고친 문구가 있으면 덮어쓸지 묻는다.
  const [pickNote, setPickNote] = useState<string | null>(null)
  const chooseNo = async (no: number) => {
    setPicking(true); setErr(null); setPickNote(null)
    try {
      const { note } = await applyVariant(designId, no)
      setGroups((gs) => gs.map((g) => ({ ...g, variants: g.variants.map((v) => ({ ...v, selected: v.variant_no === no })) })))
      setPickNote(note)
      await loadImgs()
      onDesignChange()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setPicking(false)
    }
  }

  const makeOne = async (presetId: number, regenerate: boolean) => {
    await post(`/api/designs/${designId}/presets/${presetId}/image`, { regenerate_background: regenerate })
  }

  // 이미지 안 문구(본문·CTA) 설정: 저장한 뒤 합성본이 있으면 배경을 유지한 채 바로 다시 합성한다(이미지 API 호출 없음 → 비용 0).
  const [inImage, setInImage] = useState(inImageProp)
  useEffect(() => setInImage(inImageProp), [inImageProp])
  const setInImageFor = (presetId: number, body: boolean, cta: boolean) => overlay.run('이미지 안 문구 설정을 저장하고 다시 합성하는 중…', async () => {
    setImgErr((x) => ({ ...x, [presetId]: '' }))
    try {
      const j = await post(`/api/designs/${designId}/presets/${presetId}/in-image`, { body, cta }, 'PUT') as InImage
      setInImage((m) => ({ ...m, [presetId]: j }))
      if (imgs[presetId]?.composite) {
        setImgBusy(presetId)
        await makeOne(presetId, false)
        await loadImgs()
      }
      onDesignChange()
    } catch (e) {
      setImgErr((x) => ({ ...x, [presetId]: (e as Error).message }))
    } finally { setImgBusy(null) }
  })

  // 배경은 시안당 1회만 생성하고, 아직 합성이 없는 나머지 매체도 같은 배경을 각 규격 크기로 맞춰 이어서 합성한다(추가 생성 비용 없음).
  const makeImage = (presetId: number, regenerate: boolean) => overlay.run('이미지 만드는 중… 배경 생성과 지면 합성에 1~2분 걸립니다', () => makeImageInner(presetId, regenerate),
    { order: orderPreview(designId, presetId) })
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

  // 배경은 합성본이 있는 매체의 비율군만 1장씩 새로 만들고(합성본이 없으면 모든 비율군), 그 매체를 새 배경으로 다시 합성한다.
  // 아직 합성하지 않은 비율군은 나중에 그 매체를 합성할 때 현재 장면을 참조해 만든다(쓰지 않는 비율군 호출 없음).
  const regenBackground = () => overlay.run(
    '배경 새로 만드는 중… 합성본이 있는 비율군마다 1장 만든 뒤 매체를 다시 합성합니다 (1~3분)', () => regenBackgroundInner(),
    { order: orderPreview(designId) })
  const regenBackgroundInner = async () => {
    setBgBusy(true); setBgMsg(null); setBgNote(null)
    try {
      const out = await post(`/api/designs/${designId}/background/regenerate`, { style: bgStyle }) as {
        deferred?: string[]; applied?: number[]; skipped?: { name: string; reason: string }[]; single_extra?: string[]; blur_fill?: string[] }
      const notes: string[] = []
      if (out.single_extra?.length) notes.push(`1장을 잘라 쓰면 피사체가 많이 사라지는 비율군(${out.single_extra.join(', ')})은 원본을 참조해 1장 더 만들었습니다.`)
      if (out.blur_fill?.length) notes.push(`보완 변형에 실패한 비율군(${out.blur_fill.join(', ')})은 잘라 쓰지 않고 블러 채우기로 합성합니다.`)
      if (out.applied?.length) notes.push(`편집 저장한 지면 ${out.applied.length}개는 편집 내용을 유지한 채 새 배경으로 다시 그렸습니다.`)
      if (out.skipped?.length) notes.push(`유지됨: ${out.skipped.map((x) => `${x.name}(${x.reason})`).join(' / ')}`)
      if (out.deferred?.length) notes.push(`아직 합성본이 없는 비율군(${out.deferred.join(', ')})은 그 지면을 합성할 때 같은 장면으로 만듭니다.`)
      if (notes.length) setBgNote(notes.join(' '))
      onDesignChange()
      const failed: string[] = []
      for (const g of withCopy) {
        if (!imgs[g.preset_id]?.composite) continue
        if (out.applied?.includes(g.preset_id)) continue   // 편집 저장한 지면은 서버가 요소를 유지한 채 이미 다시 그렸다 (자동 합성은 409)
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
  // 이미지 초기화는 이미지(배경·합성·편집·검수)만 지운다. 아이콘 세트 초기화는 ⑤ 아이콘 패널의 별도 버튼
  const resetGenerated = async () => {
    if (!window.confirm('이 시안의 생성 이미지, 편집 내용, 검수 결과를 모두 지웁니다. (브랜드·규격·입력 내용·카피·아이콘 세트는 유지)\n계속할까요?')) return
    setBgBusy(true); setBgMsg(null)
    try {
      const j = await post(`/api/designs/${designId}/reset-generated`, { include_copy: false, include_icons: false })
      setBgMsg(`생성 이미지 ${j.deleted_images}건을 초기화했습니다. 아이콘 세트는 그대로입니다 (⑤ 아이콘에서 따로 초기화).`)
      await loadImgs()
    } catch (e) {
      setBgMsg((e as Error).message)
    } finally {
      setBgBusy(false)
    }
  }

  // 추가 문구 자산만 다시 받기(채팅 1회): 카피 시안·선택은 그대로 둔다. 카피를 추가 문구 지원 전에 만들었을 때 쓴다
  const [assetBusy, setAssetBusy] = useState(false)
  const [noteAssets, setNoteAssets] = useState<string | null>(null)
  const makeAssets = () => overlay.run('추가 문구 자산 만드는 중… (수십 초)', async () => {
    setAssetBusy(true); setErr(null); setNoteAssets(null)
    try {
      const j = await post(`/api/designs/${designId}/copy/assets`, {})
      onDesignChange()
      setNoteAssets(`추가 문구 자산을 만들었습니다: ${Object.entries(j.text_assets as Record<string, number>).map(([k, n]) => `${k} ${n}개`).join(', ')}. 검수를 다시 실행하세요.`)
    } catch (e) { setErr((e as Error).message) } finally { setAssetBusy(false) }
  })
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
  const goImages = () => onGoImages()
  const firstComposite = withCopy.find((g) => imgs[g.preset_id]?.composite)?.preset_id ?? null
  const hasComposite = firstComposite !== null
  // 같은 번호 시안의 지면별 글자 수 위반(문장은 모든 지면이 같으므로 필수·금지 표현 위반은 한 번만 센다)
  const lengthIssues = (no: number) => withCopy.flatMap((g) =>
    (g.variants.find((v) => v.variant_no === no)?.violations ?? [])
      .filter((x) => x.type === 'length' || x.type === 'min_length')
      .map((x) => ({ ...x, placement: g.name, preset_id: g.preset_id })))
  // 지면 맞춤 줄이기: 그 지면의 선택 카피 중 제한을 넘는 필드만 Claude 1회로 고친다 (다른 지면·시안 선택은 그대로)
  const [fitting, setFitting] = useState<number | null>(null)
  const [fitNote, setFitNote] = useState<string | null>(null)
  const fitPreset = (presetId: number) => overlay.run('이 지면에 맞게 문구 줄이는 중… (수십 초)', async () => {
    setFitting(presetId); setErr(null); setFitNote(null)
    try {
      const j = await post(`/api/designs/${designId}/presets/${presetId}/copy/fit`, {})
      const g = await fetch(`/api/designs/${designId}/copy`).then((r) => r.json())
      if (Array.isArray(g)) setGroups(g)
      setFitNote(j.fits ? `줄였습니다 (${(j.changed as string[]).join(', ')}). 검수를 다시 실행하세요.` : j.note)
    } catch (e) { setErr((e as Error).message) } finally { setFitting(null) }
  })

  return (
    <div className="copy gen">
      {view === 'copy' && <div className="gen-top">
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
      </div>}
      {err && <div className="notice err">{err}</div>}
      {pickNote && view === 'copy' && <div className="notice ok">{pickNote}</div>}
      {view === 'copy' && <DevStats designId={designId} kinds={['copy', 'copy_fit']} label="카피 생성" refreshKey={`${busy}-${groups.length}-${count}-${fitting}`} />}

      {!has && view === 'copy' && <p className="muted gen-empty">카피 생성을 누르면 선택한 규격의 글자 수 제한에 맞춰 시안 문구를 만듭니다.</p>}
      {!has && view !== 'copy' && <p className="muted gen-empty">먼저 ③ 카피·시안에서 카피를 생성하고 시안을 고르면 {view === 'images' ? '이미지를' : '아이콘을'} 만들 수 있습니다.</p>}

      {has && (view !== 'copy' || tab === 'ads') && (
        <>
          {view === 'copy' && (<>
          <p className="muted gen-note">
            카피는 선택한 규격 {withCopy.length}개의 글자 수 제한 중 가장 엄격한 값으로 한 번에 만들어, 고른 시안의 문장이 <strong>모든 지면에 동일하게</strong> 적용됩니다.
          </p>
          <div className="ad-grid">
            {preview.variants.map((v) => {
              const reqOk = !v.violations.some((x) => x.type === 'required_missing')
              const banOk = !v.violations.some((x) => x.type === 'forbidden')
              const others = [
                ...v.violations.filter((x) => !['required_missing', 'forbidden', 'length', 'min_length', 'legal'].includes(x.type)),
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
                    {v.violations.some((x) => x.type === 'legal')
                      ? v.violations.filter((x) => x.type === 'legal').map((x, i) => (
                        <li key={`legal${i}`} className={x.severity === '필수' ? 'ng' : 'warn'}>
                          {x.severity === '필수' ? '✕' : '△'} 법적 표현 '{x.phrase}' — {x.reason}
                          <div className="muted">근거: {x.law}{x.suggest ? ` · 제안: ${x.suggest}` : ''}{x.grounded ? ' · 입력에 있던 표현이라 근거 자료 확인 필요' : ''}</div>
                        </li>))
                      : <li className="ok" title="표시·광고법 등 법적 금지·주의 표현 사전 기준의 참고용 자동 검사입니다. 법률 자문이 아닙니다.">✓ 법적 표현 점검 통과</li>}
                    {others.map((x, i) => <li key={i} className="ng">✕ {x.field}: {x.detail}</li>)}
                  </ul>
                  {v.selected
                    ? <div className="ad-picked">✓ 선택됨</div>
                    : <button disabled={picking} onClick={() => chooseNo(v.variant_no)}>이 시안 선택</button>}
                </article>
              )
            })}
          </div>

          </>)}
          {view === 'images' && groups.some((g) => g.content_kind === '완성형' && imgs[g.preset_id]?.composite) && (
            <CommonText designId={designId} onApplied={loadImgs} />
          )}

          {view === 'images' && <div id="sec-images" className="gen-images">
            <h4 className="gen-h"><span className="step-no">④</span> 이미지 생성·편집</h4>
            <DevStats designId={designId} kinds={['background', 'variant']} label="배경 생성" refreshKey={`${imgBusy}-${bgBusy}-${usage?.used}`} />
            {usage && (
              <p className={usage.exhausted ? 'notice err' : 'muted'}>
                오늘 이미지 생성{usage.provider ? `(${usage.provider})` : ''} {usage.used}/{usage.limit}회 사용 · 남은 횟수 약 {usage.remaining}회
                {usage.exhausted ? ' · 오늘 설정한 생성 한도를 모두 사용했습니다' : ''}
                {usage.calls && usage.calls.total > 0 && (
                  <> · 오늘 호출 내역: {Object.entries(usage.calls.by_kind).map(([k, n]) => `${CALL_KIND[k] ?? k} ${n.count}`).join(', ')} (이미지 {usage.calls.images}, 채팅 {usage.calls.chats})</>
                )}
                {' '}(호출 횟수 기준, UTC 0시 초기화) · {curMode === 'single'
                  ? '배경은 가장 적게 잘리는 비율의 1장을 공용 구도로 만들고 모든 매체가 그 한 장을 잘라 씁니다. 잘라서 피사체가 많이 사라지는 비율군(보통 가로형)만 원본을 참조해 1장 더 만들고, 그마저 실패하면 블러 채우기로 합성합니다.'
                  : '배경은 첫 비율군 1장을 만든 뒤 나머지 비율군(세로형·정사각·가로형)은 같은 장면을 그 비율 구도로 변형해 모든 매체가 한 장면을 씁니다.'}
              </p>
            )}
            {imgSettings && (
              <div className="row">
                <label className="muted">이미지 모델{' '}
                  <select value={curModel} disabled={bgBusy || locked} onChange={(e) => saveSettings({ image_model: e.target.value })}>
                    {imgSettings.models.map((m) => <option key={m.model} value={m.model}>{m.model}{m.default ? ' (기본)' : ''}</option>)}
                  </select>
                </label>
                {(() => { const m = imgSettings.models.find((x) => x.model === curModel); return m ? (
                  <span className="muted">정사각 1장 {imgSettings.quality} {usdKrw(m.usd, imgSettings.usd_krw)}{m.estimated ? ' · 요금표에 없어 추정' : ''}{m.input_fidelity ? '' : ' · input_fidelity 미지원'}</span>
                ) : null })()}
                <label className="muted">배경 방식{' '}
                  <select value={curMode} disabled={bgBusy || locked} onChange={(e) => saveSettings({ bg_mode: e.target.value })}>
                    {imgSettings.modes.map((m) => <option key={m} value={m}>{MODE_LABEL[m] ?? m}</option>)}
                  </select>
                </label>
              </div>
            )}
            {settingsMsg && <p className="muted">{settingsMsg}</p>}
            <div className="row">
              <label className="muted">배경 형태{' '}
                <select value={bgStyle} disabled={bgBusy || locked} onChange={(e) => setBgStyle(e.target.value)}>
                  {IMAGE_STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
              <button disabled={locked || bgBusy || !Object.values(imgs).some((r) => r.background)} onClick={() => regenBackground()}
                title={locked ? '검수가 완료된 캠페인은 이미지를 다시 만들 수 없습니다' : `${usage?.quality ?? ''} 품질로 배경을 새로 만듭니다`}>
                배경 새로 생성 ({usage?.quality ? `${usage.quality} · ` : ''}{curMode === 'single' ? '1장, 모든 매체가 잘라 씀' : '합성한 비율군마다 1장, 모든 매체에 적용'})
              </button>
              <button disabled={bgBusy || imgBusy !== null} onClick={resetGenerated}>시안 이미지 초기화</button>
              {bgBusy && <span className="muted">배경 생성 후 합성된 매체를 다시 합성하는 중… 1~2분 걸릴 수 있습니다.</span>}
            </div>
            {bgMsg && <div className="notice err">{bgMsg}</div>}
            {bgNote && <p className="muted">{bgNote}</p>}
            {locked && <div className="notice">검수가 완료된 캠페인입니다. 입력 수정과 이미지 재생성은 할 수 없습니다. ('시안 이미지 초기화'를 하면 검수 전 상태로 돌아갑니다)</div>}
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
                      <button className="primary" disabled={locked || imgBusy !== null || bgBusy} onClick={() => makeImage(firstEligible, false)}>
                        모든 지면 이미지 생성
                      </button>
                    )}
                  </div>
                  <ImageGrid items={withCopy.map((g) => ({ ...g, in_image: inImage[g.preset_id] }))} rows={imgs} selected={cur.preset_id} onSelect={(id) => { setImgTab(id); setLightbox(true) }}
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
                        canMake={!locked && (cur.content_kind !== '완성형' || cur.variants.some((v) => v.selected))} onMake={makeImage}
                        inImage={inImage[cur.preset_id]} onInImage={setInImageFor}
                        onEdit={(id) => { setLightbox(false); onEdit(id) }} />
                    </Lightbox>
                  )}
                </>
              )
            })()}
          </div>}

          {view === 'icons' && <IconPanel key={iconKey} designId={designId} versionNo={versionNo} ask
            ready={groups.some((g) => imgs[g.preset_id]?.composite)} onCount={setIconCount} />}
          {view === 'icons' && <PinPanel designId={designId} iconCount={iconCount} />}

          {view !== 'icons' && <div className="pick-bar">
            <span className="pick-text">
              {selected ? <>선택: <strong>시안 {letter(selected)}</strong> · {selected.headline}</> : <span className="muted">사용할 시안을 선택해 주세요.</span>}
            </span>
            <span className="row">
              {hasComposite
                ? <button onClick={() => onEdit(firstComposite!)}>편집 화면으로 →</button>
                : <button className="primary" disabled={!selected} onClick={goImages}>이미지 생성으로 →</button>}
              <button className="primary" disabled={!hasComposite} onClick={onReview}>검수·내보내기 →</button>
            </span>
          </div>}
        </>
      )}

      {view === 'copy' && has && tab === 'copy' && (
        <div className="copy-group">
          <h4>모든 지면 공통 카피 {count}개</h4>
          <p className="muted">문장은 모든 지면이 같습니다. 지면별로 적용한 글자 수 제한과, 그 제한을 넘는 시안만 아래에 표시합니다.</p>
          {assetFields && assetFields.length > 0 && (
            <div className="row">
              <button disabled={busy || assetBusy} onClick={makeAssets}>{textAssets && Object.keys(textAssets).length ? '추가 문구 자산 다시 생성' : '추가 문구 자산 생성'} (채팅 1회, 시안 유지)</button>
              <span className="muted">{assetFields.join(' · ')} — 문구를 여러 개 요구하는 지면용. 검수 '문구 수량'이 이 자산을 판정합니다.</span>
            </div>
          )}
          {noteAssets && <div className="notice ok">{noteAssets}</div>}
          {textAssets && Object.keys(textAssets).length > 0 && (
            <div className="copy-assets">
              <h4>추가 문구 자산 (문구를 여러 개 요구하는 지면용 · 같은 호출에서 생성)</h4>
              {Object.entries(textAssets).map(([field, vals]) => (
                <div key={field}>
                  <strong>{field}</strong> <span className="muted">{vals.length}개</span>
                  <ol className="muted">{vals.map((v, i) => <li key={i}>{v} <span className="muted">({v.length}자)</span></li>)}</ol>
                </div>
              ))}
              <p className="muted">검수의 '문구 수량' 항목이 수량과 글자 수를 판정하고, 내보내기의 지면 폴더에 text_assets.txt 로 담깁니다.</p>
            </div>
          )}
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
                {fitNote && v.selected && <div className="notice ok">{fitNote}</div>}
                {issues.map((x, i) => (
                  <span key={i} className={`badge ${x.severity === '필수' || x.severity === '강제' || x.severity === '수정 필요' ? 'b-need' : 'b-warn'}`}>
                    {x.severity} · {x.placement ? `${x.placement} ` : ''}{x.field}: {x.detail}{x.type === 'legal' && x.law ? ` · 근거: ${x.law}` : ''}
                    {v.selected && 'preset_id' in x && (x.type === 'length' || x.type === 'min_length') && (
                      <button type="button" className="link" style={{ marginLeft: 6 }} disabled={fitting !== null || picking}
                        onClick={() => fitPreset((x as { preset_id: number }).preset_id)}>이 지면에 맞게 줄이기 (채팅 1회)</button>
                    )}
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

// 이미지 안 문구 단계: 제목만 / 제목+본문 / 제목+본문+CTA (설계안 결정 1). 서버 설정 {body, cta} 와 상호 변환.
type InImageLevel = 'title' | 'body' | 'full'
const inImageLevel = (s: InImage) => (s.cta ? 'full' : s.body ? 'body' : 'title') as InImageLevel
const IN_IMAGE_LEVELS: { v: InImageLevel; label: string; body: boolean; cta: boolean }[] = [
  { v: 'title', label: '제목만', body: false, cta: false },
  { v: 'body', label: '제목 + 본문', body: true, cta: false },
  { v: 'full', label: '제목 + 본문 + CTA 버튼', body: true, cta: true },
]
const inImageDefaultLabel = (name: string) => (inImageDefault(name).cta ? '제목 + 본문 + CTA' : '제목만')

function ImageBlock({ g, row, busy, err, canMake, onMake, onEdit, inImage, onInImage }: {
  g: CopyGroup; row?: ImgRow; busy: boolean; err?: string; canMake: boolean
  onMake: (presetId: number, regenerate: boolean) => void; onEdit: (presetId: number) => void
  inImage?: InImage; onInImage?: (presetId: number, body: boolean, cta: boolean) => void
}) {
  const comp = row?.composite
  const sep = g.content_kind === '분리 자산형'
  return (
    <div className="imgblock">
      {sep && <p className="muted">분리 자산형: 텍스트 없는 이미지만 만들고, 로고·카피는 내보내기에서 별도 파일로 제공합니다.</p>}
      {!sep && inImage?.available && onInImage && (
        <div className="row in-image-row">
          <label>이미지 안 문구{' '}
            <select value={inImageLevel(inImage)} disabled={busy}
              onChange={(e) => { const lv = IN_IMAGE_LEVELS.find((l) => l.v === e.target.value); if (lv) onInImage(g.preset_id, lv.body, lv.cta) }}>
              {IN_IMAGE_LEVELS.map((l) => <option key={l.v} value={l.v}>{l.label}</option>)}
            </select>
          </label>
          <span className="muted">
            이 지면 기본: {inImageDefaultLabel(g.name)}{inImageChanged(inImage, g.name) ? ' · 직접 바꿈' : ''}. 본문·CTA 를 이미지 밖에 그리는 매체(Meta·카카오)는 제목만 권장. 바꾸면 배경을 유지한 채 바로 다시 합성합니다(비용 없음).
          </span>
        </div>
      )}
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
        {!canMake && <span className="muted">완성형 지면은 사용할 카피를 먼저 선택해 주세요. (검수 완료된 캠페인은 다시 만들 수 없습니다)</span>}
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

  // 시안 A/B/C 선택기: 편집 화면에서도 시안을 바꿀 수 있다(모든 지면의 문구가 바뀌고 이미지가 다시 합성된다)
  const [variants, setVariants] = useState<Variant[]>([])
  const [switching, setSwitching] = useState(false)
  const [switchNote, setSwitchNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [editVer, setEditVer] = useState(0)       // 다시 합성한 뒤 편집 패널을 새로 불러온다
  const loadVariants = useCallback(() =>
    fetch(`/api/designs/${design.id}/copy`).then((r) => r.json()).then((g: CopyGroup[]) => {
      const first = Array.isArray(g) ? g.find((x) => x.variants.length > 0) : undefined
      setVariants(first?.variants ?? [])
    }).catch(() => undefined), [design.id])
  useEffect(() => { loadVariants() }, [loadVariants])
  const switchVariant = async (no: number) => {
    setSwitching(true); setSwitchNote(null)
    try {
      const { note } = await applyVariant(design.id, no)
      await Promise.all([load(), loadVariants()])
      setEditVer((n) => n + 1)
      setSwitchNote({ ok: true, text: note ?? '시안을 바꿨습니다.' })
    } catch (e) {
      setSwitchNote({ ok: false, text: (e as Error).message })
    } finally { setSwitching(false) }
  }

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
          {variants.length > 0 && (
            <div className="variant-switch">
              <div className="chips">
                <span className="muted">시안</span>
                {variants.map((v) => (
                  <button key={v.variant_no} type="button" className={`chip ${v.selected ? 'on' : ''}`} disabled={switching || v.selected}
                    title={v.headline} onClick={() => switchVariant(v.variant_no)}>
                    {LETTERS[v.variant_no - 1] ?? v.variant_no} · {v.headline}
                  </button>
                ))}
              </div>
              <p className="muted">시안을 바꾸면 모든 지면의 문구가 바뀌고 이미지가 다시 합성됩니다. 저장해 둔 위치·크기는 유지되고, 저장하지 않은 편집은 사라집니다.</p>
              {switchNote && <div className={switchNote.ok ? 'notice ok' : 'notice err'}>{switchNote.text}</div>}
            </div>
          )}
          <ImageGrid
            items={design.presets.map((p) => ({ preset_id: p.id, platform: p.platform, name: p.name, width: p.width, height: p.height }))}
            rows={imgs} selected={cur?.id ?? null} onSelect={onPick}
            dimmed={(_it, row) => !row?.composite ? '합성 이미지 없음' : row.composite.layout.separate_assets ? '분리 자산형 — 문구·로고를 합성하지 않아 편집 대상이 아닙니다' : null}
            badge={(it, row) => row?.composite && !row.composite.layout.separate_assets
              ? <span className={`badge ${cur?.id === it.preset_id ? 'b-ok' : 'b-warn'}`}>{cur?.id === it.preset_id ? '편집 중' : '편집 가능'}</span>
              : null} />
          <p className="muted">카드를 누르면 그 지면을 편집합니다. 잠긴 요소(로고)는 바꿀 수 없고, 저장하면 검수 결과가 무효화되어 다시 검수해야 합니다.</p>
          <DevStats designId={design.id} kinds={['background', 'variant', 'copy']} label="편집 화면 재생성" refreshKey={Object.values(imgs).map((r) => r?.composite?.file ?? '').join(',')} />
          {cur && <EditPanel key={`${cur.id}-${editVer}`} designId={design.id} presetId={cur.id} onClose={onBack} onSaved={load} />}
        </>
      )}
    </section>
  )
}

type IconItem = {
  id: number; position: number; name: string; concept?: string; width: number; height: number
  render: '3d' | 'svg'; has_svg: boolean; has_source: boolean
  style: { render?: string; stroke_width?: number; corner: string; fill?: string; color?: string; colors?: string[]; model?: string; quality?: string; bbox?: number[]
    origin?: { grid?: string } | null; source_size?: number[] }
  violations: { detail: string }[]
}

function IconPanel({ designId, versionNo, ready, ask, onCount }: { designId: number; versionNo: number | null; ready: boolean; ask: boolean; onCount?: (n: number) => void }) {
  const [items, setItems] = useState<IconItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [want, setWant] = useState(false)      // 아이콘도 생성할지 여부
  const [busy, setBusy] = useState(false)
  const [one, setOne] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [ver, setVer] = useState(0)   // 이미지 캐시 무효화
  const [target, setTarget] = useState(10)   // 세트 목표 개수 (서버 설정)
  const [gridCells, setGridCells] = useState(0)   // 격자 렌더 칸 수(0 이면 아이콘마다 1장). 호출 수 = ceil(개수 / 칸 수)
  const [gridLabel, setGridLabel] = useState<string | null>(null)
  const calls = (n: number) => (gridCells >= 2 ? Math.ceil(n / gridCells) : n)
  const [filling, setFilling] = useState(false)
  const [fresh, setFresh] = useState<number[]>([])   // 방금 렌더된 아이콘 id (등장 애니메이션)
  const markFresh = (before: IconItem[], after: IconItem[]) => {
    const old = new Set(before.map((a) => a.id))
    setFresh(after.filter((a) => !old.has(a.id)).map((a) => a.id))
  }
  // 렌더하는 동안 덮개에 깔 스케치 카드: 시안 브리프의 기능명 후보(비용 없음) 중 아직 렌더하지 않은 것. 서버가 고르는 순서와 같다
  const sketchCands = async (n: number): Promise<SketchCard[]> => {
    try {
      const d = await fetch(`/api/designs/${designId}`).then((r) => r.json())
      const done = new Set(items.map((a) => a.name))
      const list = ((d?.brief?.icon_candidates ?? []) as { name?: string; concept?: string }[])
        .filter((c) => c.name && !done.has(c.name)).slice(0, n).map((c) => ({ name: c.name as string, concept: c.concept }))
      return list.length ? list : Array.from({ length: n }, () => ({ name: '?', concept: '기능명을 정하는 중' }))
    } catch { return [] }
  }
  // 기능명 후보(렌더 전, 비용 없음): 카피 호출에서 받은 15개. 사용자가 고른 것만 렌더한다(쓰지 않을 아이콘은 만들지 않음)
  type Cand = { name: string; concept: string; rendered: boolean }
  const [cands, setCands] = useState<Cand[] | null>(null)
  const [candErr, setCandErr] = useState<string | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const loadCands = async (preselect: number) => {
    setCandErr(null)
    try {
      const r = await fetch(`/api/designs/${designId}/icons/candidates`)
      const j = await r.json()
      if (!r.ok) throw new Error(j?.detail || '후보를 불러오지 못했습니다')
      const list: Cand[] = j.candidates ?? []
      setCands(list)
      setPicked(list.filter((c) => !c.rendered).slice(0, Math.max(0, preselect)).map((c) => c.name))
    } catch (e) { setCandErr((e as Error).message) }
  }
  const togglePick = (name: string) => setPicked((p) => (p.includes(name) ? p.filter((x) => x !== name) : [...p, name]))
  const renderPicked = () => overlay.run(`고른 아이콘 ${picked.length}개 렌더하는 중… (1개당 10~20초)`, renderPickedInner,
    { sketch: picked.map((n) => ({ name: n, concept: cands?.find((c) => c.name === n)?.concept })) })
  const renderPickedInner = async () => {
    setBusy(true); setErr(null); setNote(null)
    try {
      const j = await post(`/api/designs/${designId}/icons`, { names: picked })
      markFresh(items, j.icons); setItems(j.icons); setVer((v) => v + 1)
      setCands((l) => l && l.map((c) => ({ ...c, rendered: c.rendered || j.icons.some((a: IconItem) => a.name === c.name) })))
      setPicked([])
      if (!j.complete) setNote(`${j.made}개를 렌더했습니다. 세트는 ${j.icons.length}/${j.requested}개 — 검수·내보내기에는 ${j.requested}개가 필요합니다.`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const candidatePicker = (room: number) => cands && (
    <div className="icon-cands">
      <p className="muted">기능명 후보 {cands.length}개 — 쓸 것만 골라 렌더합니다 ({gridCells >= 2 ? `${gridLabel} 격자 한 장에 최대 ${gridCells}개` : '1개당 이미지 1장'}). 남은 자리 {room}개.</p>
      <div className="icon-cand-grid">
        {cands.map((c) => (
          <label key={c.name} className={`icon-cand ${c.rendered ? 'done' : ''}`} title={c.concept}>
            <input type="checkbox" disabled={c.rendered || busy || (!picked.includes(c.name) && picked.length >= room)}
              checked={c.rendered || picked.includes(c.name)} onChange={() => togglePick(c.name)} />
            <span>{c.name}</span>{c.rendered && <span className="muted"> (완료)</span>}
            {c.concept && <span className="muted icon-cand-concept">{c.concept}</span>}
          </label>
        ))}
      </div>
      <div className="row">
        <button className="primary" disabled={busy || picked.length === 0} onClick={renderPicked}>선택한 {picked.length}개 렌더 (이미지 {calls(picked.length)}장{gridCells >= 2 ? `, ${gridLabel} 격자` : ''})</button>
      </div>
    </div>
  )

  useEffect(() => {
    fetch(`/api/designs/${designId}/icons`).then((r) => r.json()).then((l) => { if (Array.isArray(l)) setItems(l); setLoaded(true) })
    fetch('/api/icon-settings').then((r) => r.json()).then((j) => { if (j?.count) setTarget(j.count); setGridCells(j?.grid_cells ?? 0); setGridLabel(j?.grid ?? null) }).catch(() => undefined)
  }, [designId])
  useEffect(() => { if (loaded) onCount?.(items.length) }, [loaded, items.length, onCount])

  const generate = async () => overlay.run('아이콘 세트 만드는 중… 기능명을 정하고 10개를 렌더합니다 (2~3분)', generateInner,
    { sketch: await sketchCands(target) })
  const generateInner = async () => {
    setBusy(true); setErr(null); setNote(null)
    try {
      const j = await post(`/api/designs/${designId}/icons`, {})
      markFresh(items, j.icons); setItems(j.icons); setVer((v) => v + 1)
      if (!j.complete) setNote(`목표 ${j.requested}개 중 ${j.made}개만 만들어졌습니다. 아래 '부족한 아이콘 채우기'로 나머지를 이어서 만들 수 있습니다.`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // 세트가 목표 개수에 못 미칠 때(크레딧 소진 등) 빠진 만큼만 이어서 만든다. 아이콘 1개당 이미지 1장 비용
  const fill = async () => overlay.run('부족한 아이콘 채우는 중… (1개당 10~20초)', () => fillInner(),
    { sketch: await sketchCands(target - items.length) })
  const fillInner = async () => {
    setFilling(true); setErr(null); setNote(null)
    try {
      const j = await post(`/api/designs/${designId}/icons/fill`, {})
      markFresh(items, j.icons); setItems(j.icons); setVer((v) => v + 1)
      if (!j.complete) setNote(`${j.made}개를 채웠지만 아직 목표 ${j.requested}개 중 ${j.icons.length}개입니다. 다시 시도할 수 있습니다.`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setFilling(false)
    }
  }

  // 세트 초기화: 이미지 초기화와 별개. 규칙 버전 소유라 같은 버전을 쓰는 다른 캠페인에서도 사라진다
  const resetSet = async () => {
    if (!window.confirm(`이 브랜드 규칙 버전의 아이콘 세트 ${items.length}개를 모두 지웁니다.\n같은 규칙 버전을 쓰는 다른 캠페인에서도 사라지며, 이미지·카피는 그대로입니다.\n계속할까요?`)) return
    setBusy(true); setErr(null); setNote(null)
    try {
      const j = await post(`/api/designs/${designId}/icons/reset`, {})
      setItems([]); setVer((v) => v + 1); setCands(null); setPicked([])
      setNote(`아이콘 ${j.icons_deleted}개를 지웠습니다. 기능명 후보는 남아 있어 다시 만들 때 제안 호출 없이 씁니다.`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const redraw = (id: number) => overlay.run('아이콘 다시 그리는 중… (10~20초)', () => redrawInner(id), {
    sketch: items.filter((a) => a.id === id).map((a) => ({ name: a.name, concept: a.concept })) })
  const redrawInner = async (id: number) => {
    setOne(id); setErr(null)
    try {
      const a = await post(`/api/designs/${designId}/icons/${id}/regenerate`, {})
      setItems((l) => l.map((x) => (x.id === id ? a : x))); setVer((v) => v + 1); setFresh([a.id])
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setOne(null)
    }
  }

  if (!loaded) return null
  const st = items[0]?.style
  // 3D 원본 표기: 1장 렌더는 1024, 격자 렌더는 잘라낸 조각이라 아이콘마다 크기가 다르다(source_size 가 없는 예전 아이콘은 대략값)
  const srcGrid = st?.origin?.grid
  const srcLong = items.map((i) => i.style.source_size).filter((v): v is number[] => !!v).map((v) => Math.max(v[0], v[1]))
  const srcText = !srcGrid ? '원본 1024'
    : srcLong.length === items.length ? `원본: ${srcGrid} 격자에서 잘라낸 ${Math.min(...srcLong)}~${Math.max(...srcLong)}px 조각`
    : `원본: ${srcGrid} 격자에서 잘라낸 약 200~350px 조각`
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
        <DevStats designId={designId} kinds={['icon', 'icon_names', 'icon_redraw']} label="아이콘 생성" refreshKey={`${busy}-${filling}-${one}-${items.length}-${ver}`} />
        <label className="icon-ask">
          <input type="checkbox" checked={want} disabled={busy} onChange={(e) => setWant(e.target.checked)} />
          {vLabel}의 아이콘 세트도 함께 만들기
        </label>
        <p className="muted">
          브랜드에 어울리는 3D 아이콘 세트입니다. 기능명 10개는 상품 설명을 바탕으로 AI가 정하고, 아이콘마다 투명 배경 PNG 를 렌더합니다.
          재질·광원은 브랜드 이미지 톤, 색은 주조·보조·강조색, 모서리는 아이콘 스타일 값으로 모든 아이콘에 동일하게 적용됩니다.
          이 규칙 버전의 아이콘 세트는 한 번만 만들 수 있고, 이후에는 개별 아이콘만 다시 그릴 수 있습니다. {gridCells >= 2 ? `격자 렌더(${gridLabel})로 한 장에 최대 ${gridCells}개를 그려 자르므로 ${target}개가 이미지 ${calls(target)}장입니다. 개별 다시 그리기는 1장씩입니다.` : '아이콘 1개당 이미지 1장 비용이 발생합니다.'}
        </p>
        {want && (
          <div className="row">
            <button className="primary" disabled={busy} onClick={generate}>아이콘 세트 생성 (후보에서 {target}개 자동, 이미지 {calls(target)}장{gridCells >= 2 ? ` · ${gridLabel} 격자` : ''})</button>
            {!cands && <button disabled={busy} onClick={() => loadCands(target)}>후보를 보고 고르기 (비용 없음)</button>}
            {busy && <span className="muted">기능을 정하고 아이콘 10개를 렌더하는 중… 2~3분 걸릴 수 있습니다.</span>}
          </div>
        )}
        {want && candErr && <div className="notice err">{candErr}</div>}
        {want && candidatePicker(target)}
        {err && <div className="notice err">{err}</div>}
        {note && <div className="notice ok">{note}</div>}
      </div>
    )
  }

  return (
    <div className="imgblock icon-step">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <strong>아이콘 세트 · {vLabel}</strong>
        <button type="button" disabled={busy || filling || one !== null} onClick={resetSet} title="아이콘 세트만 지웁니다. 시안 이미지 초기화와는 별개입니다">아이콘 세트 초기화</button>
      </div>
      <DevStats designId={designId} kinds={['icon', 'icon_names', 'icon_redraw']} label="아이콘 생성" refreshKey={`${busy}-${filling}-${one}-${items.length}-${ver}`} />
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
          {!filling && <span className="muted">({gridCells >= 2 ? `격자 렌더 · 이미지 ${calls(target - items.length)}장` : '아이콘 1개당 이미지 1장 비용'})</span>}
          {!cands && <button disabled={busy || filling} onClick={() => loadCands(target - items.length)}>후보를 보고 고르기 (비용 없음)</button>}
        </div>
      )}
      {items.length < target && candErr && <div className="notice err">{candErr}</div>}
      {items.length < target && candidatePicker(target - items.length)}
      {st && items[0].render === '3d' ? (
        <p className="muted">
          적용 스타일 — 3D 렌더({st.model} {st.quality}) · 모서리 {st.corner} · 색 {(st.colors || []).join(', ')} · 투명 배경 · 크기 {items[0].width}×{items[0].height}px ({srcText})
        </p>
      ) : st && (
        <p className="muted">
          적용 스타일 — 선 굵기 {st.stroke_width} · 모서리 {st.corner} · {st.fill} · 색 {st.color} · 크기 {items[0].width}×{items[0].height}px
        </p>
      )}
      <div className="icongrid">
        {items.map((a) => (
          <figure key={`${a.id}-${fresh.includes(a.id) ? ver : 0}`} className={fresh.includes(a.id) ? 'iconcell icon-reveal' : 'iconcell'}
            style={fresh.includes(a.id) ? { animationDelay: `${fresh.indexOf(a.id) * 90}ms` } : undefined}>
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

type PinPiece = { slot: number; kind: 'logo' | 'word' | 'name' | 'topic'; label: string; width?: number; height?: number; missing?: boolean; retried?: boolean }
type PinItem = { id: number; width: number; height: number; created_at: string; pins: PinPiece[]
  meta: { name: string; word: string; topics: string[]; logo: boolean; model?: string; quality?: string; grid?: string; retried?: number[]; missing?: string[] } }
type PinTopic = { name: string; concept: string }

// 핀 뱃지 세트: 아이콘 격자와 같은 방식 — 4×2 격자 1장(투명)에 핀 8개(로고 · 단어 · 이름 · 아이콘 세트 주제 5)를 그려 잘라 쓰고,
// 잘라낸 핀으로 4:5 시트를 배치한다. 격자 1장 + 못 잘라낸 칸만 1장씩 비용. iconCount 가 바뀌면(아이콘 세트 생성·채우기) 주제를 다시 불러온다
function PinPanel({ designId, iconCount }: { designId: number; iconCount?: number }) {
  const [pins, setPins] = useState<PinItem[]>([])
  const [info, setInfo] = useState<{ has_logo: boolean; grid: string; topic_source: string; topic_count: number } | null>(null)
  const [topics, setTopics] = useState<PinTopic[]>([])
  const [picked, setPicked] = useState<string[]>([])
  const [name, setName] = useState('')
  const [word, setWord] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [fresh, setFresh] = useState<number | null>(null)
  const need = info?.topic_count ?? 5

  useEffect(() => {
    fetch(`/api/designs/${designId}/pins`).then((r) => r.json()).then((j) => {
      if (!Array.isArray(j?.pins)) return
      setPins(j.pins); setInfo({ has_logo: j.has_logo, grid: j.grid, topic_source: j.topic_source, topic_count: j.topic_count })
      const list: PinTopic[] = j.topics ?? []
      setTopics(list)
      // 고른 주제가 아직 목록에 있으면 유지하고, 아니면 앞에서부터 채운다
      setPicked((p) => {
        const keep = p.filter((n) => list.some((t) => t.name === n))
        return keep.length === j.topic_count ? keep : list.slice(0, j.topic_count).map((t) => t.name)
      })
      setName((v) => v || (j.defaults?.name ?? '')); setWord((v) => v || (j.defaults?.word ?? ''))
    }).catch(() => undefined)
  }, [designId, iconCount])

  const toggle = (n: string) => setPicked((p) => (p.includes(n) ? p.filter((x) => x !== n) : p.length < need ? [...p, n] : p))
  const make = () => overlay.run(`핀 뱃지 세트 만드는 중… ${info?.grid ?? '4x2'} 격자 한 장에 핀 8개를 그려 잘라냅니다 (30초~1분)`, makeInner, {
    sketch: [{ name: '로고', concept: '로고 핀' }, { name: word, concept: '단어 핀' }, { name, concept: '이름 핀' },
      ...picked.map((n) => ({ name: n, concept: topics.find((t) => t.name === n)?.concept }))] })
  const makeInner = async () => {
    setBusy(true); setErr(null)
    try {
      const p: PinItem = await post(`/api/designs/${designId}/pins`, { name, word, topics: picked })
      setPins((l) => [p, ...l]); setFresh(p.id)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const remove = async (id: number) => {
    if (!window.confirm('이 핀 뱃지 세트(시트와 잘라낸 핀)를 지웁니다. 계속할까요?')) return
    const r = await fetch(`/api/designs/${designId}/pins/${id}`, { method: 'DELETE' })
    if (r.ok) setPins((l) => l.filter((p) => p.id !== id))
    else setErr('지우지 못했습니다')
  }

  if (!info) return null
  const fromIcons = info.topic_source === 'icons'
  return (
    <div className="imgblock icon-step">
      <strong>핀 뱃지 세트</strong>
      <DevStats designId={designId} kinds={['pin']} label="핀 뱃지 생성" refreshKey={`${busy}-${pins.length}`} />
      <p className="muted">
        아이콘 세트와 같은 방식으로 {info.grid} 격자 한 장에 극사실주의 일러스트 에나멜 핀 8개를 투명 배경으로 그려 하나씩 잘라내고, 잘라낸 핀으로 4:5 시트를 배치합니다.
        핀은 브랜드 로고{info.has_logo ? '(등록한 로고를 참고 이미지로 첨부)' : '(로고가 없어 이름 첫 글자 모노그램)'} · 아래 단어 · 이름 · 아이콘 세트 주제 {need}개입니다.
        격자 1장 비용이고, 격자에서 잘라내지 못한 핀만 1장씩 다시 그립니다.
      </p>
      <div className="row pin-form">
        <label>이름 핀 <input value={name} maxLength={20} disabled={busy} onChange={(e) => setName(e.target.value)} /></label>
        <label>나를 표현하는 단어 <input value={word} maxLength={12} disabled={busy} placeholder="예: 호기심" onChange={(e) => setWord(e.target.value)} /></label>
      </div>
      {topics.length < need ? (
        <div className="notice err">핀 주제는 아이콘 세트에서 가져옵니다. 아이콘 세트(또는 기능명 후보)가 {need}개 이상 있어야 만들 수 있습니다.</div>
      ) : (
        <div className="icon-cands">
          <p className="muted">주제 {need}개 고르기 — {fromIcons ? '이 규칙 버전의 아이콘 세트' : '아직 아이콘 세트가 없어 기능명 후보'}에서 ({picked.length}/{need})</p>
          <div className="icon-cand-grid">
            {topics.map((t) => (
              <label key={t.name} className="icon-cand" title={t.concept}>
                <input type="checkbox" disabled={busy || (!picked.includes(t.name) && picked.length >= need)}
                  checked={picked.includes(t.name)} onChange={() => toggle(t.name)} />
                <span>{t.name}</span>
                {t.concept && <span className="muted icon-cand-concept">{t.concept}</span>}
              </label>
            ))}
          </div>
        </div>
      )}
      <div className="row">
        <button className="primary" disabled={busy || !word.trim() || picked.length !== need} onClick={make}>
          {pins.length ? '한 장 더 만들기' : '핀 뱃지 세트 만들기'} (격자 이미지 1장)
        </button>
      </div>
      {err && <div className="notice err">{err}</div>}
      {pins.map((p) => (
        <figure key={p.id} className={fresh === p.id ? 'pinset icon-reveal' : 'pinset'}>
          <a className="pinsheet" href={`/api/pins/${p.id}/png`} target="_blank" rel="noreferrer"><img src={`/api/pins/${p.id}/png`} alt={`핀 뱃지 시트: ${p.meta.name}`} /></a>
          <div className="pinset-body">
            <figcaption className="muted">
              “{p.meta.name}” · “{p.meta.word}” · 주제 {(p.meta.topics || []).join(', ')} · 시트 {p.width}×{p.height}px · {fmtDate(p.created_at)}
            </figcaption>
            {(p.meta.missing?.length ?? 0) > 0 && <span className="badge b-need">잘라내지 못한 핀: {p.meta.missing!.join(', ')} — 한 장 더 만들어 주세요</span>}
            <div className="pin-pieces">
              {p.pins.map((x) => (
                <a key={x.slot} className={x.missing ? 'pin-piece missing' : 'pin-piece'} href={x.missing ? undefined : `/api/pins/${p.id}/pieces/${x.slot}`}
                  target="_blank" rel="noreferrer" title={x.retried ? '격자에서 잘라내지 못해 1장으로 다시 그림' : undefined}>
                  {x.missing ? <span className="pin-piece-empty">없음</span> : <img src={`/api/pins/${p.id}/pieces/${x.slot}`} alt={x.label} />}
                  <span>{x.label}{x.retried ? ' *' : ''}</span>
                </a>
              ))}
            </div>
            <div className="row">
              <a href={`/api/pins/${p.id}/png`} download={`pins_${p.id}.png`}>4:5 시트 PNG</a>
              <a href={`/api/pins/${p.id}/grid`} target="_blank" rel="noreferrer">격자 원본</a>
              <button type="button" onClick={() => remove(p.id)}>지우기</button>
            </div>
          </div>
        </figure>
      ))}
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
// 저장된 캠페인 입력을 폼 값으로 (캠페인 열기·수정 취소)
const briefOf = (d: Design): Brief => ({ product_name: d.brief.product_name, description: d.brief.description ?? '', key_message: d.brief.key_message ?? '', target: d.brief.target ?? '', product_image_asset_id: d.brief.product_image_asset_id ?? null, image_style: d.brief.image_style ?? '자동',
  required_phrases: d.brief.required_phrases ?? [], forbidden_phrases: d.brief.forbidden_phrases ?? [] })

type BrandLite = { id: number; name: string; description?: string; current_version_no: number | null }

export default function DesignPage({ workBrandId, onWorkBrand, onGoBrand }: {
  workBrandId: number | null; onWorkBrand: (id: number | null) => void; onGoBrand?: () => void
}) {
  const [brands, setBrands] = useState<BrandLite[]>([])
  const [platforms, setPlatforms] = useState<PlatformLite[]>([])
  const [designs, setDesigns] = useState<Design[]>([])
  const [iconCount, setIconCount] = useState(0)   // 아이콘 세트 캠페인: 아이콘 개수가 바뀌면 핀 뱃지 주제를 다시 불러온다
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
  // 3열 작업 화면: 왼쪽 메뉴가 고른 항목(view)이 가운데에 보인다. screen 은 상단 단계 표시와 호환용으로 함께 맞춘다
  const [view, setView] = useState<View>('master')
  const [wsTick, setWsTick] = useState(0)        // 레일·메뉴 상태 다시 불러오기
  const goView = (v: View) => { setView(v); setScreen(v === 'edit' ? 'edit' : v === 'review' ? 'review' : 'design'); window.scrollTo(0, 0) }
  const goEdit = (presetId: number | null = null) => { setEditPreset(presetId); goView('edit') }
  const goScreen = (s: Screen) => goView(s === 'edit' ? 'edit' : s === 'review' ? 'review' : 'copy')
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
  // 이 시안의 생성 작업을 기다리는 동안 덮개에 브랜드 퀴즈를 띄운다(덮개를 띄울 때마다 새로 받음, 비용 없음)
  useEffect(() => {
    if (activeId === null) return
    overlay.setQuizSource(() => fetch(`/api/designs/${activeId}/quiz`).then((r) => r.json()).then((j) => (j.items ?? []) as QuizItem[]))
    return () => overlay.setQuizSource(null)
  }, [activeId])
  const ws = useWorkspace(active?.id ?? null, active?.brand.id ?? null, active?.brand.name ?? '', `${activeId}-${wsTick}-${copyKey}-${view}-${active?.status}`)
  useEffect(() => { setView('master') }, [activeId])
  useEffect(() => { if (hasCopy === false) setView('setup') }, [hasCopy])

  const openCampaign = (d: Design) => {
    setActiveId(d.id); setMsg(null)
    setBrief(briefOf(d))
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
  // 수정 취소: 저장하지 않은 입력·규격 선택을 저장된 값으로 되돌리고 입력 요약으로 돌아간다
  const cancelEdit = () => {
    if (active) { setBrief(briefOf(active)); setPicked(active.presets.map((p) => p.id)) }
    setEditing(false)
  }
  const reviewed = !!ws.data.review?.reviewed   // 유효한 검수 결과가 있는지 (입력을 저장하면 무효화된다)
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
      setEditing(false); setWsTick((t) => t + 1)
      setMsg({ ok: true, text: reviewed
        ? '입력을 저장했습니다. 기존 검수 결과는 무효화되었으니 ⑦ 검수·내보내기에서 다시 검수해 주세요.'
        : '입력을 저장했습니다.' })
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
      setHasCopy(true); setEditing(false); setCopyKey((k) => k + 1); goView('copy')
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


  // 지면별 '이미지 안 문구' 설정 — 같은 presets 면 같은 객체를 넘겨 CopyPanel 의 로컬 상태가 렌더마다 초기화되지 않게 한다
  const inImageMap = useMemo(() => Object.fromEntries((active?.presets ?? []).map((p) => [p.id, p.in_image])) as Record<number, InImage | undefined>, [active?.presets])
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
      <button type="button" className={!active || screen === 'design' ? 'on' : ''} onClick={() => goView(hasCopy ? 'copy' : 'setup')}>2. 규격·메시지·시안</button>
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
        {hasCopy && reviewed && (
          <div className="notice">이미 검수한 캠페인입니다. 입력을 저장하면 기존 검수 결과가 무효화되어 ⑦ 검수·내보내기에서 다시 검수해야 합니다.</div>
        )}
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
          <label className="msg-field">상품 설명 <span className="opt">(선택)</span>
            <textarea value={brief.description} maxLength={1000} rows={3} placeholder="예: 산지 직송 채소와 과일을 새벽 7시 전에 문 앞까지 배송합니다"
              onChange={(e) => setBrief({ ...brief, description: e.target.value })} />
            <span className="counter">{brief.description.length}/1000</span>
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
          {hasCopy && <button type="button" className="link" disabled={busy} onClick={cancelEdit}>수정 취소</button>}
        </section>
      </aside>
    </div>
  )
  return (
    <>
      {Stepper}
      <main className="page wide ws-page">
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
            <IconPanel designId={d.id} versionNo={d.brand.version_no} ready ask={false} onCount={setIconCount} />
            <PinPanel designId={d.id} iconCount={iconCount} />
          </section>
        ) : hasCopy === null ? (
          <p className="muted">불러오는 중…</p>
        ) : (() => {
          const w = ws.data
          const dot = (st: 'ok' | 'warn' | 'bad' | 'none' | 'cur') => <span className={`ws-dot ${st}`} aria-hidden="true" />
          const setupOk = d.presets.length > 0 && !!d.brief.product_name
          const copySt = w.selected ? 'ok' : w.copyCount > 0 ? 'warn' : 'none'
          const imgSt = w.compositeCount === 0 ? 'none' : w.compositeCount >= w.presetCount ? 'ok' : 'warn'
          const iconSt = w.iconCount === 0 ? 'none' : w.iconCount >= w.iconTarget ? 'ok' : 'warn'
          const rv = w.review
          const reviewSt = !rv || !rv.reviewed ? 'none' : rv.blockers > 0 ? 'bad' : rv.exportReady ? 'ok' : 'warn'
          const items: { v: View; label: string; st: 'ok' | 'warn' | 'bad' | 'none' | 'cur'; sub: string; disabled?: boolean }[] = [
            { v: 'master', label: '보기 전용 시안', st: 'cur', sub: '로고·제목·본문·CTA 한 장' },
            { v: 'setup', label: '① 규격·메시지', st: setupOk ? 'ok' : 'warn', sub: `규격 ${d.presets.length}개 · ${d.brief.key_message ? '메시지 입력됨' : '메시지 없음'}` },
            { v: 'copy', label: '③ 카피·시안', st: copySt, sub: w.selected ? `시안 선택됨 · ${w.selected.headline}` : w.copyCount ? `시안 ${w.copyCount}개 · 미선택` : '아직 없음', disabled: !hasCopy },
            { v: 'images', label: '④ 이미지', st: imgSt, sub: `합성본 ${w.compositeCount}/${w.presetCount}`, disabled: !hasCopy },
            { v: 'icons', label: '⑤ 아이콘', st: iconSt, sub: w.iconCount ? `${w.iconCount}/${w.iconTarget}개` : '아직 없음', disabled: !hasCopy },
            { v: 'edit', label: '⑥ 편집', st: w.compositeCount ? 'ok' : 'none', sub: w.compositeCount ? '지면별 문구·로고 조정' : '합성본이 있어야 열림', disabled: !hasCopy || w.compositeCount === 0 },
            { v: 'review', label: '⑦ 검수·내보내기', st: reviewSt, sub: rv?.reviewed ? `차단 ${rv.blockers} · 경고 ${rv.warn} · 통과 ${rv.pass}` : '검수 전', disabled: !hasCopy },
          ]
          const locked = d.status === '검수 완료'
          const eff = d.effective_phrases
          const campInput = (
            <section className="card camp-input">
              <div className="camp-input-top">
                <div>
                  <div className="camp-label">캠페인 입력 값{locked ? ' · 검수 완료(수정 불가)' : ''}</div>
                  <div className="camp-msg">{d.brief.key_message || d.brief.product_name}</div>
                </div>
                <button type="button" disabled={locked} title={locked ? '검수가 완료된 캠페인은 수정할 수 없습니다' : undefined}
                  onClick={() => { setBrief(briefOf(d)); setPicked(d.presets.map((p) => p.id)); setEditing(true) }}>입력 수정</button>
              </div>
              <dl className="camp-input-grid">
                <dt>서비스·상품</dt><dd>{d.brief.product_name}</dd>
                <dt>상품 설명</dt><dd>{d.brief.description || <span className="muted">없음</span>}</dd>
                <dt>핵심 메시지</dt><dd>{d.brief.key_message || <span className="muted">없음</span>}</dd>
                <dt>타깃</dt><dd>{d.brief.target || <span className="muted">없음</span>}</dd>
                <dt>배경 형태</dt><dd>{d.brief.image_style ?? '자동'}</dd>
                <dt>제품 이미지</dt><dd>{d.brief.product_image_asset_id
                  ? <img className="prod-thumb" src={`/api/assets/${d.brief.product_image_asset_id}/file`} alt="제품 이미지" />
                  : <span className="muted">없음</span>}</dd>
                <dt>필수 표현</dt><dd>{eff ? (eff.required_phrases.join(', ') || '없음') : '-'}{eff ? <span className="muted"> ({eff.required_source})</span> : null}</dd>
                <dt>금지 표현</dt><dd>{eff ? (eff.forbidden_phrases.join(', ') || '없음') : '-'}{eff ? <span className="muted"> ({eff.forbidden_source})</span> : null}</dd>
                <dt>규격</dt><dd>{d.presets.map((p) => `${p.platform} · ${p.name}`).join(' / ') || '없음'}</dd>
              </dl>
              {!locked && <p className="muted">검수 완료 전까지는 입력을 수정할 수 있습니다. 저장하면 기존 검수 결과는 무효화되어 다시 검수해야 합니다{reviewed ? ' (현재 검수 결과 있음)' : ''}. 입력을 바꿔도 이미 만든 카피·이미지는 자동으로 바뀌지 않으니, 필요하면 다시 생성하세요.</p>}
            </section>
          )
          const panelTitle = view === 'copy' ? '③ 카피·시안 선택' : view === 'images' ? '④ 이미지 생성·편집' : '⑤ 아이콘 세트'
          const center = view === 'master' ? (
            <section className="card step-card" id="sec-master">
              <header><h3>보기 전용 시안</h3><span className="muted">실제 합성본 + 매체가 이미지 밖에 그리는 문구·버튼 · 검수·내보내기 대상 아님</span></header>
              <MasterPreview data={w} presets={d.presets} />
              <div className="row" style={{ marginTop: 10 }}>
                <button type="button" onClick={() => goView('copy')} disabled={!hasCopy}>문구 바꾸기 (③ 카피)</button>
                <button type="button" onClick={() => goView('images')} disabled={!hasCopy}>배경 바꾸기 (④ 이미지)</button>
              </div>
            </section>
          ) : view === 'setup' ? (
            (!hasCopy || (editing && d.status !== '검수 완료')) ? setupSteps : campInput
          ) : view === 'edit' && hasCopy ? (
            <EditScreen key={d.id} design={d} presetId={editPreset} onPick={setEditPreset}
              onBack={() => goView('images')} onReview={() => goView('review')} />
          ) : view === 'review' && hasCopy ? (
            <section className="card step-card" id="sec-review">
              <header>
                <h3><span className="step-no">⑦</span> 검수·내보내기</h3>
                <div className="row">
                  <button type="button" onClick={() => goEdit(editPreset)}>← 편집으로</button>
                  <button type="button" onClick={() => goView('images')}>이미지로</button>
                </div>
              </header>
              <ReviewPanel designId={d.id} />
            </section>
          ) : hasCopy ? (
            <section className="card step-card" id="sec-copy">
              <header><h3>{panelTitle}</h3>{view !== 'copy' && <span className="muted">공통 문구: {w.selected ? `시안 ${LETTERS[w.selected.variant_no - 1] ?? w.selected.variant_no} · ${w.selected.headline}` : '미선택'}</span>}</header>
              <CopyPanel key={copyKey} designId={d.id} brandName={d.brand.name ?? ''} versionNo={d.brand.version_no} locked={d.status === '검수 완료'}
                imageStyle={d.brief.image_style ?? '자동'} genSettings={{ image_model: d.brief.image_model, bg_mode: d.brief.bg_mode }}
                textAssets={d.brief.text_assets} assetFields={d.text_asset_fields} view={view as PanelView}
                inImage={inImageMap}
                onDesignChange={() => { loadDesigns(); setWsTick((t) => t + 1) }}
                onEdit={goEdit} onReview={() => goView('review')} onGoImages={() => goView('images')} />
            </section>
          ) : setupSteps
          return (
            <div className="ws">
              <nav className="ws-nav" aria-label="작업 항목">
                {items.map((it) => (
                  <button key={it.v} type="button" className={`ws-item ${view === it.v ? 'on' : ''}`} aria-current={view === it.v ? 'true' : undefined}
                    disabled={it.disabled} onClick={() => goView(it.v)}>
                    {dot(view === it.v ? 'cur' : it.st)}
                    <span className="ws-name">{it.label}</span>
                    <span className="ws-sub">{it.sub}</span>
                  </button>
                ))}
              </nav>
              <div className="ws-main">{center}</div>
              <aside className="ws-rail">
                <section className="rail-box">
                  <h4>보기 전용 시안</h4>
                  <MasterPreview data={w} presets={d.presets} compact />
                  <button type="button" className="link" onClick={() => goView('master')}>크게 보기</button>
                </section>
                <section className="rail-box">
                  <h4>검수 상태</h4>
                  {rv?.reviewed ? (
                    <div className="rail-pills">
                      <span className={`badge ${rv.blockers ? 'b-need' : 'b-ok'}`}>차단 {rv.blockers}</span>
                      <span className={`badge ${rv.warn ? 'b-warn' : 'b-ok'}`}>경고 {rv.warn}</span>
                      <span className="badge b-ok">통과 {rv.pass}</span>
                      {rv.exportReady && <span className="badge b-ok">내보내기 가능</span>}
                    </div>
                  ) : <p className="muted">아직 검수하지 않았습니다.</p>}
                  <button type="button" className="link" disabled={!hasCopy} onClick={() => goView('review')}>검수·내보내기 →</button>
                </section>
                <section className="rail-box">
                  <h4>호출 · 비용 <span className="muted">(이 캠페인)</span></h4>
                  {w.calls ? (
                    <dl className="rail-kv">
                      <dt>이미지</dt><dd>{w.calls.images}회</dd>
                      <dt>채팅</dt><dd>{w.calls.chats}회</dd>
                      <dt>추정 비용</dt><dd>${w.calls.costUsd.toFixed(3)}{w.calls.costKrw != null ? ` (약 ${w.calls.costKrw.toLocaleString()}원)` : ''}{w.calls.estimated ? ' *' : ''}</dd>
                      {w.usage && <><dt>오늘 한도</dt><dd>{w.usage.used}/{w.usage.limit}</dd></>}
                    </dl>
                  ) : <p className="muted">호출 기록 없음</p>}
                </section>
              </aside>
            </div>
          )
        })()}
      </main>
    </>
  )
}
