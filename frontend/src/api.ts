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

// 스튜디오가 다루는 브랜드 범위: #/group = 그룹 소속 브랜드, #/studio = 단독 브랜드
export type BrandScope = 'group' | 'solo'
export const inScope = <T extends { group_id?: number | null }>(list: T[], scope: BrandScope) =>
  list.filter((b) => (b.group_id != null) === (scope === 'group'))

export const groupNames = (list: { group_name: string | null }[]) => [...new Set(list.flatMap((b) => b.group_name ?? []))]

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
  // 지식 브라우저(아모레퍼시픽 입장 첫 화면). 조회는 과금 없음, knowledgeSearch 만 임베딩 1회
  knowledgeSummary: (groupId: number | null) =>
    fetch(`/api/knowledge/summary${groupId ? `?group_id=${groupId}` : ''}`).then((r) => j<KnowledgeSummary>(r)),
  knowledgeDocs: (layer: string, groupId: number | null, brandId: number | null) =>
    fetch(`/api/knowledge/docs?layer=${encodeURIComponent(layer)}${groupId ? `&group_id=${groupId}` : ''}${brandId ? `&brand_id=${brandId}` : ''}`).then((r) => j<KnowledgeDoc[]>(r)),
  knowledgeChunks: (docId: number, groupId: number | null) =>
    fetch(`/api/knowledge/docs/${docId}/chunks${groupId ? `?group_id=${groupId}` : ''}`).then((r) => j<{ doc: KnowledgeDocDetail; chunks: KnowledgeChunk[] }>(r)),
  knowledgeSearch: (body: { group_id: number | null; brand_id: number | null; product_name: string; description: string; key_message: string; target: string }) =>
    fetch('/api/knowledge/search', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then((r) => j<KnowledgeSearch>(r)),
  // 제품 가져오기(공식 사이트 → 제품 지식 + 제품 이미지). discover 는 과금 없음, import 는 제품당 임베딩 1회
  productsDiscover: (brandId: number) =>
    fetch(`/api/knowledge/products/discover?brand_id=${brandId}`).then((r) => j<ProductsDiscover>(r)),
  productsImport: (brandId: number, seqs: string[]) =>
    fetch('/api/knowledge/products/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ brand_id: brandId, seqs }) })
      .then((r) => j<{ results: ProductImportResult[] }>(r)),
  // 제품에서 시작: preview 는 제안(Haiku 1회) + 기본 지면, quickstart 는 캠페인 생성 + 카피 1회 + 1안 선택
  quickstartPreview: (docId: number) =>
    fetch('/api/knowledge/products/quickstart/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ doc_id: docId }) })
      .then((r) => j<QuickstartPreview>(r)),
  quickstart: (body: { doc_id: number; key_message: string; target: string; preset_version_ids: number[] }) =>
    fetch('/api/knowledge/products/quickstart', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then((r) => j<{ design_id: number; brand_id: number; product_name: string; has_copy: boolean; selected_variant: number | null; copy_failures: string[] }>(r)),
  campaignFromProduct: (docId: number, keyMessage: string, target: string) =>
    fetch('/api/knowledge/products/campaign', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ doc_id: docId, key_message: keyMessage, target }) })
      .then((r) => j<{ design_id: number; brand_id: number; product_name: string; product_image_asset_id: number | null }>(r)),
}

export type ProductsDiscover = { supported: boolean; brand: string; list_url?: string; products: { seq: string; name: string; line: string; price: number | null; doc_id: number | null }[] }
export type ProductImportResult = { seq: string; name: string; ok: boolean; doc_id?: number; asset_id?: number | null; problems: string[] }

export type KnowledgeSummary = {
  group: { id: number; name: string } | null
  brands: { id: number; name: string; docs: number }[]
  layers: { layer: string; docs: number; chunks: number }[]
  docs: number; chunks: number; embed_model: string; last_registered: string | null
}
export type KnowledgeDoc = {
  id: number; source_id: string; title: string; layer: string; brand_id: number | null; brand: string | null; industry: string | null
  status: string; source_url: string; fetched_at: string; created_at: string | null; chunks: number; archived: number; used_designs: number
  image_asset_id?: number | null; line?: string | null; quantity?: string | null     // 제품 층: 브랜드 탭 "제품에서 시작" 카드
}
export type QuickstartPreview = {
  doc_id: number; brand_id: number; brand_name: string; product_name: string; description: string; product_image_asset_id: number | null
  key_message: string; target: string; suggested: boolean; suggest_error: string | null; suggest_model: string
  presets: { id: number; platform: string; name: string; width: number | null; height: number | null }[]; presets_source: string
}
export type KnowledgeDocDetail = {
  id: number; title: string; layer: string; source_id: string; source_url: string; fetched_at: string; industry: string | null; status: string
  created_at: string | null; motifs: string | null; directions: string[]
  image_asset_id?: number | null; price?: number | null; quantity?: string | null; line?: string | null; brand_id?: number | null
}
export type KnowledgeUse = {
  design_id: number; brand: string; product_name: string; status: string; created_at: string | null
  headline: string; violations: number; count: number; blocked: boolean; role: '규제' | '근거'; score?: number | null
}
export type KnowledgeChunk = {
  id: number; seq: number; section: string; text: string; chars: number; product_name: string | null; aliases: string[]; functional: string[]
  embed_model: string; has_vector: boolean; used: KnowledgeUse[]
}
export type KnowledgeSearch = {
  query: string; industries: string[]; law_all: number[]; mode: string; hybrid: boolean
  layers: { layer: string; k: number | null; rows: { chunk_id: number; doc_id: number; source_id: string; doc_title: string; section: string; score: number; exact_product: boolean; product_name: string | null }[] }[]
}
