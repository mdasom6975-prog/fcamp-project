import type { ReactNode } from 'react'

// 생성 결과(배경·합성) 공통 타입 — 시안(④)·편집·검수 세 화면이 같은 썸네일 그리드를 쓴다
export type ImgOut = {
  id: number; width: number; height: number
  file?: string          // 파일 토큰(uuid) — 행 id 가 재사용돼도 캐시가 섞이지 않게 URL 버전으로 쓴다
  layout: {
    warnings?: string[]; template?: string; margin_source?: string; separate_assets?: boolean
    style?: string; style_reason?: string; logo_placement?: { corner: string }
    quality?: string    // 배경 행: 생성 품질
    crop?: { mode?: string }; single_extra?: boolean; fill?: string   // 합성 행: 크롭 방식('블러 채우기' 등) / 배경 행: 1장 방식 보완 변형·블러 표시
    // 배경 행: 제품 누끼를 붙인 자리(compose.product_box). slot·box 는 픽셀 (x0, y0, x1, y1), src 는 여백을 잘라낸 누끼 크기
    product_placed?: { slot: number[]; box: number[]; scale: number; src?: number[] }
  }
} | null
export type ImgRow = { preset_id: number; background: ImgOut; composite: ImgOut }
// 지면별 '이미지 안 문구' 설정(서버 in_image_for): 본문·CTA 를 이미지에 그리는지, 지면 유형 기본값 그대로인지
export type InImage = { body: boolean; cta: boolean; available: boolean; default: boolean }
export type GridItem = {
  preset_id: number; platform: string; name: string; width: number | null; height: number | null; content_kind?: string
  in_image?: InImage
}
// 지면 유형 기본값(서버 in_image_default 와 같은 규칙): 이미지가 광고 전체인 업로드형 배너만 본문·CTA 를 켠다
export const inImageDefault = (name: string) => (name.startsWith('업로드형') ? { body: true, cta: true } : { body: false, cta: false })
// 기본값과 다르게 설정했는가(저장했더라도 값이 기본과 같으면 '기본'으로 본다)
export const inImageChanged = (s: InImage | undefined, name: string) =>
  !!s && s.available && (s.body !== inImageDefault(name).body || s.cta !== inImageDefault(name).cta)
// 카드 태그: 기본값과 다르게 설정한 지면만 표시한다
export const inImageTag = (s: InImage | undefined, name: string) =>
  !s || !inImageChanged(s, name) ? null : s.body && s.cta ? '본문·CTA 안' : s.body ? '본문 안' : s.cta ? 'CTA 안' : '제목만'


// 누끼 해상도가 낮아 최대 확대(1.5배)에 걸려 제품 구역을 가로·세로 어느 쪽으로도 채우지 못했으면 안내 문구, 아니면 null
export const smallProductNote = (bg: ImgOut | undefined) => {
  const p = bg?.layout.product_placed
  if (!p) return null
  const [sx0, sy0, sx1, sy1] = p.slot, [x0, y0, x1, y1] = p.box
  if (x1 - x0 >= sx1 - sx0 - 2 || y1 - y0 >= sy1 - sy0 - 2) return null
  const size = p.src ? `(${p.src[0]}×${p.src[1]}px)` : ''
  return `제품 이미지${size}가 작아 ${p.scale}배까지만 키워 제품 자리보다 작게 들어갔습니다. 더 큰 누끼를 올리면 자리를 채웁니다.`
}

export const thumbUrl = (c: NonNullable<ImgOut>) => `/api/generated/${c.id}/thumb?v=${c.file ?? c.id}`
export const fileUrl = (c: NonNullable<ImgOut>) => `/api/generated/${c.id}/file?v=${c.file ?? c.id}`

export default function ImageGrid({ items, rows, selected, onSelect, badge, dimmed, emptyText }: {
  items: GridItem[]
  rows: Record<number, ImgRow | undefined>
  selected?: number | null
  onSelect?: (presetId: number) => void
  badge?: (item: GridItem, row: ImgRow | undefined) => ReactNode   // 카드 아래 상태 표시(검수 결과 등)
  dimmed?: (item: GridItem, row: ImgRow | undefined) => string | null   // 문자열이면 흐리게 + 그 이유
  emptyText?: string
}) {
  if (items.length === 0) return <p className="muted">{emptyText ?? '지면이 없습니다.'}</p>
  return (
    <div className="img-grid" role={onSelect ? 'listbox' : undefined}>
      {items.map((it) => {
        const row = rows[it.preset_id]
        const comp = row?.composite ?? null
        const why = dimmed?.(it, row) ?? null
        const on = selected === it.preset_id
        const warns = comp?.layout.warnings?.length ?? 0
        const click = onSelect && !why ? () => onSelect(it.preset_id) : undefined
        return (
          <div key={it.preset_id} className={`img-card ${on ? 'on' : ''} ${why ? 'dim' : ''} ${click ? 'clickable' : ''}`}
            role={onSelect ? 'option' : undefined} aria-selected={onSelect ? on : undefined} tabIndex={click ? 0 : -1}
            onClick={click} onKeyDown={(e) => { if (click && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); click() } }}
            title={why ?? undefined}>
            <div className="img-thumb">
              {comp
                ? <img src={thumbUrl(comp)} alt={`${it.platform} ${it.name} 합성본`} loading="lazy" />
                : <span className="muted">미생성</span>}
              <span className="img-tags">
                {it.content_kind === '분리 자산형' && <span className="img-tag">분리 자산형</span>}
                {inImageTag(it.in_image, it.name) && <span className="img-tag setting" title="이미지 안 문구 설정을 지면 기본값과 다르게 바꿨습니다">{inImageTag(it.in_image, it.name)}</span>}
                {comp?.layout.crop?.mode === '블러 채우기' && <span className="img-tag" title="보완 변형을 만들지 못해 원본 전체를 넣고 남는 자리를 흐린 확대본으로 채웠습니다">블러 채움</span>}
                {smallProductNote(row?.background) && <span className="img-tag" title={smallProductNote(row?.background) ?? undefined}>제품 작게</span>}
                {row?.background?.layout.single_extra && <span className="img-tag" title="1장 방식에서 잘라 쓰면 피사체가 많이 사라져 원본을 참조해 이 비율군용으로 1장 더 만들었습니다">보완 변형</span>}
              </span>
              {warns > 0 && <span className="img-warn" title={comp?.layout.warnings?.join('\n')}>경고 {warns}</span>}
            </div>
            <div className="img-meta">
              <div className="muted img-plat">{it.platform}</div>
              <div className="img-name">{it.name}</div>
              <div className="muted img-size">{it.width}×{it.height}{comp?.layout.template ? ` · ${comp.layout.template}` : ''}</div>
              {why && <div className="muted img-why">{why}</div>}
              {badge && <div className="img-badge">{badge(it, row)}</div>}
            </div>
          </div>
        )
      })}
    </div>
  )
}
