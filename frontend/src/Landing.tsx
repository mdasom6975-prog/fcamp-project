// 메인 선택 화면: 브랜드 정보가 있는 광고(기존 앱)와 소상공인용 간편 만들기 중에서 고른다.
export default function Landing() {
  return (
    <main className="landing">
      <div className="brandmark"><span className="logo-mark">B</span>브랜드 스튜디오</div>
      <h1>어떤 광고를 만들까요?</h1>
      <div className="landing-cards">
        <a className="landing-card" href="#/studio">
          <strong>브랜드 정보가 있는 광고를 만들어요</strong>
          <span>브랜드 규칙(로고·색·폰트·금지 표현)을 등록하고, 규격별 시안을 검수해서 내보냅니다.</span>
        </a>
        <a className="landing-card" href="#/smallbiz">
          <strong>소상공인을 위해 쉽게 만들어요</strong>
          <span>홍보할 품목과 사진만 입력하고 스타일을 고르면 광고 이미지가 만들어집니다.</span>
        </a>
      </div>
    </main>
  )
}
