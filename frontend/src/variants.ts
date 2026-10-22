import { busy as overlay } from './Busy'

type Impact = { recompose: string[]; custom: { preset_id: number; name: string; fields: string[] }[] }
type Applied = {
  recomposed: number[]
  kept_custom: { name: string; field: string }[]
  skipped: { name: string; reason: string }[]
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : '요청에 실패했습니다')
  return j as T
}

/* 시안(A/B/C)을 모든 지면에 적용하고, 이미 만든 합성본도 새 문구로 다시 합성한다(배경 유지, 이미지 생성 비용 없음).
   편집 화면에서 저장한 지면은 위치·크기를 그대로 두고 문구만 바꾼다. 직접 고친 문구가 있으면 덮어쓸지 먼저 묻는다.
   반환 note 는 화면에 보여 줄 결과 안내(없으면 null). */
export async function applyVariant(designId: number, variantNo: number): Promise<{ note: string | null }> {
  const base = `/api/designs/${designId}/copy`
  const impact = await call<Impact>(`${base}/select-impact?variant_no=${variantNo}`)
  let overwrite = false
  if (impact.custom.length > 0) {
    const list = impact.custom.map((c) => `${c.name}(${c.fields.join('·')})`).join(', ')
    overwrite = window.confirm(
      `직접 고친 문구가 있는 지면이 ${impact.custom.length}개 있습니다.\n${list}\n\n` +
      '[확인] 직접 고친 문구도 새 시안 문구로 바꿉니다.\n[취소] 직접 고친 문구는 그대로 두고 나머지만 바꿉니다.')
  }
  const apply = () => call<Applied>(`${base}/apply-variant`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ variant_no: variantNo, overwrite_custom: overwrite }),
  })
  const res = impact.recompose.length > 0
    ? await overlay.run('새 시안 문구로 이미지를 다시 합성하는 중… (배경은 그대로, 비용 없음)', apply)
    : await apply()
  const parts: string[] = []
  if (res.recomposed.length) parts.push(`이미지 ${res.recomposed.length}개를 새 문구로 다시 합성했습니다. 검수는 다시 실행하세요.`)
  if (res.kept_custom.length) parts.push(`직접 고친 문구는 그대로 두었습니다: ${res.kept_custom.map((k) => `${k.name} ${k.field}`).join(', ')}`)
  if (res.skipped.length) parts.push(`다시 합성하지 못한 지면: ${res.skipped.map((s) => `${s.name}(${s.reason})`).join(', ')}`)
  return { note: parts.length ? parts.join(' ') : null }
}
