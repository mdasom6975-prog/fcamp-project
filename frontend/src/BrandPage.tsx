import { useCallback, useEffect, useState } from 'react'
import { api, ITEM_LABELS, type BrandForm, type ColorRow, type FontRow, type Level } from './api'

const LEVELS: Level[] = ['필수', '권장', '수정가능']

const emptyForm = (roles: string[], levels: Record<string, Level>): BrandForm => ({
  name: '',
  logo_asset_id: null,
  logo_margin_percent: null,
  colors: roles.map((role) => ({ role, hex: '#000000' })),
  fonts: [
    { role: '제목', source: 'provided', name: 'Pretendard-Bold' },
    { role: '본문', source: 'provided', name: 'Pretendard-Regular' },
  ],
  icon_style: { stroke_width: 2, corner: '둥근', fill: '윤곽' },
  image_tone: { description: '', reference_asset_ids: [] },
  voice: { description: '', sample_phrases: [] },
  required_phrases: [],
  forbidden_phrases: [],
  levels: { ...levels },
})

const lines = (s: string) => s.split('\n')

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

function Section({ item, form, set, children }: { item: string; form: BrandForm; set: (f: BrandForm) => void; children: React.ReactNode }) {
  return (
    <section className="card">
      <header><h3>{ITEM_LABELS[item]}</h3><LevelSelect item={item} form={form} set={set} /></header>
      {children}
    </section>
  )
}

export default function BrandPage() {
  const [defaults, setDefaults] = useState<{ levels: Record<string, Level>; color_roles: string[] } | null>(null)
  const [provided, setProvided] = useState<{ name: string; label: string }[]>([])
  const [brands, setBrands] = useState<{ id: number; name: string }[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [versions, setVersions] = useState<{ id: number; version_no: number }[]>([])
  const [viewVersion, setViewVersion] = useState<number | null>(null)
  const [form, setForm] = useState<BrandForm | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string[] } | null>(null)
  const [logoInfo, setLogoInfo] = useState<string>('')
  const [busy, setBusy] = useState(false)

  const loadBrands = useCallback(() => api.brands().then(setBrands), [])

  useEffect(() => {
    Promise.all([api.defaults(), api.providedFonts()]).then(([d, f]) => {
      setDefaults(d)
      setProvided(f)
      setForm(emptyForm(d.color_roles, d.levels))
    })
    loadBrands()
  }, [loadBrands])

  if (!defaults || !form) return <main className="page"><p>불러오는 중…</p></main>
  const set = (f: BrandForm) => setForm(f)

  const newBrand = () => {
    setSelected(null); setVersions([]); setViewVersion(null); setLogoInfo(''); setMsg(null)
    setForm(emptyForm(defaults.color_roles, defaults.levels))
  }

  const openBrand = async (id: number, versionId?: number) => {
    const b = await api.brand(id, versionId)
    const v = b.version
    setSelected(id); setVersions(b.versions); setViewVersion(v.id); setMsg(null)
    setLogoInfo(v.logo_asset_id ? `업로드됨 (파일 #${v.logo_asset_id})` : '')
    setForm({
      name: b.name,
      logo_asset_id: v.logo_asset_id,
      logo_margin_percent: v.logo_margin?.percent ?? null,
      colors: v.colors,
      fonts: v.fonts,
      icon_style: v.icon_style?.corner ? v.icon_style : emptyForm([], defaults.levels).icon_style,
      image_tone: v.image_tone,
      voice: v.voice,
      required_phrases: v.required_phrases,
      forbidden_phrases: v.forbidden_phrases,
      levels: { ...defaults.levels, ...v.levels },
    })
  }

  const save = async () => {
    setBusy(true); setMsg(null)
    try {
      const res = selected ? await api.update(selected, form) : await api.create(form)
      const w: string[] = res.warnings || []
      setMsg({ ok: true, text: [`저장했습니다. 버전 ${res.version.version_no}`, ...w.map((x) => `⚠ ${x}`)] })
      await loadBrands()
      await openBrand(res.id)
      setMsg({ ok: true, text: [`저장했습니다. 버전 ${res.version.version_no}`, ...w.map((x) => `⚠ ${x}`)] })
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

  return (
    <div className="layout">
      <aside className="side">
        <h2>브랜드</h2>
        <button className="primary" onClick={newBrand}>+ 새 브랜드</button>
        <ul>
          {brands.map((b) => (
            <li key={b.id}><button className={b.id === selected ? 'on' : ''} onClick={() => openBrand(b.id)}>{b.name}</button></li>
          ))}
          {brands.length === 0 && <li className="muted">등록된 브랜드가 없습니다</li>}
        </ul>
      </aside>

      <main className="page">
        <h1>{selected ? '브랜드 수정' : '브랜드 등록'}</h1>
        {selected && (
          <p className="muted">
            저장하면 기존 버전은 보존되고 새 버전이 만들어집니다. 보기:{' '}
            <select value={viewVersion ?? ''} onChange={(e) => openBrand(selected, Number(e.target.value))}>
              {versions.map((v) => <option key={v.id} value={v.id}>버전 {v.version_no}</option>)}
            </select>
          </p>
        )}

        <section className="card">
          <header><h3>브랜드명</h3></header>
          <input value={form.name} maxLength={100} placeholder="브랜드 이름" onChange={(e) => set({ ...form, name: e.target.value })} />
        </section>

        <Section item="logo" form={form} set={set}>
          <input type="file" accept=".png,.svg" onChange={(e) =>
            upload('logo', e.target.files?.[0], (r) => {
              set({ ...form, logo_asset_id: r.id })
              setLogoInfo(`${r.name} (${r.width ?? '?'}×${r.height ?? '?'}px, 원본 비율 보존)`)
            })} />
          <p className="muted">PNG(투명 배경) 또는 SVG. 로고는 원본 비율을 유지하며 변형·재생성하지 않습니다.</p>
          {form.logo_asset_id && <div className="row"><img className="logo" src={`/api/assets/${form.logo_asset_id}/file`} alt="로고" /><span>{logoInfo}</span></div>}
          <label>최소 여백 (로고 크기 대비 %)
            <input type="number" min={0} max={100} value={form.logo_margin_percent ?? ''}
              onChange={(e) => set({ ...form, logo_margin_percent: e.target.value === '' ? null : Number(e.target.value) })} />
          </label>
        </Section>

        <Section item="colors" form={form} set={set}>
          {form.colors.map((c, i) => {
            const isDefault = defaults.color_roles.includes(c.role) && i < defaults.color_roles.length
            return (
              <div className="row" key={i}>
                {isDefault ? <strong className="role">{c.role}</strong> :
                  <input className="role" placeholder="역할 이름" value={c.role} onChange={(e) => setColor(i, { role: e.target.value })} />}
                <input type="color" value={/^#[0-9a-fA-F]{6}$/.test(c.hex) ? c.hex : '#000000'} onChange={(e) => setColor(i, { hex: e.target.value.toUpperCase() })} />
                <input className="hex" value={c.hex} maxLength={7} onChange={(e) => setColor(i, { hex: e.target.value })} />
                {!isDefault && <button onClick={() => set({ ...form, colors: form.colors.filter((_, k) => k !== i) })}>삭제</button>}
              </div>
            )
          })}
          <button onClick={() => set({ ...form, colors: [...form.colors, { role: '', hex: '#000000' }] })}>+ 역할 추가</button>
        </Section>

        <Section item="fonts" form={form} set={set}>
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

        <Section item="icon_style" form={form} set={set}>
          <div className="row">
            <label>선 굵기 <input type="number" min={0.5} max={12} step={0.5} value={form.icon_style.stroke_width}
              onChange={(e) => set({ ...form, icon_style: { ...form.icon_style, stroke_width: Number(e.target.value) } })} /></label>
            <label>모서리 <select value={form.icon_style.corner}
              onChange={(e) => set({ ...form, icon_style: { ...form.icon_style, corner: e.target.value as '각진' | '둥근' } })}>
              <option>각진</option><option>둥근</option></select></label>
            <label>채움 <select value={form.icon_style.fill}
              onChange={(e) => set({ ...form, icon_style: { ...form.icon_style, fill: e.target.value as '윤곽' | '채움' } })}>
              <option>윤곽</option><option>채움</option></select></label>
          </div>
        </Section>

        <Section item="image_tone" form={form} set={set}>
          <textarea rows={3} placeholder="예: 밝고 따뜻한 자연광, 채도 높은 파스텔 톤"
            value={form.image_tone.description} onChange={(e) => set({ ...form, image_tone: { ...form.image_tone, description: e.target.value } })} />
          <input type="file" accept=".png,.jpg,.jpeg,.webp" multiple onChange={async (e) => {
            for (const file of Array.from(e.target.files ?? []))
              await upload('tone_ref', file, (r) => setForm((p) => p && ({ ...p, image_tone: { ...p.image_tone, reference_asset_ids: [...p.image_tone.reference_asset_ids, r.id] } })))
          }} />
          <div className="thumbs">
            {form.image_tone.reference_asset_ids.map((id) => (
              <figure key={id}><img src={`/api/assets/${id}/file`} alt="기준 이미지" />
                <button onClick={() => set({ ...form, image_tone: { ...form.image_tone, reference_asset_ids: form.image_tone.reference_asset_ids.filter((x) => x !== id) } })}>삭제</button></figure>
            ))}
          </div>
        </Section>

        <Section item="voice" form={form} set={set}>
          <textarea rows={3} placeholder="예: 친근한 존댓말, 짧은 문장"
            value={form.voice.description} onChange={(e) => set({ ...form, voice: { ...form.voice, description: e.target.value } })} />
          <label>예시 문구 (줄바꿈으로 구분)
            <textarea rows={3} value={form.voice.sample_phrases.join('\n')}
              onChange={(e) => set({ ...form, voice: { ...form.voice, sample_phrases: lines(e.target.value) } })} />
          </label>
        </Section>

        <Section item="required_phrases" form={form} set={set}>
          <textarea rows={3} placeholder="줄바꿈으로 구분" value={form.required_phrases.join('\n')}
            onChange={(e) => set({ ...form, required_phrases: lines(e.target.value) })} />
        </Section>

        <Section item="forbidden_phrases" form={form} set={set}>
          <textarea rows={3} placeholder="줄바꿈으로 구분" value={form.forbidden_phrases.join('\n')}
            onChange={(e) => set({ ...form, forbidden_phrases: lines(e.target.value) })} />
        </Section>

        {msg && <div className={msg.ok ? 'notice ok' : 'notice err'}>{msg.text.map((t, i) => <div key={i}>{t}</div>)}</div>}
        <div className="actions">
          <button className="primary" disabled={busy || !form.name.trim()} onClick={save}>{selected ? '새 버전으로 저장' : '브랜드 저장'}</button>
        </div>
      </main>
    </div>
  )
}
