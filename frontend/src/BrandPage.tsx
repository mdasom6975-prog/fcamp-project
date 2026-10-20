import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ITEM_LABELS, type BrandForm, type ColorRow, type FontRow, type Level } from './api'

const LEVELS: Level[] = ['필수', '권장']

// 새 브랜드 등록 시 역할별 기본 색 (없는 역할은 #000000)
const DEFAULT_COLORS: Record<string, string> = { 보조색: '#828C94', 배경색: '#FFFFFF', 텍스트색: '#000000' }

const emptyForm = (roles: string[], levels: Record<string, Level>): BrandForm => ({
  name: '',
  description: '',
  logo_asset_id: null,
  logo_square_asset_id: null,
  logo_margin_percent: null,
  colors: roles.map((role) => ({ role, hex: DEFAULT_COLORS[role] ?? '#000000' })),
  fonts: [
    { role: '제목', source: 'provided', name: 'Pretendard-Bold' },
    { role: '본문', source: 'provided', name: 'Pretendard-Regular' },
  ],
  icon_style: { stroke_width: 2, corner: '둥근', fill: '윤곽', material: '자동' },
  image_tone: { description: '', reference_asset_ids: [] },
  voice: { description: '', headline_max_chars: null, polite: false },
  required_phrases: [],
  forbidden_phrases: [],
  levels: { ...levels },
})

const DEFAULT_ICON = { stroke_width: 2, corner: '둥근', fill: '윤곽' } as const
// 3D 아이콘 재질 프리셋 (서버 schemas.ICON_MATERIALS 와 같은 값). '자동'은 이미지 톤 설명의 재질 묘사를 따른다
const ICON_MATERIALS = ['자동', '무광 플라스틱', '클레이', '소프트 고무', '유리·반투명', '금속']

const gcd = (a: number, b: number): number => (b ? gcd(b, a % b) : a)
const ratioText = (w: number, h: number) => {
  const g = gcd(w, h)
  const [a, b] = [w / g, h / g]
  return a > 30 || b > 30 ? `${(w / h).toFixed(2)}:1` : `${a}:${b}`
}

function LevelSelect({ item, form, set }: { item: string; form: BrandForm; set: (f: BrandForm) => void }) {
  return (
    <label className="level">
      구분
      <select value={form.levels[item]} onChange={(e) => set({ ...form, levels: { ...form.levels, [item]: e.target.value as Level } })}>
        {LEVELS.map((l) => <option key={l}>{l}</option>)}
      </select>
    </label>
  )
}

function Section({ item, form, set, hideLevel, children }: { item: string; form: BrandForm; set: (f: BrandForm) => void; hideLevel?: boolean; children: React.ReactNode }) {
  return (
    <section className="card">
      <header><h3>{ITEM_LABELS[item]}</h3>{!hideLevel && <LevelSelect item={item} form={form} set={set} />}</header>
      {children}
    </section>
  )
}

export function PhraseBox({ label, hint, tone, items, onChange }:
  { label: string; hint: string; tone: 'req' | 'ban'; items: string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState('')
  const add = (e: React.FormEvent) => {
    e.preventDefault()
    const t = text.trim()
    if (t && !items.includes(t)) onChange([...items, t])
    setText('')
  }
  return (
    <div>
      <div className="phrase-label">{label}</div>
      <div className="phrase-hint">{hint}</div>
      <div className="chips">
        {items.map((p) => (
          <span key={p} className={`phrase-chip ${tone}`}>
            {p}
            <button type="button" aria-label={`${p} 삭제`} onClick={() => onChange(items.filter((x) => x !== p))}>✕</button>
          </span>
        ))}
      </div>
      <form className="phrase-add" onSubmit={add}>
        <input placeholder="추가" aria-label={`${label} 추가`} value={text} maxLength={100} onChange={(e) => setText(e.target.value)} />
        <button type="submit">추가</button>
      </form>
    </div>
  )
}

export default function BrandPage({ onGoDesign }: { onGoDesign?: (brandId: number) => void }) {
  const [defaults, setDefaults] = useState<{ levels: Record<string, Level>; color_roles: string[] } | null>(null)
  const [provided, setProvided] = useState<{ name: string; label: string }[]>([])
  const [brands, setBrands] = useState<{ id: number; name: string }[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [versions, setVersions] = useState<{ id: number; version_no: number }[]>([])
  const [viewVersion, setViewVersion] = useState<number | null>(null)
  const [form, setForm] = useState<BrandForm | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string[] } | null>(null)
  const [logoName, setLogoName] = useState('')
  const [logoDim, setLogoDim] = useState<{ w: number; h: number } | null>(null)
  const [logoSqDim, setLogoSqDim] = useState<{ w: number; h: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [baseline, setBaseline] = useState('')      // 불러온/저장한 시점의 폼 (변경 여부 비교용)
  const [oldView, setOldView] = useState(false)       // 현재 버전이 아닌 이전 버전을 보는 중
  const [savedId, setSavedId] = useState<number | null>(null)   // 이번에 저장한 브랜드 (시안으로 이동 가능)
  const [iconRender, setIconRender] = useState<'3d' | 'svg'>('3d')   // 아이콘 렌더 방식(서버 설정): 3d 면 선 굵기·채움은 쓰지 않는다
  const logoInput = useRef<HTMLInputElement>(null)
  const logoSqInput = useRef<HTMLInputElement>(null)

  const loadBrands = useCallback(() => api.brands().then(setBrands), [])

  useEffect(() => {
    Promise.all([api.defaults(), api.providedFonts()]).then(([d, f]) => {
      setDefaults(d)
      setProvided(f)
      const f0 = emptyForm(d.color_roles, d.levels)
      setForm(f0); setBaseline(JSON.stringify(f0))
    })
    loadBrands()
    fetch('/api/icon-settings').then((r) => r.json()).then((j) => { if (j?.render === 'svg' || j?.render === '3d') setIconRender(j.render) }).catch(() => undefined)
  }, [loadBrands])

  if (!defaults || !form) return <main className="page"><p>불러오는 중…</p></main>
  const set = (f: BrandForm) => setForm(f)

  const newBrand = () => {
    setSelected(null); setSavedId(null); setVersions([]); setViewVersion(null); setLogoName(''); setLogoDim(null); setMsg(null)
    const f0 = emptyForm(defaults.color_roles, defaults.levels)
    setForm(f0); setBaseline(JSON.stringify(f0)); setOldView(false)
  }

  const openBrand = async (id: number, versionId?: number) => {
    const b = await api.brand(id, versionId)
    const v = b.version
    setSelected(id); setVersions(b.versions); setViewVersion(v.id); setMsg(null); setSavedId(null)
    setLogoName(v.logo_asset_id ? `등록된 로고 (파일 #${v.logo_asset_id})` : ''); setLogoDim(null); setLogoSqDim(null)
    const loaded: BrandForm = {
      name: b.name,
      description: b.description ?? '',
      logo_asset_id: v.logo_asset_id,
      logo_square_asset_id: v.logo_square_asset_id ?? null,
      logo_margin_percent: v.logo_margin?.percent ?? null,
      colors: v.colors,
      fonts: v.fonts,
      icon_style: v.icon_style?.corner ? v.icon_style : emptyForm([], defaults.levels).icon_style,
      image_tone: v.image_tone,
      voice: { description: '', headline_max_chars: null, polite: false, ...v.voice },
      required_phrases: v.required_phrases,
      forbidden_phrases: v.forbidden_phrases,
      levels: Object.fromEntries(Object.entries({ ...defaults.levels, ...v.levels })
        .map(([k, l]) => [k, l === '수정가능' ? '권장' : l])) as Record<string, Level>,
    }
    setForm(loaded); setBaseline(JSON.stringify(loaded)); setOldView(v.id !== b.current_version_id)
  }

  // 새 브랜드는 이름이 있으면 저장 가능, 기존 브랜드는 변경이 있거나 이전 버전을 보는 중일 때만 저장 가능
  const dirty = selected === null ? true : oldView || JSON.stringify(form) !== baseline

  const save = async () => {
    setBusy(true); setMsg(null)
    try {
      const body: BrandForm = form.icon_style.stroke_width >= 0.5 ? form
        : { ...form, icon_style: { ...form.icon_style, stroke_width: 2 } }   // 비어 있으면 기본값 2px
      const res = selected ? await api.update(selected, body) : await api.create(body)
      const w: string[] = res.warnings || []
      setMsg({ ok: true, text: [`저장했습니다. 버전 ${res.version.version_no}`, ...w.map((x) => `⚠ ${x}`)] })
      await loadBrands()
      await openBrand(res.id)
      setMsg({ ok: true, text: [`저장했습니다. 버전 ${res.version.version_no}`, ...w.map((x) => `⚠ ${x}`)] })
      setSavedId(res.id)
    } catch (e) {
      setMsg({ ok: false, text: [(e as Error).message] })
    } finally {
      setBusy(false)
    }
  }

  const upload = async (kind: 'logo' | 'font' | 'tone_ref', file: File | undefined, onOk: (r: { id: number; width: number | null; height: number | null; name: string }) => void) => {
    if (!file) return
    try { onOk(await api.upload(kind, file)); setMsg(null) } catch (e) { setMsg({ ok: false, text: [(e as Error).message] }) }
  }

  const setColor = (i: number, c: Partial<ColorRow>) =>
    set({ ...form, colors: form.colors.map((x, k) => (k === i ? { ...x, ...c } : x)) })
  const setFont = (i: number, f: Partial<FontRow>) =>
    set({ ...form, fonts: form.fonts.map((x, k) => (k === i ? { ...x, ...f } : x)) })


  // 왼쪽에 작성한 내용을 항목별 규칙 문장으로 바꾼다 (비어 있는 항목은 표시하지 않는다)
  const ruleLines = (item: string): string[] => {
    const out: string[] = []
    const comma = (a: string[]) => a.filter(Boolean).join(', ')
    if (item === 'logo') {
      out.push(logoDim ? `로고 원본·비율(${ratioText(logoDim.w, logoDim.h)}) 유지` : '로고 원본·비율 유지')
      if (form.logo_margin_percent != null) out.push(`로고 최소 여백 ${form.logo_margin_percent}%`)
      if (form.logo_square_asset_id) out.push('정사각 로고 자산 등록됨 (PMax 등 1:1 로고 지면)')
    } else if (item === 'colors') {
      const n = form.colors.filter((c) => /^#[0-9a-fA-F]{6}$/.test(c.hex)).length
      if (n) out.push(`텍스트·도형·아이콘은 허용 색상 ${n}개만`)
    } else if (item === 'fonts') {
      const names = form.fonts.map((f) => (f.source === 'provided'
        ? provided.find((p) => p.name === f.name)?.label ?? f.name
        : f.fileName ?? (f.asset_id ? `업로드 폰트 #${f.asset_id}` : '')) ?? '')
      const uniq = [...new Set(names.filter(Boolean) as string[])]
      if (uniq.length) out.push(`폰트 ${uniq.join(', ')}`)
    } else if (item === 'image_tone') {
      if (form.image_tone.description.trim()) out.push(`이미지 톤: ${form.image_tone.description.trim()}`)
    } else if (item === 'voice') {
      if (form.voice.description.trim()) out.push(`어조: ${form.voice.description.trim()}`)
      if (form.voice.headline_max_chars != null) out.push(`헤드라인 ${form.voice.headline_max_chars}자 이내`)
      if (form.voice.polite) out.push('존댓말로 작성')
    } else if (item === 'required_phrases') {
      const p = form.required_phrases.map((x) => x.trim()).filter(Boolean)
      if (p.length) out.push(`필수 표현 포함: ${comma(p)}`)
    } else if (item === 'forbidden_phrases') {
      const p = form.forbidden_phrases.map((x) => x.trim()).filter(Boolean)
      if (p.length) out.push(`금지 표현 미포함: ${comma(p)}`)
    }
    return out
  }

  const groups: { level: Level; cls: string; title: string; hint: string }[] = [
    { level: '필수', cls: 'must', title: '필수 규칙', hint: '위반 시 검수 완료 차단' },
    { level: '권장', cls: 'rec', title: '권장 규칙', hint: '위반 시 경고' },
  ]

  return (
    <main className="bp">
      <div className="bp-brands">
        <span className="bp-brands-label">브랜드</span>
        {brands.map((b) => (
          <button key={b.id} className={`chip ${b.id === selected ? 'on' : ''}`} onClick={() => openBrand(b.id)}>{b.name}</button>
        ))}
        {brands.length === 0 && <span className="muted">등록된 브랜드가 없습니다</span>}
        <button className="primary bp-new" onClick={newBrand}>+ 새 브랜드</button>
      </div>

      <div className="bp-tabs">
        <h1>{selected ? '브랜드 수정' : '브랜드 등록'}</h1>
        {selected && (
          <span className="bp-version">
            버전{' '}
            <select value={viewVersion ?? ''} onChange={(e) => openBrand(selected, Number(e.target.value))}>
              {versions.map((v) => <option key={v.id} value={v.id}>v{v.version_no}</option>)}
            </select>
          </span>
        )}
      </div>

      <div className="bp-grid">
        <section className="bp-main">
        <section className="card">
          <header><h3>기본 정보</h3></header>
          <div className="field-grid">
            <label>브랜드명 <span className="req">*</span>
              <input value={form.name} maxLength={100} placeholder="브랜드 이름" onChange={(e) => set({ ...form, name: e.target.value })} />
            </label>
            <label>서비스 설명 <span className="opt">(선택)</span>
              <input value={form.description} maxLength={300} placeholder="예: 신선한 식재료를 새벽에 배송해 드리는 온라인 장보기 서비스"
                onChange={(e) => set({ ...form, description: e.target.value })} />
            </label>
          </div>
        </section>

        <section className="card">
          <header><h3>로고</h3><span className="tag-note">원본 비율 고정 · 재생성 금지</span></header>
          <div className="logo-row">
            <div className="logo-box">
              {form.logo_asset_id ? (
                <img src={`/api/assets/${form.logo_asset_id}/file`} alt="로고"
                  onLoad={(e) => { const i = e.currentTarget; if (i.naturalWidth && i.naturalHeight) setLogoDim({ w: i.naturalWidth, h: i.naturalHeight }) }} />
              ) : <span className="muted">로고 없음</span>}
            </div>
            <label className="logo-margin">최소 여백 (로고 크기 대비 %)
              <input type="number" min={0} max={100} value={form.logo_margin_percent ?? ''}
                onChange={(e) => set({ ...form, logo_margin_percent: e.target.value === '' ? null : Number(e.target.value) })} />
            </label>
            <div className="logo-info">
              {form.logo_asset_id && (
                <div className="logo-name">{logoName}{logoDim && <span className="logo-ratio">비율 {ratioText(logoDim.w, logoDim.h)}</span>}</div>
              )}
              <div className="logo-help">PNG(투명 배경)·SVG, 300KB 이하. 등록한 원본 그대로 합성되며 생성 모델이 다시 그리지 않습니다.</div>
              <input ref={logoInput} type="file" hidden accept=".png,.svg,image/png,image/svg+xml"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  e.target.value = ''
                  if (f && f.size > 300 * 1024) { setMsg({ ok: false, text: ['로고 파일은 300KB 이하여야 합니다'] }); return }
                  upload('logo', f, (r) => {
                    set({ ...form, logo_asset_id: r.id })
                    setLogoName(r.name); setLogoDim(r.width && r.height ? { w: r.width, h: r.height } : null)
                  })
                }} />
              <button type="button" className="logo-btn" onClick={() => logoInput.current?.click()}>
                {form.logo_asset_id ? '원본 파일 교체' : '로고 파일 선택'}
              </button>
            </div>
          </div>
          {/* 정사각 로고: Google PMax 처럼 1:1 로고 자산을 요구하는 지면용. 합성에는 쓰지 않고 검수·내보내기에만 쓴다 */}
          <div className="logo-row" style={{ marginTop: 10 }}>
            <div className="logo-box">
              {form.logo_square_asset_id ? (
                <img src={`/api/assets/${form.logo_square_asset_id}/file`} alt="정사각 로고"
                  onLoad={(e) => { const i = e.currentTarget; if (i.naturalWidth && i.naturalHeight) setLogoSqDim({ w: i.naturalWidth, h: i.naturalHeight }) }} />
              ) : <span className="muted">정사각 로고 없음</span>}
            </div>
            <div className="logo-info">
              <div className="logo-name">정사각 로고 (선택){logoSqDim && <span className="logo-ratio">{logoSqDim.w}×{logoSqDim.h}</span>}</div>
              <div className="logo-help">Google PMax 처럼 1:1 로고 자산을 요구하는 지면에 쓰입니다. 1:1, 128×128 이상(권장 1200×1200), PNG·SVG. 기본 로고는 변형하지 않으므로 심볼형 파일을 따로 올리세요. 검수와 내보내기에만 쓰이고 이미지 합성에는 쓰지 않습니다.</div>
              <input ref={logoSqInput} type="file" hidden accept=".png,.svg,image/png,image/svg+xml"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  e.target.value = ''
                  if (f && f.size > 300 * 1024) { setMsg({ ok: false, text: ['로고 파일은 300KB 이하여야 합니다'] }); return }
                  upload('logo', f, (r) => {
                    if (r.width && r.height && (Math.abs(r.width / r.height - 1) > 0.05 || Math.min(r.width, r.height) < 128)) {
                      setMsg({ ok: false, text: [`정사각 로고는 1:1 이고 128×128 이상이어야 합니다 (올린 파일 ${r.width}×${r.height})`] }); return
                    }
                    set({ ...form, logo_square_asset_id: r.id })
                    setLogoSqDim(r.width && r.height ? { w: r.width, h: r.height } : null)
                  })
                }} />
              <div className="row">
                <button type="button" className="logo-btn" onClick={() => logoSqInput.current?.click()}>
                  {form.logo_square_asset_id ? '정사각 로고 교체' : '정사각 로고 선택'}
                </button>
                {form.logo_square_asset_id && <button type="button" onClick={() => { set({ ...form, logo_square_asset_id: null }); setLogoSqDim(null) }}>제거</button>}
              </div>
            </div>
          </div>
        </section>

        <Section item="colors" form={form} set={set}>
          {form.colors.map((c, i) => {
            const isDefault = i < defaults.color_roles.length   // 기본 역할 행은 삭제할 수 없다
            return (
              <div className="color-row" key={i}>
                <input type="color" aria-label={`${c.role || '색상'} 색상`}
                  value={/^#[0-9a-fA-F]{6}$/.test(c.hex) ? c.hex : '#000000'} onChange={(e) => setColor(i, { hex: e.target.value.toUpperCase() })} />
                <input className="role" aria-label="색상 이름" placeholder="역할 이름" value={c.role} maxLength={30}
                  onChange={(e) => setColor(i, { role: e.target.value })} />
                <input className="hex" aria-label="색상 코드" value={c.hex} maxLength={7} onChange={(e) => setColor(i, { hex: e.target.value })} />
                {!isDefault && <button onClick={() => set({ ...form, colors: form.colors.filter((_, k) => k !== i) })}>삭제</button>}
              </div>
            )
          })}
          <button onClick={() => set({ ...form, colors: [...form.colors, { role: '', hex: '#000000' }] })}>+ 역할 추가</button>
        </Section>

        <Section item="fonts" form={form} set={set} hideLevel>
          {form.fonts.map((f, i) => (
            <div className="row" key={i}>
              <strong className="role">{f.role}</strong>
              <select value={f.source} onChange={(e) => setFont(i, e.target.value === 'provided'
                ? { source: 'provided', name: provided[0]?.name, asset_id: null }
                : { source: 'upload', name: null, asset_id: null })}>
                <option value="provided">제공 폰트</option>
                <option value="upload">파일 업로드</option>
              </select>
              {f.source === 'provided' ? (
                <select value={f.name ?? ''} onChange={(e) => setFont(i, { name: e.target.value })}>
                  {provided.map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}
                </select>
              ) : (
                <>
                  <input type="file" accept=".ttf,.otf" onChange={(e) => upload('font', e.target.files?.[0], (r) => setFont(i, { asset_id: r.id, fileName: r.name }))} />
                  {f.asset_id && <span className="muted">{f.fileName ?? `파일 #${f.asset_id}`}</span>}
                </>
              )}
            </div>
          ))}
          <p className="muted">업로드 폰트는 TTF/OTF이며 한글 글리프가 있어야 합니다. 라이선스는 사용자 책임입니다.</p>
        </Section>

        <Section item="icon_style" form={form} set={set} hideLevel>
          <div className="row">
            <label>모서리 <select value={form.icon_style.corner}
              onChange={(e) => set({ ...form, icon_style: { ...form.icon_style, corner: e.target.value as '각진' | '둥근' } })}>
              <option>각진</option><option>둥근</option></select></label>
            {iconRender === '3d' && (
              <label>재질 <select value={form.icon_style.material ?? '자동'}
                onChange={(e) => set({ ...form, icon_style: { ...form.icon_style, material: e.target.value } })}>
                {ICON_MATERIALS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select></label>
            )}
            {iconRender === 'svg' && (
              <>
                <label>선 굵기 <input type="number" min={0.5} max={12} step={0.5} placeholder="2" value={form.icon_style.stroke_width || ''}
                  onChange={(e) => set({ ...form, icon_style: { ...form.icon_style, stroke_width: e.target.value === '' ? 0 : Number(e.target.value) } })} /></label>
                <label>채움 <select value={form.icon_style.fill}
                  onChange={(e) => set({ ...form, icon_style: { ...form.icon_style, fill: e.target.value as '윤곽' | '채움' } })}>
                  <option>윤곽</option><option>채움</option></select></label>
              </>
            )}
          </div>
          {iconRender === 'svg'
            ? <p className="muted">권장 기본값: 선 굵기 {DEFAULT_ICON.stroke_width}px · {DEFAULT_ICON.corner} 모서리 · {DEFAULT_ICON.fill} (비워 두면 이 값이 적용됩니다)</p>
            : <p className="muted">3D 아이콘은 여기서 정한 <strong>모서리·재질</strong>과 함께, 색은 <strong>허용 색상</strong>의 주조·보조·강조색, 조명(재질이 '자동'이면 재질·광택도)은 아래 <strong>이미지 톤</strong> 설명을 따릅니다. 재질을 고르면 10개가 같은 재질로 나옵니다. 선 굵기·채움은 2D(SVG) 방식에서만 씁니다.</p>}
        </Section>

        <section className="card">
          <header><h3>문구 어조 · 이미지 톤</h3><span className="tag-note">권장 규칙 · 위반 시 경고</span></header>
          <div className="field-grid">
            <label>어조 설명
              <input placeholder="예: 친근한 존댓말, 짧은 문장" value={form.voice.description}
                onChange={(e) => set({ ...form, voice: { ...form.voice, description: e.target.value } })} />
            </label>
            <label>이미지 톤
              <input placeholder="예: 밝고 깨끗한 톤" value={form.image_tone.description}
                onChange={(e) => set({ ...form, image_tone: { ...form.image_tone, description: e.target.value } })} />
            </label>
            <label>헤드라인 권장 길이 (자 이내)
              <input type="number" min={5} max={40} value={form.voice.headline_max_chars ?? ''}
                onChange={(e) => set({ ...form, voice: { ...form.voice, headline_max_chars: e.target.value === '' ? null : Number(e.target.value) } })} />
            </label>
            <label className="check-line">
              <input type="checkbox" checked={form.voice.polite}
                onChange={(e) => set({ ...form, voice: { ...form.voice, polite: e.target.checked } })} />
              존댓말로 작성
            </label>
          </div>
        </section>

        <section className="card">
          <header><h3>필수 / 금지 표현</h3><span className="tag-note">카피 생성·검수에 그대로 사용</span></header>
          <div className="phrase-grid">
            <PhraseBox label="필수 표현" hint="카피에 반드시 포함 (미포함 시 수정 필요)" tone="req"
              items={form.required_phrases} onChange={(v) => set({ ...form, required_phrases: v })} />
            <PhraseBox label="금지 표현" hint="카피에 포함되면 수정 필요" tone="ban"
              items={form.forbidden_phrases} onChange={(v) => set({ ...form, forbidden_phrases: v })} />
          </div>
        </section>

        </section>

        <aside className="bp-side">
          <section className="card">
            <header><h3>저장</h3></header>
            <p className="muted bp-hint">
              {selected ? '저장하면 기존 버전은 보존되고 새 버전이 만들어집니다.' : '브랜드명을 입력하고 저장하면 첫 버전이 만들어집니다.'}
            </p>
            {msg && <div className={msg.ok ? 'notice ok' : 'notice err'}>{msg.text.map((t, i) => <div key={i}>{t}</div>)}</div>}
            <button className="primary bp-save" disabled={busy || !form.name.trim() || !dirty} onClick={save}>{selected ? '새 버전으로 저장' : '브랜드 저장'}</button>
            {selected !== null && !dirty && <p className="muted bp-nochange">변경 사항이 없습니다.</p>}
            {onGoDesign && savedId !== null && savedId === selected && (
              <button className="bp-go" onClick={() => onGoDesign(savedId)}>시안 만들러 가기 →</button>
            )}
          </section>

          <section className="card">
            <header><h3>규칙 구분 (검수에서 이렇게 쓰입니다)</h3></header>
            {groups.map((g) => {
              const lines = Object.keys(form.levels)
                .filter((k) => form.levels[k] === g.level && ITEM_LABELS[k])
                .flatMap((k) => ruleLines(k).map((t) => ({ k, t })))
              return (
                <div key={g.level} className={`rule-box ${g.cls}`}>
                  <div className="rule-head"><strong>{g.title}</strong><span>{g.hint}</span></div>
                  {lines.length === 0 ? <div className="muted">작성된 규칙이 없습니다</div> : (
                    <ul>{lines.map((l, i) => <li key={`${l.k}-${i}`}>{l.t}</li>)}</ul>
                  )}
                </div>
              )
            })}
          </section>
        </aside>
      </div>
    </main>
  )
}
