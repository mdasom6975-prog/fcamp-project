import { useState } from 'react'
import BrandPage from './BrandPage'
import PresetLibrary from './PresetLibrary'
import DesignPage from './DesignPage'
import './App.css'

export default function App() {
  const [tab, setTab] = useState<'brand' | 'preset' | 'design'>('brand')
  return (
    <>
      <header className="topbar">
        <div className="brandmark"><span className="logo-mark">B</span>브랜드 콘텐츠 스튜디오</div>
        <nav className="tabs">
          <button className={tab === 'brand' ? 'on' : ''} onClick={() => setTab('brand')}>브랜드</button>
          <button className={tab === 'design' ? 'on' : ''} onClick={() => setTab('design')}>시안</button>
          <button className={tab === 'preset' ? 'on' : ''} onClick={() => setTab('preset')}>규격 라이브러리</button>
        </nav>
      </header>
      {tab === 'brand' ? <BrandPage /> : tab === 'preset' ? <PresetLibrary /> : <DesignPage />}
    </>
  )
}
