import { useEffect, useState } from 'react'
import { api, type KnowledgeSummary } from './api'

/* 첫 화면: 진입점 둘.
   - 아모레퍼시픽 입장(#/group): 지식이 등록된 그룹. 1단계 브랜드 탭으로 들어간다(지식 브라우저는 '지식' 탭, 00 정의서 v2).
   - 타 브랜드 생성(#/studio): 브랜드를 직접 등록하고 지식 검색 없이 시안을 만든다(RAG 유무 비교용 대조군, 기존 스튜디오 화면 그대로).
   소상공인(#/smallbiz)·RAG 진화 시연(#/evolution)은 주소로는 열리지만 첫 화면 카드에서는 뺐다(2026-11 재기획). */
export default function Landing() {
  const [k, setK] = useState<KnowledgeSummary | null>(null)
  const [groupName, setGroupName] = useState<string>('')
  useEffect(() => {
    api.brandGroups().then((gs) => {
      const g = gs[0]
      setGroupName(g?.name ?? '')
      return api.knowledgeSummary(g?.id ?? null).then(setK)
    }).catch(() => setK(null))
  }, [])
  const layer = (name: string) => k?.layers.find((l) => l.layer === name)
  return (
    <main className="landing">
      <div className="brandmark"><span className="logo-mark">B</span>브랜드 스튜디오</div>
      <h1>어디에서 시작할까요</h1>
      <p className="muted">지식이 쌓인 그룹으로 들어가거나, 지식 없이 새 브랜드로 시작합니다</p>
      <div className="landing-cards">
        <a className="landing-card entry-knowledge" href="#/group">
          <strong>{groupName || '아모레퍼시픽'} 입장</strong>
          <span>{k?.brands.map((b) => b.name).join(' · ') || '설화수 · 라네즈 · 이니스프리 · 에스트라'}. 규제·그룹·브랜드·제품 지식이 등록되어 있고, 카피와 검수가 그 지식을 근거로 움직입니다.</span>
          <div className="entry-chips">
            {['규제', '그룹', '브랜드', '제품'].map((n) => <em key={n}>{n} {layer(n)?.docs ?? '…'}</em>)}
          </div>
          <b>브랜드 고르고 시작 →</b>
        </a>
        <a className="landing-card entry-other" href="#/studio">
          <strong>타 브랜드 생성</strong>
          <span>브랜드를 직접 등록하고 시안을 만듭니다. 지식 검색 없이 생성하므로 {groupName || '아모레퍼시픽'} 쪽과 같은 브리프로 결과를 비교하는 데 씁니다.</span>
          <div className="entry-chips"><em>RAG 없음</em><em>테스트용</em><em>기존 스튜디오 화면</em></div>
          <b>브랜드 등록으로 →</b>
        </a>
      </div>
      <p className="muted">왼쪽은 지식이 있는 쪽, 오른쪽은 없는 쪽. 같은 브리프를 양쪽에 넣으면 검색이 카피와 검수를 어떻게 바꾸는지 비교할 수 있습니다.</p>
    </main>
  )
}
