export type Level = '필수' | '권장' | '수정가능'
export const ITEM_LABELS: Record<string, string> = {
  logo: '로고', colors: '허용 색상', fonts: '폰트', icon_style: '아이콘 스타일',
  image_tone: '이미지 톤', voice: '문구 어조',
  required_phrases: '필수 표현', forbidden_phrases: '금지 표현',
}
export type ColorRow = { role: string; hex: string }
export type FontRow = { role: string; source: 'provided' | 'upload'; name?: string | null; asset_id?: number | null; fileName?: string }
export type BrandForm = {
  name: string
  description: string
  logo_asset_id: number | null
  logo_square_asset_id: number | null     // 정사각 로고(1:1, 128px 이상) — PMax 등 1:1 로고 자산을 요구하는 지면용
  logo_margin_percent: number | null
  colors: ColorRow[]
  fonts: FontRow[]
  icon_style: { stroke_width: number; corner: '각진' | '둥근'; fill: '윤곽' | '채움'; material?: string }
  image_tone: { description: string; reference_asset_ids: number[] }
  voice: { description: string; headline_max_chars: number | null; polite: boolean }
  required_phrases: string[]
  forbidden_phrases: string[]
  levels: Record<string, Level>
}

async function j<T>(r: Response): Promise<T> {
  const body = await r.json().catch(() => ({}))
  if (!r.ok) {
    const d = body.detail
    const msg = Array.isArray(d) ? d.map((x: { msg: string }) => x.msg).join(', ') : d
    throw new Error(msg || '요청에 실패했습니다')
  }
  return body as T
}

export const api = {
  defaults: () => fetch('/api/brand-defaults').then((r) => j<{ levels: Record<string, Level>; color_roles: string[] }>(r)),
  providedFonts: () => fetch('/api/fonts/provided').then((r) => j<{ name: string; label: string }[]>(r)),
  brands: () => fetch('/api/brands').then((r) => j<{ id: number; name: string }[]>(r)),
  brand: (id: number, versionId?: number) =>
    fetch(`/api/brands/${id}${versionId ? `?version_id=${versionId}` : ''}`).then((r) => j<any>(r)),
  create: (b: BrandForm) =>
    fetch('/api/brands', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }).then((r) => j<any>(r)),
  update: (id: number, b: BrandForm) =>
    fetch(`/api/brands/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }).then((r) => j<any>(r)),
  upload: (kind: 'logo' | 'font' | 'tone_ref' | 'product', file: File) => {
    const f = new FormData()
    f.append('kind', kind)
    f.append('file', file)
    return fetch('/api/assets', { method: 'POST', body: f }).then((r) => j<{ id: number; width: number | null; height: number | null; name: string }>(r))
  },
}
