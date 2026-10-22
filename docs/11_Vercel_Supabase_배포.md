# 11. Vercel + Supabase 배포

프론트(정적)와 FastAPI(Python 함수)를 Vercel에, DB는 Supabase Postgres에, 파일은 Supabase Storage에 둔다.
10번 문서의 VM + cloudflared 방식(`deploy/`)은 대안으로 남겨 둔다.

## 1. 구성

```
[브라우저] ── https://<프로젝트>.vercel.app
   ├─ /          정적 파일 (frontend/dist, Vite 빌드)
   └─ /api/*     Vercel Python 함수 (api/index.py → backend/app, FastAPI)
                  ├─ DB       Supabase Postgres  (DATABASE_URL, 풀러 6543)
                  └─ 파일     Supabase Storage   (비공개 버킷 brand-studio)
```

- 함수는 `vercel.json` 의 rewrite 로 `/api/*` 를 모두 받는다. 최대 실행 시간 300초(OpenAI 이미지 호출 타임아웃 240초 안에 끝나게).
- 로컬 개발은 그대로다: `DATABASE_URL`·`SUPABASE_*` 가 없으면 SQLite(`backend/data/app.db`)와 `backend/storage/` 를 쓴다.
- 파일은 DB에 **키**(`bg_ab12.png`, `exports/exp_1_….zip`)로 적는다. 읽을 때 Supabase에서 함수의 `/tmp/storage` 로 내려받아 쓰고,
  이미지·ZIP을 브라우저에 줄 때는 서명 URL로 리다이렉트한다(Vercel 함수 응답은 4.5MB 가 한계).
- 예전에 `backend/data/` 파일로 쌓던 것은 DB 테이블로 옮겼다: 일일 이미지 카운터·모델 지원 여부 → `kv`, 호출 기록 → `api_calls`,
  프롬프트 전문 → `prompt_logs`, 비전 채점 → `judgments`.
- 스키마 생성과 규격 시드는 인스턴스가 첫 요청을 받을 때 한 번 한다(Postgres advisory lock 으로 동시에 뜰 때 겹치지 않게 한다).

## 2. Supabase 준비

1. 새 프로젝트를 만든다(리전은 Vercel 함수 리전과 가까운 곳). DB 비밀번호를 적어 둔다.
2. **DATABASE_URL**: 프로젝트 → Connect → *Transaction pooler* 의 URI(포트 6543). `postgresql://…` 그대로 붙이면 앱이 `postgresql+psycopg://` 로 바꾼다.
   서버리스는 요청마다 연결을 열고 닫으므로 직접 연결(5432) 대신 풀러를 써야 연결 한도를 넘지 않는다.
3. **SUPABASE_URL / SUPABASE_SERVICE_KEY**: Project Settings → API 의 Project URL 과 `service_role` 키.
   이 키는 서버(Vercel 환경변수)에만 두고 프론트·저장소에는 넣지 않는다.
4. Storage → New bucket → 이름 `brand-studio`, **Private**. (기존 데이터를 이전하면 3번 스크립트가 만들어 준다.)

## 3. 기존 데이터 이전 (선택)

로컬에서 만든 브랜드·시안·파일을 가져갈 때만. **앱이 이 DB에 처음 접속하기 전에** 실행한다(앱이 먼저 시드하면 id가 겹쳐 스크립트가 멈춘다).

```bash
export DATABASE_URL='postgresql://…:6543/postgres'
export SUPABASE_URL='https://<ref>.supabase.co'
export SUPABASE_SERVICE_KEY='…'
backend/.venv/bin/pip install 'psycopg[binary]'
backend/.venv/bin/python tools/migrate_to_supabase.py --dry-run     # 옮길 양만 확인
backend/.venv/bin/python tools/migrate_to_supabase.py
```

테이블 행(id 유지), 파일 경로 → 키 변환, `backend/storage` 파일 업로드, 호출 기록·프롬프트·채점 결과 이관, 시퀀스 보정까지 한다.
처음부터 시작하면 건너뛴다(브랜드 등록은 5번).

## 4. Vercel 배포

1. Vercel → Add New Project → 이 저장소를 가져온다. **Root Directory 는 저장소 루트**(`vercel.json` 이 있는 곳). Framework Preset 은 Other.
   install/build 명령과 출력 폴더는 `vercel.json` 에 있다.
2. Settings → Environment Variables 에 `.env.example` 의 값을 넣는다:
   `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_BUCKET`, 모델 API 키 4개,
   `IMAGE_DAILY_LIMIT`, `IMAGE_PROVIDER_DRAFT`, `APP_PASSWORD`, `APP_USER`, `APP_PUBLIC=1`.
3. Deploy. 끝나면 확인:
   ```bash
   curl https://<프로젝트>.vercel.app/api/health     # {"status":"ok","presets":N}
   ```

## 5. 브랜드 등록

프로젝트에는 더미 데이터를 두지 않는다. `brands/` 의 브랜드(현재 쿠챠)를 넣으려면:

```bash
backend/.venv/bin/python tools/seed_demo.py --base https://demo:<APP_PASSWORD>@<프로젝트>.vercel.app
```

캠페인은 화면에서 직접 만든다. 이 스크립트는 `demo/campaigns/*.json` 이 있을 때만 캠페인을 넣는다.

## 6. 알아 둘 제한

| 항목 | 내용 |
|---|---|
| 접근 제한 | `APP_PASSWORD` 는 `/api/*` 에만 걸린다. 정적 화면(HTML·JS)은 공개이고, API 호출 때 브라우저가 로그인 창을 띄운다. 데이터는 보호되지만 화면 껍데기는 누구나 볼 수 있다. |
| 실행 시간 | 함수 최대 300초(플랜별 상한이 다르다). 여러 장을 한 번에 만드는 요청이 이를 넘으면 502가 난다. 그럴 때는 규격을 나눠서 생성한다. |
| 업로드 크기 | Vercel 은 요청 본문을 4.5MB 로 제한한다. 로고 300KB·제품 200KB 는 문제없지만, 4.5MB 가 넘는 폰트는 올라가지 않는다. |
| 콜드 스타트 | 한동안 요청이 없으면 첫 요청에서 스키마 확인·시드(수 초)를 한다. 시연 직전에 한 번 열어 두면 된다. |
| Postgres 엄격함 | SQLite와 달리 컬럼 길이를 넘는 문자열(`brands.description` 300자 등)은 오류가 된다. |
| 무료 플랜 | Supabase 무료 프로젝트는 일주일 동안 접속이 없으면 일시 정지된다(대시보드에서 재개). Vercel Hobby 는 비상업용이다. |
| 로컬 캐시 | 함수의 `/tmp/storage` 는 인스턴스마다 따로이고 사라져도 된다(원본은 Supabase). |

## 7. 코드에서 달라진 점

- `backend/app/db.py`: `DATABASE_URL` 지원(Postgres는 NullPool·prepared statement 끔), `STORAGE_DIR`(Vercel 에서는 `/tmp/storage`).
- `backend/app/storage.py`: 파일 저장소(`put`·`local`·`exists`·`delete`·`response`). 라우터는 파일 시스템을 직접 쓰지 않는다.
- `backend/app/state.py` + `models.py`(`KV`·`ApiCall`·`PromptLog`·`Judgment`): 파일 기록을 DB로.
- `backend/app/main.py`: 초기화를 `init_app()` 으로 묶어 lifespan 과 첫 요청 양쪽에서 한 번만 실행. 옛 `ALTER TABLE` 보정은 SQLite 에서만.
- `api/index.py`, `vercel.json`, `.vercelignore`, `requirements.txt`(Vercel용, 루트), `.env.example`.
- `tools/migrate_to_supabase.py`: 로컬 데이터 이전.
