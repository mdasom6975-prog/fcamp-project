# 브랜드 마케팅 콘텐츠 제작 플랫폼

RFP: 브랜드 아이덴티티를 유지하며 매체·광고 유형·지면 규격에 맞게 콘텐츠를 생성·편집·검수·내보내는 웹앱.
설계 문서: `docs/` (00 프로젝트 정의서 · 01 데이터 구조 · 02 매체·프리셋 · 03 브랜드 등록 · 04 ERD · 05 시연 시나리오 · 06 데이터 모델 점검 · 07 Day1~4 점검표 · 10 시연 배포)

## 실행
```bash
# 백엔드 (터미널 1)
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
cd backend && .venv/bin/uvicorn app.main:app --port 8000 --reload --reload-dir app

# 프론트엔드 (터미널 2)
cd frontend
npm install
npm run dev        # http://localhost:5173
```
첫 실행 시 SQLite(`backend/data/app.db`)가 만들어지고 규격 프리셋이 시드됩니다. `DATABASE_URL`(Supabase Postgres)·`SUPABASE_URL`·`SUPABASE_SERVICE_KEY` 를 환경변수로 주면 같은 코드가 Postgres·Supabase Storage 를 씁니다.
배포: Vercel(프론트 정적 + FastAPI Python 함수) + Supabase 는 `docs/11_Vercel_Supabase_배포.md`.
대안으로 클라우드 VM 1대에 올려 시연하는 절차(접근 비밀번호·터널 주소·비용)는 `docs/10_시연_배포.md`, 운영 파일은 `deploy/`.

## 기준 데이터와 검증 (워크북 Day 2~4)
- `specs/presets.json` · `specs/sources.md`: 규격 프리셋과 출처(시드에서 생성, `tools/export_presets.py`)
- `brands/kucha/brand.json` · `brands/kucha/QUESTIONS.md`: 쿠챠 브랜드 키트와 가이드 해석 질문. `brands/greentable/`·`brands/learnmate/`: 시연·교차 테스트용 브랜드(더미는 이 3개만 둔다)
- `tools/register_brand.py brands/kucha/brand.json`: 실행 중인 앱에 브랜드 등록(같은 이름이면 새 버전)
- `tools/seed_demo.py`: 시연·QA 용 더미 브랜드 3개 + 캠페인 4개를 실행 중인 앱에 한 번에 등록(과금 없음, 재실행 안전). 데이터 설명·시나리오별 캠페인은 `demo/README.md`, 검증은 `tests/test_demo_data.py`
- `tools/hash_logo.py <로고>`: 로고 해시·비율 기록
- `prompts/*.md`: 모델에 보내는 모든 프롬프트 문장 (코드에는 두지 않음). 형식·변경 규칙은 `prompts/README.md`, 로더는 `backend/app/prompts.py`
- 검증: `REQUIRE_VERIFIED_SPECS=1 backend/.venv/bin/python -m pytest tests -q`
  - `tests/test_prompts_snapshot.py`: 프롬프트 렌더링 결과를 `tests/fixtures/prompts/*.txt` 와 글자 단위로 비교. 문장을 의도적으로 바꿨을 때만 `UPDATE_PROMPT_SNAPSHOTS=1` 로 갱신
- 이미지 생성 비교(실제 과금, DB 의 시안 1개 필요): `backend/scripts/compare_models.py` 모델별 1장 → `backend/storage/compare/index.html` · `backend/scripts/compare_prompts.py` 프롬프트 변형별 1장 → `backend/storage/compare/prompts/index.html`
- 화면 캡처: `docs/wireframes/`
- 분석 탭(학습·개발용, 검수와 별개): 캠페인 하나의 브랜드 입력↔결과 대조(입력이 반영되는 단계 표, 배경·아이콘 주요 색과 브랜드 색의 ΔE, Claude 비전 채점 버튼·과금), 모델에 보낸 프롬프트 전문(호출 기록의 `prompt_logs` 테이블 → DB 저장분 → 재구성), 단계별 비용. `GET /api/designs/{id}/analysis`, `backend/app/analysis.py`, `frontend/src/AnalysisPage.tsx`
