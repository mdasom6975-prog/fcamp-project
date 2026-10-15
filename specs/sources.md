# 규격 출처 목록 (Day 2 산출물)

`specs/presets.json` 과 같은 시드에서 생성. 확인일 2026-10-14. 생성 스크립트: `tools/export_presets.py`

| 매체 | 지면 | 상태 | 제작 기본값 | 출처 URL | 확인일 |
|---|---|---|---|---|---|
| Meta | Instagram 피드 | 확인됨 | 1440×1800 | https://www.facebook.com/business/ads-guide/update/image/instagram-feed | 2026-10-14 |
| Meta | Instagram 스토리 | 확인됨 | 1440×2560 | https://www.facebook.com/business/ads-guide/update/image/instagram-story | 2026-10-14 |
| Meta | Facebook 피드 | 충돌 | 1440×1800 | https://www.facebook.com/business/ads-guide/update/image/facebook-feed/link-clicks | 2026-10-14 |
| 카카오 | 디스플레이 네이티브 2:1 | 확인됨 | 1200×600 | https://kakaobusiness.gitbook.io/main/ad/moment/performance/displayad/content-guide<br>http://developers.kakao.com/docs/ko/kakaomoment/creatives-display | 2026-10-14 |
| 카카오 | 디스플레이 네이티브 1:1 | 확인됨 | 500×500 | https://kakaobusiness.gitbook.io/main/ad/moment/performance/displayad/content-guide<br>http://developers.kakao.com/docs/ko/kakaomoment/creatives-display | 2026-10-14 |
| 카카오 | 비즈보드 배너 1029×258 | 확인됨 | 1029×258 | https://kakaobusiness.gitbook.io/main/ad/moment/performance/talkboard/content-guide<br>http://developers.kakao.com/docs/ko/kakaomoment/creatives-display | 2026-10-14 |
| 카카오 | 채널 메시지 와이드이미지형 800×600 | 확인됨 | 800×600 | https://kakaobusiness.gitbook.io/main/ad/moment/messagead/channelmessage/content-guide | 2026-10-14 |
| Google Ads | PMax 가로 1.91:1 | 확인됨 | 1200×628 | https://support.google.com/google-ads/answer/17091269<br>https://support.google.com/google-ads/answer/13676244 | 2026-10-14 |
| Google Ads | PMax 정사각형 1:1 | 확인됨 | 1200×1200 | https://support.google.com/google-ads/answer/17091269<br>https://support.google.com/google-ads/answer/13676244 | 2026-10-14 |
| Google Ads | 업로드형 300×250 | 확인됨 | 300×250 | https://support.google.com/google-ads/answer/1722096 | 2026-10-14 |
| Google Ads | 업로드형 336×280 | 확인됨 | 336×280 | https://support.google.com/google-ads/answer/1722096 | 2026-10-14 |
| Google Ads | 업로드형 320×100 | 확인됨 | 320×100 | https://support.google.com/google-ads/answer/1722096 | 2026-10-14 |
| 네이버 | 지면 미확인 | 확인 필요 | — | (없음) | — |

## 조건별 확인 상태

| 매체 | 지면 | 조건 | 값 | 구분 | 상태 | 비고 |
|---|---|---|---|---|---|---|
| Meta | Instagram 피드 | 파일 형식 | JPG, PNG | 강제 | 확인됨 |  |
| Meta | Instagram 피드 | 최대 용량 | 30MB | 강제 | 확인됨 |  |
| Meta | Instagram 피드 | 최소 너비 | 500px | 강제 | 확인됨 |  |
| Meta | Instagram 피드 | 비율 허용 오차 | 1% | 강제 | 확인됨 |  |
| Meta | Instagram 피드 | 최소/최대 화면 비율 | 400x500 / 191x100 | 강제 | 확인 필요 | 공식 표기가 모호함. Ads Manager 미리보기로 검증 필요 |
| Meta | Instagram 피드 | 권장 해상도 | 1440x1800 | 권장 | 확인됨 |  |
| Meta | Instagram 스토리 | 파일 형식 | JPG, PNG | 강제 | 확인됨 |  |
| Meta | Instagram 스토리 | 최대 용량 | 30MB | 강제 | 확인됨 |  |
| Meta | Instagram 스토리 | 최소 너비 | 500px | 강제 | 확인됨 |  |
| Meta | Instagram 스토리 | 비율 허용 오차 | 1% | 강제 | 확인됨 |  |
| Meta | Instagram 스토리 | 권장 해상도 | 1440x2560 | 권장 | 확인됨 |  |
| Meta | Facebook 피드 | 파일 형식 | JPG, PNG | 강제 | 확인됨 |  |
| Meta | Facebook 피드 | 최대 용량 | 30MB | 강제 | 확인됨 |  |
| Meta | Facebook 피드 | 최소 너비 | 600px | 강제 | 확인됨 |  |
| Meta | Facebook 피드 | 최소 높이 | 750px (4:5 기준) | 강제 | 확인됨 |  |
| Meta | Facebook 피드 | 비율 허용 오차 | 3% | 강제 | 확인됨 |  |
| Meta | Facebook 피드 | 권장 해상도 | 1440x1800 | 권장 | 확인됨 |  |
| Meta | Facebook 피드 | 허용 비율 범위 | 4:5 (본문 기준) | 권장 | 충돌 | 검색 요약은 1.91:1~4:5 범위로 표기. 본문에는 4:5만 있음. 범위 미확정 |
| 카카오 | 디스플레이 네이티브 2:1 | 최소 크기 | 1200x600 | 미표기 | 확인됨 | 필수 여부 구분이 표에 없음 |
| 카카오 | 디스플레이 네이티브 2:1 | 파일 형식 | JPG, JPEG, PNG | 미표기 | 확인됨 |  |
| 카카오 | 디스플레이 네이티브 2:1 | 최대 용량 | 500KB | 미표기 | 확인됨 |  |
| 카카오 | 디스플레이 네이티브 2:1 | 이미지 내 텍스트 크기 | 48~164px | 권장 | 확인됨 |  |
| 카카오 | 디스플레이 네이티브 2:1 | 이미지 내 텍스트 줄 수 | 최대 3줄 | 권장 | 확인됨 |  |
| 카카오 | 디스플레이 네이티브 2:1 | 폰트 스타일·컬러 | 4가지 이상 사용 불가 | 강제 | 확인됨 |  |
| 카카오 | 디스플레이 네이티브 2:1 | 프로필 이미지 | 300x300 이상 1:1, 500KB | 강제 | 확인됨 |  |
| 카카오 | 디스플레이 네이티브 1:1 | 최소 크기 | 500x500 | 미표기 | 확인됨 |  |
| 카카오 | 디스플레이 네이티브 1:1 | 파일 형식 | JPG, JPEG, PNG | 미표기 | 확인됨 |  |
| 카카오 | 디스플레이 네이티브 1:1 | 최대 용량 | 500KB | 미표기 | 확인됨 |  |
| 카카오 | 디스플레이 네이티브 1:1 | 폰트 스타일·컬러 | 4가지 이상 사용 불가 | 강제 | 확인됨 |  |
| 카카오 | 비즈보드 배너 1029×258 | 크기 | 1029x258 | 강제 | 확인됨 |  |
| 카카오 | 비즈보드 배너 1029×258 | 파일 형식 | PNG-24 또는 PNG-32 | 강제 | 확인됨 |  |
| 카카오 | 비즈보드 배너 1029×258 | 최대 용량 | 300KB | 강제 | 확인됨 |  |
| 카카오 | 비즈보드 배너 1029×258 | 배경 | 투명 배경으로 제작 | 강제 | 확인됨 |  |
| 카카오 | 비즈보드 배너 1029×258 | 광고주체 표기 | 필수 | 강제 | 확인됨 |  |
| 카카오 | 비즈보드 배너 1029×258 | AD 마크 영역 | 우측 상단 66x174px 에 오브젝트 겹침 불가 | 강제 | 확인됨 |  |
| 카카오 | 채널 메시지 와이드이미지형 800×600 | 권장 크기 | 800x600 | 권장 | 확인됨 |  |
| 카카오 | 채널 메시지 와이드이미지형 800×600 | 파일 형식 | JPG, JPEG, PNG | 미표기 | 확인됨 |  |
| 카카오 | 채널 메시지 와이드이미지형 800×600 | 최대 용량 | 10MB | 미표기 | 확인됨 |  |
| Google Ads | PMax 가로 1.91:1 | 최소 크기 | 600x314 | 강제 | 확인됨 |  |
| Google Ads | PMax 가로 1.91:1 | 최대 용량 | 5MB | 강제 | 확인됨 |  |
| Google Ads | PMax 가로 1.91:1 | 파일 형식 | JPG, PNG | 강제 | 확인됨 |  |
| Google Ads | PMax 가로 1.91:1 | 권장 크기 | 1200x628 | 권장 | 확인됨 |  |
| Google Ads | PMax 가로 1.91:1 | 수량 | 1~20장 (권장 4장 이상) | 강제 | 확인됨 |  |
| Google Ads | PMax 가로 1.91:1 | 정사각 로고 | 1:1, 권장 1200x1200, 최소 128x128 (필수 자산) | 강제 | 확인됨 |  |
| Google Ads | PMax 가로 1.91:1 | 문구 수량 | 광고 제목 3~15개, 긴 광고 제목 1~5개, 설명 2~5개 | 강제 | 확인됨 |  |
| Google Ads | PMax 정사각형 1:1 | 최소 크기 | 300x300 | 강제 | 확인됨 |  |
| Google Ads | PMax 정사각형 1:1 | 최대 용량 | 5MB | 강제 | 확인됨 |  |
| Google Ads | PMax 정사각형 1:1 | 파일 형식 | JPG, PNG | 강제 | 확인됨 |  |
| Google Ads | PMax 정사각형 1:1 | 권장 크기 | 1200x1200 | 권장 | 확인됨 |  |
| Google Ads | PMax 정사각형 1:1 | 수량 | 1~20장 (권장 4장 이상) | 강제 | 확인됨 |  |
| Google Ads | PMax 정사각형 1:1 | 정사각 로고 | 1:1, 권장 1200x1200, 최소 128x128 (필수 자산) | 강제 | 확인됨 |  |
| Google Ads | PMax 정사각형 1:1 | 문구 수량 | 광고 제목 3~15개, 긴 광고 제목 1~5개, 설명 2~5개 | 강제 | 확인됨 |  |
| Google Ads | 업로드형 300×250 | 크기 | 300x250 | 미표기 | 확인됨 | 지원 크기 목록 중 하나. 필수/권장 구분이 문서에 없음 |
| Google Ads | 업로드형 300×250 | 파일 형식 | GIF, JPG, PNG | 미표기 | 확인됨 |  |
| Google Ads | 업로드형 300×250 | 최대 용량 | 150KB | 미표기 | 확인됨 | 이미지 광고 기준 |
| Google Ads | 업로드형 336×280 | 크기 | 336x280 | 미표기 | 확인됨 | 지원 크기 목록 중 하나. 필수/권장 구분이 문서에 없음 |
| Google Ads | 업로드형 336×280 | 파일 형식 | GIF, JPG, PNG | 미표기 | 확인됨 |  |
| Google Ads | 업로드형 336×280 | 최대 용량 | 150KB | 미표기 | 확인됨 | 이미지 광고 기준 |
| Google Ads | 업로드형 320×100 | 크기 | 320x100 | 미표기 | 확인됨 | 지원 크기 목록 중 하나. 필수/권장 구분이 문서에 없음 |
| Google Ads | 업로드형 320×100 | 파일 형식 | GIF, JPG, PNG | 미표기 | 확인됨 |  |
| Google Ads | 업로드형 320×100 | 최대 용량 | 150KB | 미표기 | 확인됨 | 이미지 광고 기준 |

## 찾지 못해 '확인 필요'로 둔 값

- Meta · Instagram 피드 · 최소/최대 화면 비율: [확인 필요] 공식 표기가 모호함. Ads Manager 미리보기로 검증 필요
- Meta · Instagram 피드: 안전영역: 확인 불가
- Meta · Facebook 피드 · 허용 비율 범위: [충돌] 검색 요약은 1.91:1~4:5 범위로 표기. 본문에는 4:5만 있음. 범위 미확정
- Meta · Facebook 피드: 안전영역: 확인 불가
- Google Ads · PMax 가로 1.91:1: 한국어 등 전각 문자는 글자당 2자로 계산 (PMax 문서). 안전영역: 확인 불가
- Google Ads · PMax 정사각형 1:1: 한국어 등 전각 문자는 글자당 2자로 계산 (PMax 문서). 안전영역: 확인 불가
- Google Ads · 업로드형 300×250: 안전영역: 확인 불가. 애니메이션 GIF 규칙(30초 이하, 5FPS 이하)은 정적 이미지에 해당 없음
- Google Ads · 업로드형 336×280: 안전영역: 확인 불가
- Google Ads · 업로드형 320×100: 안전영역: 확인 불가
- 네이버 · 지면 미확인: 공식 규격 확인 불가(도구에서 공식 도메인 차단). 검수 불가 대상. 비공식 수치는 사용하지 않음
