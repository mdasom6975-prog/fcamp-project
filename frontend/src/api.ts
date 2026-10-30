export type Level = '필수' | '권장' | '수정가능'
export const ITEM_LABELS: Record<string, string> = {
  logo: '로고', colors: '허용 색상', fonts: '폰트', icon_style: '아이콘 스타일',
  image_tone: '이미지 톤', voice: '문구 어조',
  required_phrases: '필수 표현', forbidden_phrases: '금지 표현',
}
export type ColorRow = { role: string; hex: string }
export type LogoPosition = '좌상단' | '우상단' | '좌하단' | '우하단' | '자동'
export const LOGO_POSITIONS: LogoPosition[] = ['좌상단', '우상단', '좌하단', '우하단', '자동']
export type FontRow = { role: string; source: 'provided' | 'upload'; name?: string | null; asset_id?: number | null; fileName?: string }
export type BrandForm = {
  name: string
  description: string
  group_id: number | null                 // 소속 그룹(예: 아모레퍼시픽). 없으면 단독 브랜드
  logo_asset_id: number | null
  logo_square_asset_id: number | null     // 정사각 로고(1:1, 128px 이상) — PMax 등 1:1 로고 자산을 요구하는 지면용
  logo_margin_percent: number | null
  logo_position: LogoPosition | null     // 합성 로고 자리. 비우면 좌상단. '자동'은 지면마다 빈 모서리를 고른다
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

// 같은 GET 이 같은 순간에 여러 컴포넌트에서 나가면(캠페인을 열 때 /copy·/images·/brands 가 2~3번) 한 번만 보내고 결과를 나눠 준다.
// 서버리스(Vercel)에서는 동시 요청 수만큼 함수 인스턴스가 새로 떠서 콜드 스타트(~3초)가 생기므로 동시 요청 수를 줄이는 것이 중요하다.
// 응답이 오면 바로 지우므로 캐시가 아니다(다음 호출은 다시 서버로 간다). ok 가 아니면 null.
const inflight = new Map<string, Promise<unknown>>()
export function getJson<T = any>(url: string): Promise<T | null> {
  const cur = inflight.get(url)
  if (cur) return cur as Promise<T | null>
  const p = fetch(url).then((r) => (r.ok ? (r.json() as Promise<T>) : null)).finally(() => inflight.delete(url))
  inflight.set(url, p)
  return p
}

export const api = {
  defaults: () => fetch('/api/brand-defaults').then((r) => j<{ levels: Record<string, Level>; color_roles: string[] }>(r)),
  providedFonts: () => fetch('/api/fonts/provided').then((r) => j<{ name: string; label: string }[]>(r)),
  brands: () => fetch('/api/brands').then((r) => j<{ id: number; name: string; group_id: number | null; group_name: string | null }[]>(r)),
  brandGroups: () => fetch('/api/brand-groups').then((r) => j<{ id: number; name: string }[]>(r)),
  brand: (id: number, versionId?: number) =>
    fetch(`/api/brands/${id}${versionId ? `?version_id=${versionId}` : ''}`).then((r) => j<any>(r)),
  create: (b: BrandForm) =>
    fetch('/api/brands', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }).then((r) => j<any>(r)),
  update: (id: number, b: BrandForm) =>
    fetch(`/api/brands/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }).then((r) => j<any>(r)),
  deleteBrand: (id: number) =>
    fetch(`/api/brands/${id}`, { method: 'DELETE' }).then((r) => j<{ deleted: number; designs: number; versions: number; deleted_files: number }>(r)),
  brandDesignCount: (id: number) =>
    fetch(`/api/designs?brand_id=${id}`).then((r) => j<unknown[]>(r)).then((l) => l.length),
  // square: 정사각 로고 칸. 1:1 이 아니면 서버가 투명 여백을 넣어 정사각으로 저장하고 note 에 안내를 돌려준다
  upload: (kind: 'logo' | 'font' | 'tone_ref' | 'product', file: File, opts: { square?: boolean } = {}) => {
    const f = new FormData()
    f.append('kind', kind)
    if (opts.square) f.append('square', 'true')
    f.append('file', file)
    return fetch('/api/assets', { method: 'POST', body: f }).then((r) => j<{ id: number; width: number | null; height: number | null; name: string; note?: string | null }>(r))
  },
}
