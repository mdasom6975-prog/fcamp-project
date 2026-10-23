// 작업 화면(3열)의 왼쪽 메뉴 상태·오른쪽 레일·보기 전용 시안이 함께 쓰는 캠페인 요약 데이터.
// 카피·이미지·아이콘·검수·호출 지표를 한 번에 불러와 상태 점과 숫자를 만든다. refreshKey 가 바뀌면 다시 불러온다.
import { useCallback, useEffect, useState } from 'react'

export type Variant = { variant_no: number; headline: string; body: string; cta: string; selected: boolean }
export type WorkspaceData = {
  brandName: string
  logoAssetId: number | null
  selected: Variant | null
  copyCount: number
  presetCount: number
  compositeCount: number
  backgroundId: number | null
  backgroundFile: string | null
  backgroundQuality: string | null
  backgroundModel: string | null
  composites: { preset_id: number; id: number; file: string | null; width: number; height: number; separate: boolean; edited: boolean }[]
  iconCount: number
  iconTarget: number
  review: { reviewed: boolean; blockers: number; warn: number; pass: number; exportReady: boolean } | null
  calls: { images: number; chats: number; costUsd: number; costKrw: number | null; estimated: boolean } | null
  usage: { used: number; limit: number } | null
}

const EMPTY: WorkspaceData = {
  brandName: '', logoAssetId: null, selected: null, copyCount: 0, presetCount: 0, compositeCount: 0,
  backgroundId: null, backgroundFile: null, backgroundQuality: null, backgroundModel: null, composites: [],
  iconCount: 0, iconTarget: 10, review: null, calls: null, usage: null,
}

const j = async (url: string) => { const r = await fetch(url); return r.ok ? r.json() : null }

export function useWorkspace(designId: number | null, brandId: number | null, brandName: string, refreshKey: unknown) {
  const [data, setData] = useState<WorkspaceData>({ ...EMPTY, brandName })
  const load = useCallback(async () => {
    if (!designId) return
    const [copy, imgs, icons, review, calls, usage, brand, iconSettings] = await Promise.all([
      j(`/api/designs/${designId}/copy`), j(`/api/designs/${designId}/images`), j(`/api/designs/${designId}/icons`),
      j(`/api/designs/${designId}/review`), j(`/api/designs/${designId}/api-calls`), j('/api/image-usage'),
      brandId ? j(`/api/brands/${brandId}`) : Promise.resolve(null), j('/api/icon-settings'),
    ])
    const groups: { preset_id: number; variants: Variant[] }[] = Array.isArray(copy) ? copy : []
    const first = groups.find((g) => g.variants.length > 0)
    const rows: { preset_id: number; background: any; composite: any }[] = Array.isArray(imgs) ? imgs : []
    const bgRow = rows.find((r) => r.background)
    const bg = bgRow?.background ?? null
    const items: { status: string; blocking: boolean }[] = review?.groups ? review.groups.flatMap((g: any) => g.items) : []
    setData({
      brandName,
      logoAssetId: brand?.version?.logo_asset_id ?? null,
      selected: first?.variants.find((v) => v.selected) ?? null,
      copyCount: first?.variants.length ?? 0,
      presetCount: rows.length,
      compositeCount: rows.filter((r) => r.composite).length,
      backgroundId: bg?.id ?? null,
      backgroundFile: bg?.file ?? null,
      backgroundQuality: bg?.layout?.quality ?? null,
      backgroundModel: bg?.layout?.model ?? null,
      composites: rows.filter((r) => r.composite).map((r) => ({
        preset_id: r.preset_id, id: r.composite.id, file: r.composite.file ?? null, width: r.composite.width, height: r.composite.height,
        separate: !!r.composite.layout?.separate_assets, edited: !!r.composite.layout?.edited,
      })),
      iconCount: Array.isArray(icons) ? icons.length : 0,
      iconTarget: iconSettings?.count ?? 10,
      review: review ? {
        reviewed: !!review.reviewed, exportReady: !!review.export_ready,
        blockers: Array.isArray(review.blockers) ? review.blockers.length : 0,
        warn: items.filter((i) => i.status === '경고').length,
        pass: items.filter((i) => i.status === '통과').length,
      } : null,
      calls: calls && typeof calls.total === 'number' ? {
        images: calls.images, chats: calls.chats, costUsd: calls.cost_usd, costKrw: calls.cost_krw, estimated: !!calls.estimated || calls.unpriced > 0,
      } : null,
      usage: usage && typeof usage.used === 'number' ? { used: usage.used, limit: usage.limit } : null,
    })
  }, [designId, brandId, brandName])
  useEffect(() => { load().catch(() => undefined) }, [load, refreshKey])
  return { data, reload: load }
}
