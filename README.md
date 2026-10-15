# 브랜드 마케팅 콘텐츠 제작 플랫폼

RFP: 브랜드 아이덴티티를 유지하며 매체·광고 유형·지면 규격에 맞게 콘텐츠를 생성·편집·검수·내보내는 웹앱.
설계 문서: `docs/` (01 데이터 구조, 02 매체·프리셋)

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
