import { useCallback, useEffect, useState } from 'react'
import { api, groupNames } from './api'
import BrandPage from './BrandPage'
import PresetLibrary from './PresetLibrary'
import DesignPage from './DesignPage'
import AnalysisPage from './AnalysisPage'
import SmallBizPage from './SmallBizPage'
import EvolutionPage from './EvolutionPage'
import Landing from './Landing'
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
  const [tab, setTab] = useState<'brand' | 'preset' | 'design' | 'analysis'>('brand')
  // 시안 작업 브랜드 (새로고침해도 유지)
  const [workBrandId, setWorkBrandIdState] = useState<number | null>(() => {
    try { const v = localStorage.getItem('workBrandId'); return v ? Number(v) : null } catch { return null }
  })
  const setWorkBrandId = useCallback((id: number | null) => {
    setWorkBrandIdState(id)
    try { id === null ? localStorage.removeItem('workBrandId') : localStorage.setItem('workBrandId', String(id)) } catch { /* 저장 실패는 무시 */ }
  }, [])
  const [groupName, setGroupName] = useState('')
  useEffect(() => {
    if (group) api.brands().then((b) => setGroupName(groupNames(b).join('·'))).catch(() => setGroupName(''))
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
        </nav>
      </header>
      {tab === 'brand'
        ? <BrandPage key={scope} scope={scope} onGoDesign={(id) => { setWorkBrandId(id); setTab('design') }} />
        : tab === 'preset' ? <PresetLibrary />
        : tab === 'analysis' ? <AnalysisPage key={scope} scope={scope} workBrandId={workBrandId} />
        : <DesignPage key={scope} scope={scope} workBrandId={workBrandId} onWorkBrand={setWorkBrandId} onGoBrand={() => setTab('brand')} />}
      <footer className="site-footer"><strong>브랜드 스튜디오</strong><span>브랜드 규격 기반 광고 시안 도구</span></footer>
    </>
  )
}
