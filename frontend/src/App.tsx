import { useCallback, useState } from 'react'
import BrandPage from './BrandPage'
import PresetLibrary from './PresetLibrary'
import DesignPage from './DesignPage'
import AnalysisPage from './AnalysisPage'
import BusyOverlay from './Busy'
import './App.css'

export default function App() {
  const [tab, setTab] = useState<'brand' | 'preset' | 'design' | 'analysis'>('brand')
  // 시안 작업 브랜드 (새로고침해도 유지)
  const [workBrandId, setWorkBrandIdState] = useState<number | null>(() => {
    try { const v = localStorage.getItem('workBrandId'); return v ? Number(v) : null } catch { return null }
  })
  const setWorkBrandId = useCallback((id: number | null) => {
    setWorkBrandIdState(id)
    try { id === null ? localStorage.removeItem('workBrandId') : localStorage.setItem('workBrandId', String(id)) } catch { /* 저장 실패는 무시 */ }
  }, [])
  return (
    <>
      <BusyOverlay />
      <header className="topbar">
        <div className="brandmark"><span className="logo-mark">B</span>브랜드 스튜디오</div>
        <nav className="tabs">
          <button className={tab === 'brand' ? 'on' : ''} onClick={() => setTab('brand')}>브랜드</button>
          <button className={tab === 'design' ? 'on' : ''} onClick={() => setTab('design')}>규격·메시지·시안</button>
          <button className={tab === 'preset' ? 'on' : ''} onClick={() => setTab('preset')}>규격 라이브러리</button>
          <button className={tab === 'analysis' ? 'on' : ''} onClick={() => setTab('analysis')} title="학습·개발용: 입력↔결과 대조, 보낸 프롬프트, 비용">분석</button>
        </nav>
      </header>
      {tab === 'brand'
        ? <BrandPage onGoDesign={(id) => { setWorkBrandId(id); setTab('design') }} />
        : tab === 'preset' ? <PresetLibrary />
        : tab === 'analysis' ? <AnalysisPage workBrandId={workBrandId} />
        : <DesignPage workBrandId={workBrandId} onWorkBrand={setWorkBrandId} onGoBrand={() => setTab('brand')} />}
    </>
  )
}
