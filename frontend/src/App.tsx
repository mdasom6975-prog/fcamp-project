import { useCallback, useEffect, useState } from 'react'
import { api, groupNames } from './api'
import BrandPage from './BrandPage'
import PresetLibrary from './PresetLibrary'
import DesignPage from './DesignPage'
import AnalysisPage from './AnalysisPage'
import SmallBizPage from './SmallBizPage'
import EvolutionPage from './EvolutionPage'
import Landing from './Landing'
import KnowledgePage from './KnowledgePage'
import BusyOverlay from './Busy'
import './App.css'

export default function App() {
  // 주소 해시로 화면을 나눈다: 없음·#/ = 메인(선택), #/studio = 단독 브랜드 스튜디오, #/group = 그룹 브랜드 스튜디오, #/smallbiz = 소상공인, #/evolution = RAG 진화 시연
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const on = () => setHash(window.location.hash)
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  const smallBiz = hash === '#/smallbiz'
  const studio = hash === '#/studio'
  const group = hash === '#/group'
  const scope = group ? 'group' : 'solo'
  const evolution = hash === '#/evolution'
  // 두 입구 모두 1단계 브랜드 탭이 첫 화면(00 정의서 v2.3: 지식은 단계 안에서 근거로 보이지, 단계 앞을 막는 화면이 아니다). 지식 브라우저는 그룹 입구의 "지식" 탭
  const [tab, setTab] = useState<'knowledge' | 'brand' | 'preset' | 'design' | 'analysis'>('brand')
  useEffect(() => { setTab('brand') }, [hash])
  // 시안 작업 브랜드 (새로고침해도 유지)
  const [workBrandId, setWorkBrandIdState] = useState<number | null>(() => {
    try { const v = localStorage.getItem('workBrandId'); return v ? Number(v) : null } catch { return null }
  })
  const setWorkBrandId = useCallback((id: number | null) => {
    setWorkBrandIdState(id)
    try { id === null ? localStorage.removeItem('workBrandId') : localStorage.setItem('workBrandId', String(id)) } catch { /* 저장 실패는 무시 */ }
  }, [])
  const [groupName, setGroupName] = useState('')
  const [openDesign, setOpenDesign] = useState<number | null>(null)   // "제품에서 시작"이 만든 캠페인을 열어 둘 id
  const [autoImages, setAutoImages] = useState(false)                  // 열자마자 ④ 이미지 생성을 이어서 돌릴지
  const [groupId, setGroupId] = useState<number | null>(null)
  useEffect(() => {
    if (group) {
      api.brands().then((b) => setGroupName(groupNames(b).join('·'))).catch(() => setGroupName(''))
      api.brandGroups().then((gs) => setGroupId(gs[0]?.id ?? null)).catch(() => setGroupId(null))
    }
  }, [group])
  if (evolution) return <><header className="topbar"><a className="brandmark" href="#/"><span className="logo-mark">B</span>RAG 진화 시연</a></header><EvolutionPage /></>
  if (!smallBiz && !studio && !group) return <Landing />
  if (smallBiz) return <><BusyOverlay /><header className="topbar"><a className="brandmark" href="#/"><span className="logo-mark">B</span>소상공인 광고 이미지</a></header><SmallBizPage /></>
  return (
    <>
      <BusyOverlay />
      <header className="topbar">
        <a className="brandmark" href="#/"><span className="logo-mark">B</span>{group ? `${groupName || '그룹'} 스튜디오` : '브랜드 스튜디오'}</a>
        <nav className="tabs">
          <button className={tab === 'brand' ? 'on' : ''} onClick={() => setTab('brand')}>브랜드</button>
          <button className={tab === 'design' ? 'on' : ''} onClick={() => setTab('design')}>규격·메시지·시안</button>
          <button className={tab === 'preset' ? 'on' : ''} onClick={() => setTab('preset')}>규격 라이브러리</button>
          <button className={tab === 'analysis' ? 'on' : ''} onClick={() => setTab('analysis')} title="학습·개발용: 입력↔결과 대조, 보낸 프롬프트, 비용">분석</button>
          {group && <button className={tab === 'knowledge' ? 'on' : ''} onClick={() => setTab('knowledge')} title="참고·관리: 등록된 규제·그룹·브랜드·제품 지식과 그 지식이 쓰인 카피">지식</button>}
        </nav>
      </header>
      {tab === 'knowledge' && group
        ? (groupId === null ? <p className="muted" style={{ padding: 24 }}>지식을 불러오는 중…</p>
           : <KnowledgePage groupId={groupId} />)
        : tab === 'brand' || tab === 'knowledge'
        ? <BrandPage key={scope} scope={scope} onGoDesign={(id) => { setWorkBrandId(id); setTab('design') }}
            onStartDesign={(did, bid, withImages) => { setWorkBrandId(bid); setOpenDesign(did); setAutoImages(withImages); setTab('design') }} />
        : tab === 'preset' ? <PresetLibrary />
        : tab === 'analysis' ? <AnalysisPage key={scope} scope={scope} workBrandId={workBrandId} />
        : <DesignPage key={scope} scope={scope} workBrandId={workBrandId} onWorkBrand={setWorkBrandId} onGoBrand={() => setTab('brand')} openDesignId={openDesign} onOpened={() => setOpenDesign(null)} autoImages={autoImages} onAutoDone={() => setAutoImages(false)} />}
      <footer className="site-footer"><strong>브랜드 스튜디오</strong><span>브랜드 규격 기반 광고 시안 도구</span></footer>
    </>
  )
}
