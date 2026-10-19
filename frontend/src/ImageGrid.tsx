import type { ReactNode } from 'react'

// 생성 결과(배경·합성) 공통 타입 — 시안(④)·편집·검수 세 화면이 같은 썸네일 그리드를 쓴다
export type ImgOut = {
  id: number; width: number; height: number
  file?: string          // 파일 토큰(uuid) — 행 id 가 재사용돼도 캐시가 섞이지 않게 URL 버전으로 쓴다
  layout: {
    warnings?: string[]; template?: string; margin_source?: string; separate_assets?: boolean
    style?: string; style_reason?: string; logo_placement?: { corner: string }
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
              {it.content_kind === '분리 자산형' && <span className="img-tag">분리 자산형</span>}
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
