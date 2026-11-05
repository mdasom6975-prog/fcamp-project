import { useCallback, useEffect, useState } from 'react'
import ImageGrid, { type GridItem, type ImgRow } from './ImageGrid'

type Item = {
  id: number; category: string; item: string; status: string; raw_status: string; message: string
  level: string | null; blocking: boolean; needs_human: boolean; confirmed: boolean
  evidence?: { phrase?: string; law?: string; reason?: string; suggest?: string; grounded?: boolean } | null   // 법적·기능성 항목의 근거 조문·수정 제안(S2)
}
type Group = { preset_id: number | null; name: string; items: Item[]; summary: Record<string, number>; export_ready: boolean }
type Exp = {
  id: number; status: string; filename: string; file_count: number; created_at: string; download_url: string
  unresolved: { preset: string; item: string; status: string; message: string }[]
}
type Review = {
  reviewed: boolean; stale: boolean; groups: Group[]; export_ready: boolean
  blockers: { preset: string | null; item: string; status: string; message: string }[]
  fix_enabled?: boolean; fix_max?: number; fix_rounds?: Record<string, number>   // 6단계 "고쳐 보기"(copyfix): 켜짐 여부·최대 회차·지면별 쓴 회차
}

const cls = (s: string) => (s === '통과' ? 'b-ok' : s === '경고' ? 'b-warn' : 'b-need')

async function call(method: string, url: string, body?: unknown) {
  const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) {
    const d = j.detail
    throw new Error(typeof d === 'string' ? d : d?.message || '요청에 실패했습니다')
  }
  return j
}

export default function ReviewPanel({ designId }: { designId: number }) {
  const [rv, setRv] = useState<Review | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [exps, setExps] = useState<Exp[]>([])
  const [picked, setPicked] = useState<number[] | null>(null)   // null = 전체
  const [exBusy, setExBusy] = useState(false)
  const [exMsg, setExMsg] = useState<{ ok: boolean; text: string } | null>(null)

  // 썸네일 그리드용: 지면 목록과 최신 합성본
  const [items, setItems] = useState<GridItem[]>([])
  const [imgs, setImgs] = useState<Record<number, ImgRow>>({})
  const [focus, setFocus] = useState<number | null>(null)

  const load = useCallback(() => call('GET', `/api/designs/${designId}/review`).then(setRv).catch(() => undefined), [designId])
  // 글자 수를 넘는 지면: 그 지면의 선택 카피만 Claude 1회로 제한에 맞게 고친다. 고치면 그 지면 검수가 무효화되어 다시 검수해야 한다
  const [fitting, setFitting] = useState<number | null>(null)
  const [fitMsg, setFitMsg] = useState<string | null>(null)
  const fit = async (presetId: number) => {
    setFitting(presetId); setFitMsg(null)
    try {
      const j = await call('POST', `/api/designs/${designId}/presets/${presetId}/copy/fit`)
      setFitMsg(j.fits ? `문구를 줄였습니다 (${(j.changed as string[]).join(', ')}). '다시 검수'를 눌러 확인하세요.` : j.note)
      await load()
    } catch (e) { setFitMsg((e as Error).message) } finally { setFitting(null) }
  }
  // 6단계 "고쳐 보기": 차단된 법적·기능성·금지 표현이 든 필드만 Claude 1회로 고치고 코드로 재검사, 줄었을 때만 채택. 검수는 서버가 바로 다시 돌린다
  const [fixing, setFixing] = useState<number | null>(null)
  const [fixMsg, setFixMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const fix = async (presetId: number) => {
    setFixing(presetId); setFixMsg(null)
    try {
      const j = await call('POST', `/api/designs/${designId}/presets/${presetId}/copy/fix`)
      setFixMsg({ ok: !!j.adopted, text: j.note })
      setRv(j.review); setOpen(true)
      await loadImgs()
    } catch (e) { setFixMsg({ ok: false, text: (e as Error).message }) } finally { setFixing(null) }
  }
  const fixable = (g: Group) => g.preset_id !== null && g.items.some((i) => i.blocking && (i.category === '법적' || (i.category === '브랜드' && i.item === '금지 표현')))
  const fixUsed = (pid: number) => rv?.fix_rounds?.[String(pid)] ?? 0
  const loadExps = useCallback(() => call('GET', `/api/designs/${designId}/exports`).then(setExps).catch(() => undefined), [designId])
  const loadImgs = useCallback(() => Promise.all([
    call('GET', `/api/designs/${designId}`).then((d: { presets: { id: number; platform: string; name: string; width: number | null; height: number | null }[] }) =>
      setItems(d.presets.map((p) => ({ preset_id: p.id, platform: p.platform, name: p.name, width: p.width, height: p.height })))),
    call('GET', `/api/designs/${designId}/images`).then((rows: ImgRow[]) =>
      setImgs(Object.fromEntries(rows.map((r) => [r.preset_id, r])))),
  ]).catch(() => undefined), [designId])
  useEffect(() => { load(); loadExps(); loadImgs() }, [load, loadExps, loadImgs])

  // 카드 배지: 그 지면의 검수 요약(수정 필요·확인 필요·경고·통과) 또는 '검수 필요'
  const cardBadge = (it: GridItem, row: ImgRow | undefined) => {
    if (!row?.composite) return <span className="badge b-warn">이미지 없음</span>
    const g = rv?.groups.find((x) => x.preset_id === it.preset_id)
    if (!rv?.reviewed || !g || g.items.length === 0) return <span className="badge b-warn">검수 필요</span>
    return (
      <>
        {(['수정 필요', '확인 필요', '경고', '통과'] as const).map((k) => g.summary[k]
          ? <span key={k} className={`badge ${cls(k)}`}>{k} {g.summary[k]}</span> : null)}
        {g.export_ready && <span className="badge b-ok">내보내기 가능</span>}
      </>
    )
  }
  const focusGroup = (pid: number) => {
    setFocus(pid); setOpen(true)
    window.setTimeout(() => document.getElementById(`rv-group-${pid}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
  }

  const presetGroups = (rv?.groups ?? []).filter((g) => g.preset_id !== null)
  const targetIds = picked ?? presetGroups.map((g) => g.preset_id as number)
  const targetGroups = rv?.groups.filter((g) => g.preset_id === null || targetIds.includes(g.preset_id)) ?? []
  const allReady = !!rv?.reviewed && targetGroups.length > 0 && targetGroups.every((g) => g.export_ready)

  const doExport = async (draft: boolean) => {
    setExBusy(true); setExMsg(null)
    try {
      const body = presetGroups.length ? { preset_ids: targetIds, draft } : { draft }
      const e: Exp = await call('POST', `/api/designs/${designId}/export`, body)
      setExMsg({ ok: true, text: `${e.status} 묶음을 만들었습니다: ${e.filename} (${e.file_count}개 파일)` })
      await loadExps()
    } catch (e) { setExMsg({ ok: false, text: (e as Error).message }) } finally { setExBusy(false) }
  }

  const run = async () => {
    setBusy(true); setErr(null)
    try { setRv(await call('POST', `/api/designs/${designId}/review`)); setOpen(true) }
    catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const confirm = async (id: number, confirmed: boolean) => {
    try { setRv(await call('POST', `/api/review-results/${id}/confirm`, { confirmed })) }
    catch (e) { setErr((e as Error).message) }
  }

  return (
    <div className="copy">
      {/* 검수 실행·상태는 맨 위에: 지면 카드가 많으면 버튼이 화면 아래로 밀려 찾기 어렵다 */}
      <div className="row">
        <button className="primary" disabled={busy} onClick={run}>{rv?.reviewed ? '다시 검수' : '검수 실행'}</button>
        {rv?.reviewed && (
          <span className={`badge ${rv.export_ready ? 'b-ok' : 'b-need'}`}>
            {rv.export_ready ? '검수 완료 — 내보내기 가능' : '수정·확인 필요 — 검수 완료 내보내기 차단'}
          </span>
        )}
        {rv && !rv.reviewed && <span className="muted">검수 결과가 없습니다. 시안이 바뀌면 다시 검수해야 합니다.</span>}
        {fitMsg && <span className="notice ok">{fitMsg}</span>}
        {fixMsg && <span className={`notice ${fixMsg.ok ? 'ok' : 'err'}`}>{fixMsg.text}</span>}
        <span className="muted">실제로 내려받을 파일(인코딩 결과)과 편집 원본을 기준으로 검사합니다. 광고 심사 통과를 보장하지 않습니다.</span>
      </div>
      {err && <div className="notice err">{err}</div>}
      <ImageGrid items={items} rows={imgs} selected={focus} onSelect={focusGroup} badge={cardBadge}
        emptyText="검수할 지면이 없습니다." />

      {rv?.reviewed && rv.blockers.length > 0 && (
        <div className="notice err">
          <strong>내보내기를 막는 항목 {rv.blockers.length}건</strong>
          {rv.blockers.map((b, i) => <div key={i}>[{b.status}] {b.preset ? `${b.preset} · ` : ''}{b.item}: {b.message}</div>)}
        </div>
      )}

      {rv?.reviewed && (
        <button className="link" onClick={() => setOpen((v) => !v)}>{open ? '결과 접기' : '결과 펼치기'}</button>
      )}
      {open && rv?.groups.map((g) => (
        <div key={g.name} className={`copy-group ${g.preset_id !== null && g.preset_id === focus ? 'focus' : ''}`}
          id={g.preset_id !== null ? `rv-group-${g.preset_id}` : undefined}>
          <h4>
            {g.name}{' '}
            {(['수정 필요', '확인 필요', '경고', '통과'] as const).map((k) => g.summary[k] ? (
              <span key={k} className={`badge ${cls(k)}`} style={{ marginRight: 4 }}>{k} {g.summary[k]}</span>
            ) : null)}
            {g.preset_id !== null && fixUsed(g.preset_id) > 0 && (
              <span className="badge b-warn" style={{ marginRight: 4 }}>고쳐 보기 {fixUsed(g.preset_id)}/{rv?.fix_max ?? 3}회</span>
            )}
            {rv?.fix_enabled && fixable(g) && (
              <button type="button" className="fix-btn" disabled={fixing !== null || busy || fixUsed(g.preset_id as number) >= (rv.fix_max ?? 3)}
                onClick={() => fix(g.preset_id as number)}
                title="차단된 법적·기능성·금지 표현이 든 필드만 근거 조문·수정 제안을 붙여 고쳐 쓰게 하고, 코드로 다시 검사해 차단이 줄었을 때만 받아들입니다">
                {fixing === g.preset_id ? '고치는 중…' : fixUsed(g.preset_id as number) >= (rv.fix_max ?? 3)
                  ? '못 고침 — 직접 편집' : `고쳐 보기 (${fixUsed(g.preset_id as number) + 1}/${rv.fix_max ?? 3}회 · 채팅 1회)`}
              </button>
            )}
          </h4>
          {g.items.length === 0 && <p className="muted">이 지면은 아직 검수 결과가 없습니다 (수정 후 무효화됨). 다시 검수해 주세요.</p>}
          <table className="rtable">
            <tbody>
              {[...g.items].sort((a, b) => ['수정 필요', '확인 필요', '경고', '통과'].indexOf(a.status) - ['수정 필요', '확인 필요', '경고', '통과'].indexOf(b.status)).map((it) => (
                <tr key={it.id}>
                  <td style={{ whiteSpace: 'nowrap' }}><span className={`badge ${cls(it.status)}`}>{it.status}</span></td>
                  <td>{it.category}</td>
                  <td>{it.item}{it.blocking && <span className="badge b-need" style={{ marginLeft: 4 }}>차단</span>}</td>
                  <td className="muted">
                    {it.evidence?.law ? (
                      <>
                        {it.evidence.reason || it.message}
                        <div className="rv-evidence"><span>근거</span> {it.evidence.law}{it.evidence.suggest ? <><br /><span>제안</span> {it.evidence.suggest}</> : null}{it.evidence.grounded ? <><br /><span>참고</span> 입력에 있던 표현이라 근거 자료를 확인하세요</> : null}</div>
                      </>
                    ) : it.message}
                  </td>
                  <td>
                    {it.category === '문구' && it.item.includes('글자 수') && it.status === '수정 필요' && g.preset_id !== null && (
                      <button type="button" disabled={fitting !== null || busy} onClick={() => fit(g.preset_id as number)}>
                        {fitting === g.preset_id ? '줄이는 중…' : '지면에 맞게 줄이기 (채팅 1회)'}
                      </button>
                    )}
                    {it.needs_human && it.raw_status === '확인 필요' && (
                      <label style={{ display: 'inline-flex', gap: 4, whiteSpace: 'nowrap' }}>
                        <input type="checkbox" checked={it.confirmed} onChange={(e) => confirm(it.id, e.target.checked)} /> 확인함
                      </label>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <div className="imgblock">
        <strong>내보내기</strong>
        <p className="muted">
          규격별 이미지와 카피(TXT/CSV), 파일 목록·브랜드/규격 버전·검수 결과를 ZIP 으로 내려받습니다.
          외부 광고 플랫폼 업로드는 직접 하셔야 하며, 파일 규격 검수는 광고 심사를 대체하지 않습니다.
        </p>
        {presetGroups.length > 0 && (
          <div>
            {presetGroups.map((g) => (
              <label key={g.preset_id} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 14 }}>
                <input type="checkbox" checked={targetIds.includes(g.preset_id as number)}
                  onChange={(e) => setPicked(e.target.checked ? [...targetIds, g.preset_id as number] : targetIds.filter((x) => x !== g.preset_id))} />
                {g.name}
                <span className={`badge ${!rv?.reviewed || g.items.length === 0 ? 'b-warn' : g.export_ready ? 'b-ok' : 'b-need'}`}>
                  {!rv?.reviewed || g.items.length === 0 ? '검수 필요' : g.export_ready ? '내보내기 가능' : '차단 항목 있음'}
                </span>
              </label>
            ))}
          </div>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <button className="primary" disabled={exBusy || !allReady || targetGroups.length === 0} onClick={() => doExport(false)}>
            검수 완료 내보내기
          </button>
          <button disabled={exBusy || (presetGroups.length > 0 && targetIds.length === 0)} onClick={() => doExport(true)}>
            미검수 작업본 내보내기
          </button>
        </div>
        {!allReady && <p className="muted">선택한 항목 중 검수를 통과하지 못했거나 검수 결과가 없으면 '검수 완료' 묶음은 만들 수 없습니다. 미검수 작업본에는 해결되지 않은 항목이 함께 기록됩니다.</p>}
        {exMsg && <div className={exMsg.ok ? 'notice ok' : 'notice err'}>{exMsg.text}</div>}
        {exps.length > 0 && (
          <table className="rtable">
            <tbody>
              {exps.map((e) => (
                <tr key={e.id}>
                  <td><span className={`badge ${e.status === '검수 완료' ? 'b-ok' : 'b-warn'}`}>{e.status}</span></td>
                  <td><a href={e.download_url}>{e.filename}</a></td>
                  <td className="muted">{e.file_count}개 파일 · {e.created_at.slice(0, 16).replace('T', ' ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
