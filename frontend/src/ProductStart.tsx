import { useCallback, useEffect, useState } from 'react'
import { api, type KnowledgeDoc, type QuickstartPreview } from './api'
import { ImportPanel } from './KnowledgePage'
import { busy as overlay } from './Busy'

type PresetLite = { id: number; name: string; status: string; production_width: number | null; production_height: number | null }
type PlatformLite = { name: string; media_only: boolean; ad_types: { name: string; presets: PresetLite[] }[] }
// 시연 기본 세트(05 시연 시나리오 · 서버 DEFAULT_PLACEMENTS 와 같은 이름)
const DEMO_NAMES = ['Instagram 피드', 'Instagram 스토리', 'Facebook 피드', '디스플레이 네이티브 2:1', '디스플레이 네이티브 1:1', 'PMax 가로 1.91:1']

/* 브랜드 탭 "제품에서 시작": 등록된 제품 조각이 카드로 보이고, 카드의 버튼 하나로 캠페인·지면·메시지를 자동으로 채워 카피까지 만든다(사용자 입력 0개).
   흐름: 카드 버튼 → 미리보기(Haiku 1회: 핵심 메시지·타깃 제안, 기본 지면) → 확인 시트(모두 채워져 있음) → 바로 만들기(캠페인 생성 + 카피 1회 + 1안 선택)
   → 규격·메시지·시안 탭의 그 캠페인이 열리고 ④ 이미지 생성이 자동으로 이어진다. 목록에 없는 제품은 "제품 가져오기"(지식 탭과 같은 것). */
export default function ProductStart({ brandId, brandName, groupId, onStart }: {
  brandId: number; brandName: string; groupId: number | null
  onStart: (designId: number, brandId: number, withImages: boolean) => void
}) {
  const [docs, setDocs] = useState<KnowledgeDoc[] | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [preview, setPreview] = useState<QuickstartPreview | null>(null)
  const [key, setKey] = useState('')
  const [target, setTarget] = useState('')
  // 시트 안 지면 고르기: 기본은 미리보기가 준 지면, "바꾸기"를 펼치면 규격 라이브러리와 같은 구조(플랫폼 > 광고 유형 > 지면)에서 체크
  const [sel, setSel] = useState<number[]>([])
  const [pickOpen, setPickOpen] = useState(false)
  const [plats, setPlats] = useState<PlatformLite[] | null>(null)
  const allPresets = (plats ?? []).filter((pl) => !pl.media_only).flatMap((pl) => pl.ad_types.flatMap((a) => a.presets.map((x) => ({ ...x, platform: pl.name }))))
  const toggle = (id: number) => setSel((v) => (v.includes(id) ? v.filter((x) => x !== id) : [...v, id]))
  const demoSet = () => setSel(allPresets.filter((x) => DEMO_NAMES.includes(x.name) && x.status !== '확인 필요').map((x) => x.id))
  const openPicker = () => { setPickOpen((v) => !v); if (!plats) fetch('/api/presets').then((r) => r.json()).then(setPlats).catch(() => setPlats([])) }
  const [err, setErr] = useState<string | null>(null)
  const load = useCallback(() => api.knowledgeDocs('제품', groupId, brandId).then(setDocs).catch(() => setDocs([])), [groupId, brandId])
  useEffect(() => { setShowAll(false); load() }, [load])

  const open = (doc: KnowledgeDoc) => overlay.run('제품 자료로 캠페인 내용을 채우는 중… (핵심 메시지·타깃 제안, 수 초)', async () => {
    setErr(null)
    try {
      const p = await api.quickstartPreview(doc.id)
      setPreview(p); setKey(p.key_message); setTarget(p.target); setSel(p.presets.map((x) => x.id)); setPickOpen(false)
    } catch (e) { setErr((e as Error).message) }
  })
  const start = (withImages: boolean) => {
    if (!preview) return
    const p = preview
    overlay.run('캠페인을 만들고 카피 3안을 쓰는 중… (10~20초)', async () => {
      setErr(null)
      try {
        const r = await api.quickstart({ doc_id: p.doc_id, key_message: key, target, preset_version_ids: sel })
        setPreview(null)
        onStart(r.design_id, r.brand_id, withImages && r.has_copy)
      } catch (e) { setErr((e as Error).message) }
    })
  }

  // 제품 이미지가 있는(가져온) 제품을 앞에, 그다음 쓰인 캠페인이 많은 순
  const list = [...(docs ?? [])].sort((a, b) => Number(!!b.image_asset_id) - Number(!!a.image_asset_id) || b.used_designs - a.used_designs)
  const shown = showAll ? list : list.slice(0, 4)
  return (
    <section className="ps">
      <div className="ps-head">
        <div>
          <h2>제품에서 시작</h2>
          <p className="muted">제품을 고르면 캠페인·지면·메시지가 자동으로 채워지고 카피와 지면 이미지까지 만들어집니다. 사용자 입력 0개.</p>
        </div>
        <div className="ps-import">
          <span className="muted">목록에 없으면</span>
          <ImportPanel brandId={brandId} brandName={brandName} onDone={() => load()} />
        </div>
      </div>
      {err && <div className="notice err">{err}</div>}
      {docs === null ? <p className="muted">제품을 불러오는 중…</p>
        : list.length === 0 ? <p className="muted">{brandName}에 등록된 제품이 없습니다. "제품 가져오기"로 공식 사이트에서 가져오세요.</p>
        : (
          <>
            <div className="ps-grid">
              {shown.map((d) => (
                <div key={d.id} className="ps-card">
                  {d.image_asset_id ? <img src={`/api/assets/${d.image_asset_id}/file`} alt="" /> : <div className="ps-noimg">이미지 없음</div>}
                  <strong>{d.title.replace(' 제품 정보', '')}</strong>
                  <span className="muted">{[d.line, d.quantity].filter(Boolean).join(' · ') || '제품 조각'}{d.used_designs ? ` · 쓰인 캠페인 ${d.used_designs}` : ''}</span>
                  <button type="button" className="primary" onClick={() => open(d)}>이 제품으로 시안 만들기</button>
                </div>
              ))}
            </div>
            {list.length > 4 && (
              <button type="button" className="link" onClick={() => setShowAll((v) => !v)}>{showAll ? '접기' : `제품 더 보기 (${list.length - 4})`}</button>
            )}
          </>
        )}

      {preview && (
        <div className="ps-sheet-bg" role="dialog" aria-label="이 제품으로 시안 만들기">
          <div className="ps-sheet">
            <div className="ps-sheet-head">
              <div>
                <h3>이 제품으로 시안 만들기</h3>
                <span className="muted">모두 자동으로 채웠습니다. 그대로 만들거나, 고치고 싶은 칸만 바꾸세요</span>
              </div>
              <button type="button" aria-label="닫기" onClick={() => setPreview(null)}>×</button>
            </div>
            <div className="ps-sheet-body">
              {preview.product_image_asset_id ? <img src={`/api/assets/${preview.product_image_asset_id}/file`} alt="" /> : <div className="ps-noimg">이미지 없음</div>}
              <div className="ps-fields">
                <label>캠페인 이름 <span className="ps-auto">자동 · 제품명</span>
                  <input type="text" value={preview.product_name} readOnly />
                </label>
                <label>핵심 메시지 <span className="ps-auto">{preview.suggested ? `자동 · 제품 자료와 브랜드 소개로 제안 (${preview.suggest_model})` : preview.suggest_error ?? '제안 없음 — 직접 입력'}</span>
                  <input type="text" value={key} maxLength={200} onChange={(e) => setKey(e.target.value)} placeholder="예: 속수분을 채우는 장벽 크림" />
                </label>
                <label>타깃 <span className="ps-auto">{preview.suggested ? '자동 · 브랜드 소개의 고객층' : '직접 입력'}</span>
                  <input type="text" value={target} maxLength={300} onChange={(e) => setTarget(e.target.value)} placeholder="예: 건조하고 민감한 30대" />
                </label>
              </div>
            </div>
            <div className="ps-presets">
              <div className="ps-presets-head">
                <span><b>지면 {sel.length}개</b> <span className="ps-auto">{sel.length === preview.presets.length && sel.every((id) => preview.presets.some((p) => p.id === id)) ? `자동 · ${preview.presets_source}` : '직접 고름'}</span></span>
                <button type="button" className="link" onClick={openPicker}>{pickOpen ? '접기' : '바꾸기'}</button>
              </div>
              {!pickOpen && (sel.length === 0
                ? <span className="notice err">지면이 없습니다. "바꾸기"에서 고르세요.</span>
                : <div className="ps-chips">{sel.map((id) => { const p = preview.presets.find((x) => x.id === id) ?? allPresets.find((x) => x.id === id); return p ? <span key={id} className="chip">{p.platform} {p.name}</span> : null })}</div>)}
              {pickOpen && (
                <div className="ps-picker">
                  <div className="row">
                    <button type="button" onClick={demoSet} disabled={!plats}>시연 기본 6개</button>
                    <button type="button" onClick={() => setSel([])}>모두 해제</button>
                    <span className="muted">'확인 필요' 지면은 공식 규격이 확인되지 않아 고를 수 없습니다</span>
                  </div>
                  {plats === null ? <p className="muted">지면 목록을 불러오는 중…</p> : plats.filter((pl) => !pl.media_only).map((pl) => (
                    <div key={pl.name} className="ps-plat">
                      <strong>{pl.name}</strong>
                      {pl.ad_types.map((a) => (
                        <div key={a.name} className="ps-adtype">
                          <span className="muted">{a.name}</span>
                          {a.presets.map((x) => (
                            <label key={x.id} className={x.status === '확인 필요' ? 'off' : ''}>
                              <input type="checkbox" disabled={x.status === '확인 필요'} checked={sel.includes(x.id)} onChange={() => toggle(x.id)} />
                              {x.name}{x.production_width ? ` ${x.production_width}×${x.production_height}` : ''}{x.status === '확인 필요' ? ' · 확인 필요' : ''}
                            </label>
                          ))}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
            <p className="muted">상품명·설명·제품 이미지는 제품 조각에서 · 규제 핵심 16 항상 + 검색 · 브랜드·그룹 근거 자동</p>
            <div className="ps-sheet-foot">
              <span className="muted">과금: 카피 1회(Sonnet) + 이미지 {sel.length}회 · 지금 캠페인 1개와 같음</span>
              <div className="row">
                <button type="button" disabled={sel.length === 0} onClick={() => start(false)}>카피까지만</button>
                <button type="button" className="primary" disabled={sel.length === 0} onClick={() => start(true)}>바로 만들기 → 카피 + 이미지 {sel.length}</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
