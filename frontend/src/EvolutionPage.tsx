import { useEffect, useState } from 'react'

/* RAG 진화 시연: 같은 입력이 단계마다 어떻게 달라졌는지 저장된 실제 결과로 보여 준다(모델 호출 없음). */
type Copy = { headline: string; body: string; cta: string; angle?: string; evidence?: string; long_body?: string; violations?: string[] }
type Stage = {
  no: number; title: string; commit: string; changed: string; why: string; result: string; cost: string; model?: string
  copies?: Copy[]; metrics?: { label: string; value: string }[]; chunks?: { layer: string; section: string; score: number }[]
  visual?: { motifs: string; direction_before: string; direction_after: string }; images?: { file: string; label: string }[]
}
type Data = { title: string; subtitle: string; notice: string; input: Record<string, string>; stages: Stage[] }

const INPUT_LABEL: Record<string, string> = { brand: '브랜드', product: '상품', message: '핵심 메시지', target: '타깃', limits: '글자 수 제한' }

function CopyCards({ copies }: { copies: Copy[] }) {
  return (
    <div className="ev-copies">
      {copies.map((c, i) => (
        <article key={i} className="ev-copy">
          {c.angle && <span className="ev-angle">{c.angle}</span>}
          <h4>{c.headline}</h4>
          <p>{c.body}</p>
          <span className="ev-cta">{c.cta}</span>
          {c.long_body && <p className="ev-long"><strong>인스타그램 게시 문구 ({c.long_body.length}자)</strong><br />{c.long_body}</p>}
          {c.evidence && <p className="muted ev-note">근거: {c.evidence}</p>}
          {c.violations?.map((v) => <p key={v} className="ev-bad">규칙 위반: {v}</p>)}
        </article>
      ))}
    </div>
  )
}

export default function EvolutionPage() {
  const [data, setData] = useState<Data | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [no, setNo] = useState(0)
  useEffect(() => {
    fetch('/api/evolution').then((r) => { if (!r.ok) throw new Error('시연 데이터가 없습니다 (demo/evolution)'); return r.json() })
      .then(setData).catch((e) => setErr((e as Error).message))
  }, [])
  if (err) return <main className="page"><div className="notice err">{err}</div></main>
  if (!data) return <main className="page"><p>불러오는 중…</p></main>
  const s = data.stages[no]
  return (
    <main className="ev">
      <h1>{data.title}</h1>
      <p className="muted">{data.subtitle}</p>
      <div className="notice">{data.notice}</div>
      <section className="card">
        <header><h3>같은 입력</h3></header>
        <dl className="camp-input-grid">
          {Object.entries(data.input).map(([k, v]) => <div key={k} style={{ display: 'contents' }}><dt>{INPUT_LABEL[k] ?? k}</dt><dd>{v}</dd></div>)}
        </dl>
      </section>
      <nav className="ev-steps" aria-label="단계">
        {data.stages.map((x, i) => (
          <button key={x.no} className={i === no ? 'on' : ''} aria-current={i === no ? 'step' : undefined} onClick={() => setNo(i)}>
            <span>{x.no}</span>{x.title}
          </button>
        ))}
      </nav>
      <section className="card">
        <header><h3>{s.no}단계 · {s.title}</h3><span className="muted">{s.commit}</span></header>
        <dl className="ev-facts">
          <dt>바뀐 것</dt><dd>{s.changed}</dd>
          <dt>왜</dt><dd>{s.why}</dd>
          <dt>결과</dt><dd>{s.result}</dd>
          <dt>비용</dt><dd>{s.cost}</dd>
        </dl>
        {s.metrics && <div className="ev-metrics">{s.metrics.map((m) => <div key={m.label}><strong>{m.value}</strong><span>{m.label}</span></div>)}</div>}
        {s.chunks && (
          <table className="an-table">
            <thead><tr><th>층</th><th>찾은 조각</th><th>유사도</th></tr></thead>
            <tbody>{s.chunks.map((c) => <tr key={c.section}><td>{c.layer}</td><td>{c.section}</td><td>{c.score.toFixed(3)}</td></tr>)}</tbody>
          </table>
        )}
        {s.copies && <CopyCards copies={s.copies} />}
        {s.visual && (
          <dl className="ev-facts">
            <dt>제품 소재</dt><dd>{s.visual.motifs}</dd>
            <dt>이전 연출</dt><dd>{s.visual.direction_before}</dd>
            <dt>이후 연출</dt><dd>{s.visual.direction_after}</dd>
          </dl>
        )}
        {s.images && (
          <div className="ev-images">
            {s.images.map((im) => <figure key={im.file}><img src={`/api/evolution/image/${im.file}`} alt={im.label} /><figcaption>{im.label}</figcaption></figure>)}
          </div>
        )}
      </section>
      <div className="ev-move">
        <button disabled={no === 0} onClick={() => setNo(no - 1)}>이전 단계</button>
        <button className="primary" disabled={no === data.stages.length - 1} onClick={() => setNo(no + 1)}>다음 단계</button>
      </div>
    </main>
  )
}
