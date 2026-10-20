# 시연·QA 더미 데이터

실제 브랜드가 아닌 가상 데이터. 브랜드 3개와 캠페인 브리프 4개를 앱에 한 번에 넣어 시연·QA 를 돌려 볼 수 있다.
(한때 카테고리별 브랜드 28개·캠페인 74개를 두었으나, 유지 부담을 줄이려 3개 브랜드만 남겼다. 다른 카테고리가 필요하면 같은 형식으로 폴더와 캠페인 JSON 을 추가하면 된다.)

- 브랜드: `brands/<폴더>/brand.json`(저장 형식) · `logo.png`(1200×360 워드마크) · `logo_square.png`(512×512, PMax 등 1:1 로고) · 일부 `product.png`(제품 누끼)
- 캠페인: `demo/campaigns/<slug>.json` — 규격 이름은 `specs/presets.json` 의 placement 와 같아야 한다
- 넣기: 앱(백엔드)을 띄운 뒤 `backend/.venv/bin/python tools/seed_demo.py` (옵션은 `--help`)
- 검증: `tests/test_demo_data.py` (스키마·규격 이름·로고 한도·주조색 중복·임시 DB 시딩과 재실행 안전성)

## 시나리오별로 보기
| 보려는 것 | 캠페인 |
|---|---|
| 기본 흐름, 제품 이미지 첨부 | `greentable_morning` (product.png 첨부, 배경 형태 '자동') |
| 캠페인 표현이 브랜드 값을 대체 | `greentable_fruit` (필수 '정기배송', 금지 '당도 보장' 추가), `learnmate_typo` (필수 '러닝메이트'·'첫 달') |
| 캠페인 표현을 비우면 브랜드 값 사용 | `greentable_morning`, `learnmate_15min` |
| 배경 형태별 | `greentable_fruit`(실사 제품·공간), `learnmate_15min`(플랫 일러스트), `learnmate_typo`(그래픽 패턴) |
| 분리 자산형·업로드형 규격(PMax·비즈보드·300×250) | `learnmate_15min`(PMax 가로·정사각 + 업로드형 300×250 — 이미지 안 본문·CTA 기본 켬), `learnmate_typo`(비즈보드), `greentable_morning`(PMax 가로) |
| 문구를 여러 개 요구하는 지면(PMax '문구 수량') | `greentable_fruit`, `greentable_morning`, `learnmate_15min` — 카피 생성 때 추가 문구 자산을 함께 받는다 |
| 정사각 로고(PMax) 검수 | PMax 가 든 캠페인 전부 (`logo_square.png` 를 브랜드 정사각 로고로 올린다) |
| 금지 표현 충돌 → 검수 '수정 필요' 차단 | 현재 세트에는 없다. 핵심 메시지에 브랜드 금지 표현(예: 그린테이블 '무조건')을 넣은 캠페인을 추가하면 된다 |

참고: Facebook 피드의 '허용 비율 범위'는 출처별 표기가 달라(충돌) 검수가 모든 표기를 대조한다. 4:5 합성본은 통과하고, 어긋나면 '확인 필요'로 두되 사람이 확인함으로 넘길 수 있다.

## 브랜드
| 폴더 | 브랜드 | 캠페인 수 | 로고 | 필수 / 금지 표현 |
|---|---|---|---|---|
| kucha | 쿠챠 | 0 (시연 시나리오의 기준 브랜드, 앱에서 직접 입력) | 없음 | 쿠챠 / 최저가, 무조건 무료배송, 100% 당첨, 손해 없음, 중복 없음, 시크릿 보장, 희귀 보장, 특정 상품 선택, 원하는 상품을 고르, 카지노, 잭팟, 룰렛, 슬롯머신, 베팅, 코인, 경품, 투자, 수익, 가격 비교, 디지털 아이템, 쿠지 |
| greentable | 그린테이블 | 2 | logo.png · logo_square.png · product.png | 그린테이블 / 최저가, 100% 유기농, 무조건, 평생 |
| learnmate | 러닝메이트 | 2 | logo.png · logo_square.png | 러닝메이트, 15분 / 100% 합격, 보장, 무조건, 평생 무료 |
