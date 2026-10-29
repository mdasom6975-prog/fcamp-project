import { useCallback, useEffect, useState } from 'react'

type Condition = { name: string; value: string | null; level: string; status: string; note?: string | null }
type TextLimit = {
  field: string; min_chars?: number | null; max_chars?: number | null; min_px?: number | null
  max_px?: number | null; count_method: string; required?: boolean | null; level: string; note?: string | null
}
type Preset = {
  id: number; name: string; ad_type: string; content_kind: string; status: string
  production_width: number | null; production_height: number | null; ratio: string | null
  conditions: Condition[]; text_limits: TextLimit[]; safe_area: Record<string, string | number> | null
  required_assets: string[]; note: string | null; source_urls: string[]; checked_at: string | null
  reviewable: boolean
}
type Platform = { id: number; name: string; media_only: boolean; note: string | null; ad_types: { name: string; presets: Preset[] }[] }

type SourceState = { url: string; status: string; error: string | null; changed_at: string | null }
type UpdateState = {
  last_run: string | null; cooldown_left: number; running: boolean
  platforms: { name: string; last_fetched_at: string | null; sources: SourceState[] }[]
}

const fmtTime = (iso: string | null) => (iso ? iso.replace('T', ' ').slice(0, 16) : '-')
const fmtLeft = (sec: number) => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`

const badge = (s: string) => (s === '확인됨' ? 'b-ok' : s === '충돌' ? 'b-warn' : 'b-need')

const safeLabel: Record<string, [string, string]> = {
  top_pct: ['상단', '%'], bottom_pct: ['하단', '%'], left_pct: ['좌', '%'], right_pct: ['우', '%'],
  top_px: ['상단', 'px'], bottom_px: ['하단', 'px'], left_px: ['좌', 'px'], right_px: ['우', 'px'],
}

function limitText(t: TextLimit) {
  const parts: string[] = []
  if (t.min_chars != null) parts.push(`${t.min_chars}자 이상`)
  if (t.max_chars != null) parts.push(`${t.max_chars}자 이하`)
  if (t.min_px != null) parts.push(`${t.min_px}px 이상`)
  if (t.max_px != null) parts.push(`${t.max_px}px 이하`)
  return parts.join(', ') || '확인 불가'
}

const groupMeta: { key: string; title: string; cls: string }[] = [
  { key: '필수', title: '강제 조건 (위반 시 수정 필요)', cls: 'g-must' },
  { key: '권장', title: '권장 조건 (위반 시 경고)', cls: 'g-rec' },
]

function Detail({ p, platform }: { p: Preset; platform: string }) {
  const groups = [
    ...groupMeta.map((g) => ({ ...g, items: p.conditions.filter((c) => c.level === g.key) })),
    { key: 'etc', title: '기타 조건', cls: 'g-etc', items: p.conditions.filter((c) => c.level !== '필수' && c.level !== '권장') },
  ].filter((g) => g.items.length > 0)

  return (
    <aside className="detail">
      <div className="detail-head">
        <h2>{platform} · {p.name}</h2>
        <span className={`badge ${badge(p.status)}`}>{p.status}</span>
      </div>
      <div className="muted detail-sub">{p.content_kind} · {p.ad_type}</div>
      {!p.reviewable && <div className="notice err">공식 규격을 확인하지 못해 검수 대상에서 제외됩니다.</div>}
      {p.production_width && (
        <p><strong>제작 기본값</strong> {p.production_width}×{p.production_height}px {p.ratio ? `(${p.ratio})` : ''}</p>
      )}
      {p.note && <p className="muted">※ {p.note}</p>}

      {groups.map((g) => (
        <div className="spec-group" key={g.key}>
          <div className="spec-group-title">{g.title}</div>
          <dl className={`spec-list ${g.cls}`}>
            {g.items.map((c, i) => (
              <div className="spec-item" key={i}>
                <div className="spec-line">
                  <dt>{c.name}</dt>
                  <dd>
                    {c.value ?? <span className="muted">확인 불가</span>}
                    {c.status !== '확인됨' && <span className={`badge ${badge(c.status)}`}>{c.status}</span>}
                  </dd>
                </div>
                {c.note && <div className="muted">{c.note}</div>}
              </div>
            ))}
          </dl>
        </div>
      ))}

      {p.text_limits.length > 0 && (
        <div className="spec-group">
          <div className="spec-group-title">문구 제한</div>
          <dl className="spec-list g-etc">
            {p.text_limits.map((t, i) => (
              <div className="spec-item" key={i}>
                <div className="spec-line">
                  <dt>{t.field}{t.required ? ' (필수)' : ''}</dt>
                  <dd>{limitText(t)}</dd>
                </div>
                <div className="muted">{t.count_method} · {t.level}{t.note ? ` · ${t.note}` : ''}</div>
              </div>
            ))}
          </dl>
        </div>
      )}

      {p.safe_area && (
        <div className="spec-group">
          <div className="spec-group-title">안전영역</div>
          <p>
            {Object.entries(p.safe_area).filter(([k]) => k in safeLabel).map(([k, v]) => `${safeLabel[k][0]} ${v}${safeLabel[k][1]}`).join(' · ')}
            {p.safe_area.level ? ` (${p.safe_area.level})` : ''}
          </p>
          {p.safe_area.note && <p className="muted">{p.safe_area.note}</p>}
        </div>
      )}

      {p.required_assets.length > 0 && (
        <div className="spec-group"><div className="spec-group-title">필수 자산</div><p>{p.required_assets.join(', ')}</p></div>
      )}

      <div className="spec-group">
        <div className="spec-group-title">출처</div>
        {p.source_urls.length === 0 ? <p className="muted">출처 없음</p> : (
          <ul className="sources">{p.source_urls.map((u) => <li key={u}><a href={u} target="_blank" rel="noreferrer">{u}</a></li>)}</ul>
        )}
      </div>
      <p className="muted">확인일: {p.checked_at ?? '확인 불가'} · 공식 문서 기준이며 광고 심사 통과를 보장하지 않습니다.</p>
    </aside>
  )
}

export default function PresetLibrary() {
  const [platforms, setPlatforms] = useState<Platform[] | null>(null)
  const [error, setError] = useState(false)
  const [focus, setFocus] = useState<{ p: Preset; platform: string } | null>(null)

  const [upd, setUpd] = useState<UpdateState | null>(null)
  const [updBusy, setUpdBusy] = useState(false)
  const [updMsg, setUpdMsg] = useState<string | null>(null)
  const [left, setLeft] = useState(0)

  const applyUpdate = useCallback((u: UpdateState) => { setUpd(u); setLeft(u.cooldown_left) }, [])

  useEffect(() => {
    fetch('/api/presets').then((r) => r.json()).then(setPlatforms).catch(() => setError(true))
    fetch('/api/presets/update-status').then((r) => r.json()).then(applyUpdate).catch(() => {})
  }, [applyUpdate])

  // 처음 들어오면 첫 행이 눌린 상태(상세가 바로 보인다). 이미 고른 행이 있으면 건드리지 않는다
  useEffect(() => {
    if (!platforms) return
    setFocus((cur) => {
      if (cur) return cur
      for (const pl of platforms) for (const a of pl.ad_types) if (a.presets[0]) return { p: a.presets[0], platform: pl.name }
      return null
    })
  }, [platforms])

  // 대기 시간 카운트다운 (서버가 최소 간격을 강제하고, 화면은 버튼만 잠근다)
  useEffect(() => {
    if (left <= 0) return
    const t = setInterval(() => setLeft((v) => Math.max(0, v - 1)), 1000)
    return () => clearInterval(t)
  }, [left > 0]) // eslint-disable-line

  const runUpdate = async () => {
    if (updBusy || left > 0) return
    setUpdBusy(true); setUpdMsg(null)
    try {
      const r = await fetch('/api/presets/update', { method: 'POST' })
      const j = await r.json().catch(() => ({}))
      if (r.status === 429) { setLeft(j.cooldown_left ?? 0); setUpdMsg(j.detail); return }
      if (!r.ok) throw new Error(j.detail || '업데이트에 실패했습니다')
      applyUpdate(j)
      const bad = (j as UpdateState).platforms.flatMap((p) => p.sources).filter((s) => s.status === '실패').length
      const chg = (j as UpdateState).platforms.flatMap((p) => p.sources).filter((s) => s.status === '변경 감지').length
      setUpdMsg(chg ? `원문 변경 ${chg}건을 감지했습니다. 공식 페이지를 확인하세요.` : bad ? `${bad}건을 가져오지 못했습니다.` : '최신 상태를 확인했습니다. 변경은 없습니다.')
    } catch (e) {
      setUpdMsg((e as Error).message)
    } finally {
      setUpdBusy(false)
    }
  }

  if (error) return <main className="page"><p>규격을 불러오지 못했습니다.</p></main>
  if (!platforms) return <main className="page"><p>불러오는 중…</p></main>


  const rows = platforms.flatMap((pl) => pl.ad_types.flatMap((a) => a.presets.map((p) => ({ p, platform: pl.name, adType: a.name }))))
  const mediaOnly = platforms.filter((pl) => pl.media_only)
  const verified = rows.filter(({ p }) => p.status === '확인됨').length

  return (
    <div className="lib">
      <div className="lib-head">
        <div className="lib-title">
          <h1>규격 라이브러리</h1>
          <div className="lib-update">
            <button type="button" className="primary" onClick={runUpdate} disabled={updBusy || left > 0}>
              {updBusy ? '업데이트 중…' : left > 0 ? `${fmtLeft(left)} 후 가능` : '규격 업데이트'}
            </button>
            <span className="muted">마지막 확인 {fmtTime(upd?.last_run ?? null)}</span>
          </div>
        </div>
        {updMsg && <div className={`notice ${updMsg.includes('실패') || updMsg.includes('가져오지') || updMsg.includes('후 다시') ? 'err' : 'ok'}`}>{updMsg}</div>}
        <p className="muted">
          규격 {rows.length}개 · 공식 확인 {verified}개 ({platforms.filter((pl) => !pl.media_only).length}개 매체)
        </p>
      </div>

      <div className="lib-grid">
        <div className="lib-list">
          <section className="spec-table-wrap">
            <table className="spec-table">
              <thead>
                <tr><th>매체</th><th>광고 유형</th><th>지면</th><th>크기</th><th>확인일</th></tr>
              </thead>
              <tbody>
                {rows.map(({ p, platform, adType }) => (
                  <tr key={p.id} className={focus?.p.id === p.id ? 'on' : ''} onClick={() => setFocus({ p, platform })}>
                    <td className="strong">{platform}</td>
                    <td className="muted">{adType}</td>
                    <td>
                      <button type="button" className="link" onClick={() => setFocus({ p, platform })}>{p.name}</button>
                      {p.content_kind !== '완성형' && <span className="tag">{p.content_kind}</span>}
                    </td>
                    <td className="muted">{p.production_width}×{p.production_height}{p.ratio ? ` · ${p.ratio}` : ''}</td>
                    <td className="muted">{p.checked_at ?? '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {mediaOnly.map((pl) => (
            <div className="notice err" key={pl.id}>
              <strong>{pl.name}</strong> · 확인 필요 · 검수 불가 — {pl.note}
              <br />선택할 수 있는 지면이 없습니다. 공식 규격이 확인되면 추가됩니다.
            </div>
          ))}
        </div>

        <div className="lib-detail">
          {focus ? <Detail p={focus.p} platform={focus.platform} /> : <p className="muted">지면을 누르면 규격 상세가 표시됩니다.</p>}
        </div>
      </div>
    </div>
  )
}
