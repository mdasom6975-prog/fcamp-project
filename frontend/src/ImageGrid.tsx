import type { ReactNode } from 'react'

// 생성 결과(배경·합성) 공통 타입 — 시안(④)·편집·검수 세 화면이 같은 썸네일 그리드를 쓴다
export type ImgOut = {
  id: number; width: number; height: number
  file?: string          // 파일 토큰(uuid) — 행 id 가 재사용돼도 캐시가 섞이지 않게 URL 버전으로 쓴다
  layout: {
    warnings?: string[]; template?: string; margin_source?: string; separate_assets?: boolean
    style?: string; style_reason?: string; logo_placement?: { corner: string }
    quality?: string; draft?: boolean    // 배경 행: 생성 품질과 초안 여부(확정 품질보다 낮게 만들었는가)
    crop?: { mode?: string }; single_extra?: boolean; fill?: string   // 합성 행: 크롭 방식('블러 채우기' 등) / 배경 행: 1장 방식 보완 변형·블러 표시
  }
} | null
export type ImgRow = { preset_id: number; background: ImgOut; composite: ImgOut }
export type GridItem = {
  preset_id: number; platform: string; name: string; width: number | null; height: number | null; content_kind?: string
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
                {row?.background?.layout.draft && <span className="img-tag draft" title={`배경을 초안 품질(${row.background.layout.quality})로 만들었습니다. 내보내기 전에 '확정 품질로 배경 생성'을 실행하세요`}>초안 배경</span>}
                {comp?.layout.crop?.mode === '블러 채우기' && <span className="img-tag" title="보완 변형을 만들지 못해 원본 전체를 넣고 남는 자리를 흐린 확대본으로 채웠습니다">블러 채움</span>}
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
