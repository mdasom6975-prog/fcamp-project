# 브랜드 마케팅 콘텐츠 제작 플랫폼

RFP: 브랜드 아이덴티티를 유지하며 매체·광고 유형·지면 규격에 맞게 콘텐츠를 생성·편집·검수·내보내는 웹앱.
설계 문서: `docs/` (00 프로젝트 정의서 · 01 데이터 구조 · 02 매체·프리셋 · 03 브랜드 등록 · 04 ERD · 05 시연 시나리오 · 06 데이터 모델 점검 · 07 Day1~4 점검표)

## 실행
```bash
# 백엔드 (터미널 1)
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000

# 프론트엔드 (터미널 2)
cd frontend
npm install
npm run dev        # http://localhost:5173
```
첫 실행 시 SQLite(`backend/data/app.db`)가 만들어지고 규격 프리셋이 시드됩니다.

## 기준 데이터와 검증 (워크북 Day 2~4)
- `specs/presets.json` · `specs/sources.md`: 규격 프리셋과 출처(시드에서 생성, `tools/export_presets.py`)
- `brands/kucha/brand.json` · `brands/kucha/QUESTIONS.md`: 쿠챠 브랜드 키트와 가이드 해석 질문. `brands/malkkeum/`: 교차 테스트용 두 번째 브랜드
- `tools/register_brand.py brands/kucha/brand.json`: 실행 중인 앱에 브랜드 등록(같은 이름이면 새 버전)
- `tools/hash_logo.py <로고>`: 로고 해시·비율 기록
- `prompts/*.md`: 모델에 보내는 모든 프롬프트 문장 (코드에는 두지 않음). 형식·변경 규칙은 `prompts/README.md`, 로더는 `backend/app/prompts.py`
- 검증: `REQUIRE_VERIFIED_SPECS=1 backend/.venv/bin/python -m pytest tests -q`
  - `tests/test_prompts_snapshot.py`: 프롬프트 렌더링 결과를 `tests/fixtures/prompts/*.txt` 와 글자 단위로 비교. 문장을 의도적으로 바꿨을 때만 `UPDATE_PROMPT_SNAPSHOTS=1` 로 갱신
- 화면 캡처: `docs/wireframes/`
