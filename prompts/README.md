# prompts/ — 모델에 보내는 문장은 여기에만 둔다

코드(`backend/app/*.py`)에는 프롬프트 문장을 쓰지 않는다. 문장은 이 폴더의 `.md` 파일에 두고,
`backend/app/prompts.py` 의 `render("경로", 변수=값, ...)` 로 읽어 쓴다. (2일차 "고정 규칙은 System, 이번 입력은 User" · "프롬프트도 git 으로 버전 관리")

## 파일 구조
| 경로 | 용도 | 쓰는 곳 |
|---|---|---|
| `copy/system.md` · `copy/user.md` | 카피 생성 System(고정) / User(브랜드·브리프·지면 제한) | `copywriter._prompt` |
| `icons/propose3d_system.md` · `propose3d_user.md` | **3D(기본)** 기능명 10개 + 렌더할 오브젝트 묘사 | `icons.propose3d` |
| `icons/more3d_system.md` · `more3d_user.md` | 3D 부족한 기능 추가 제안 | `icons.propose3d_more` |
| `icons/render3d.md` | 3D 아이콘 1개 이미지 프롬프트(투명 PNG). 다시 그리기도 같은 문장 | `icons.render_prompt` |
| `icons/_3d_style.md` | 3D 공통 스타일 블록 (partial, render3d 가 include) — 카메라·광원·모서리·색·재질 | — |
| `icons/_3d_output_spec.md` | 3D 제안 출력 JSON 규격 (partial, propose3d·more3d System 이 include) | — |
| `icons/propose_system.md` · `propose_user.md` | (svg 방식, `ICON_RENDER=svg`) 기능명 10개 제안 + 도형 | `icons.propose_and_draw` |
| `icons/redraw_system.md` · `redraw_user.md` | (svg) 아이콘 하나 다시 그리기 | `icons.redraw_one` |
| `icons/more_system.md` · `more_user.md` | (svg) 부족한 기능 추가 제안 | `icons.propose_more` |
| `icons/_output_spec.md` | (svg) 아이콘 출력 JSON 규격 (partial, 세 System 이 include) | — |
| `image/background.md` | 배경 이미지 생성 (표현 방식 `{{style}}` + 구도 `{{composition}}`) | `imagegen.build_prompt` |
| `image/composition/{가로형,정사각,세로형}.md` | 비율군(템플릿)별 구도. 배경은 비율군마다 1장 생성 | `imagegen.build_prompt` |
| `image/style/{실사인물,실사제품,플랫일러스트,3D렌더,그래픽패턴}.md` | 배경 형태(표현 방식). 브리프 `image_style` 또는 자동 선택(`imagegen.pick_style`)으로 파일을 고른다 | `imagegen.build_prompt` |
| `image/product_ref.md` | 제품 누끼 이미지를 참조 이미지로 첨부할 때 background 프롬프트 앞에 붙는 문장 (`{{product_name}}`, `{{base}}`) | `imagegen.build_prompt(product=True)` |
| `image/same_scene.md` | 비율군 변형 배경. 첫 비율군 배경을 참조 이미지로 붙여 같은 장면을 다른 비율 구도로 (`{{base}}` 는 그 비율군의 background 프롬프트) | `imagegen.build_variant_prompt` |
| `image/_common.md` | 모든 템플릿 공통 구도 규칙 (partial) | — |
| `legal/_copy_common.md` | 모든 카피에 붙는 공통 법적 준수 규칙(표시·광고의 공정화에 관한 법률 기준: 거짓·과장·기만·부당 비교·추천 보증·건전성). `copy/system.md` 가 include | `copywriter._prompt` |
| `legal/_image_common.md` | 모든 배경에 붙는 공통 법적 준수 규칙(전후 비교·전문가 가운·보증 장식·실존 인물·상표 금지). `image/background.md` 가 include | `imagegen.build_prompt` |
| `legal/phrases.json` | 생성 후 검사용 표현 사전(차단/경고, 사유, 근거 법령, 적용 업종). 프롬프트가 아니라 코드 검사 데이터 | `legal.scan` |
| `legal/industry/{식품,건강기능식품,화장품,의료,금융,주류}.md` | 품목 글에서 업종 키워드(`legal.INDUSTRY_KEYWORDS`)가 감지되면 카피 User 끝에 붙는 업종별 주의 규칙. 메타에 `law`(근거 법령)·`status`(검토 상태)를 적는다 | `legal.industry_block` |
| `analysis/judge_system.md` · `judge_user.md` | 분석 화면 비전 채점: 생성 배경 1장을 Claude 가 보고 톤·내용·표현 방식·금지 요소·글자 항목을 1~5점 (참고값, 검수 아님) | `analysis.judge_background` |

`_` 로 시작하는 파일은 partial(다른 파일이 include 하는 조각)이다.

## 파일 형식
```
---
version: 1.0            ← 문장을 바꾸면 올린다
description: 한 줄 설명
join: space             ← (선택) 본문 줄을 공백 한 칸으로 이어 한 줄로 만든다. 없으면 줄바꿈 유지
---
본문. {{변수}} 와 {{include 경로}} 를 쓸 수 있다.
```
- `{{변수}}`: `render()` 에 넘긴 값으로 치환. 값이 없으면 KeyError 로 바로 실패한다 (조용히 빈칸이 되지 않게).
- `{{include icons/_output_spec}}`: 다른 파일을 그 자리에 넣는다. 경로는 `prompts/` 기준, 확장자 없음.
- 분기 문장('없음', '지정 없음', 글자 수 제한 줄 등)은 Python 쪽에서 계산해 변수로 넘긴다. md 에는 조건문이 없다.

## 바꾸는 규칙 (한 번에 하나만)
1. 문장을 바꾸면 그 파일의 `version` 을 올린다.
2. `tests/test_prompts_snapshot.py` 가 실패한다 — 바뀐 내용이 의도한 것인지 diff 로 확인한 뒤
   `UPDATE_PROMPT_SNAPSHOTS=1 backend/.venv/bin/python -m pytest tests/test_prompts_snapshot.py -q` 로 스냅샷을 갱신하고 함께 커밋한다.
3. 구조를 옮기는 작업(파일 분리, 로더 수정)에서는 문장을 바꾸지 않는다. 스냅샷이 그대로 통과해야 한다.
4. 결과(카피·이미지·아이콘)에는 어떤 프롬프트로 만들었는지 `prompts.ref("copy/system")` → `copy/system@1.0#9bb57af1` 형식으로 남긴다 (3단계 결과 JSON 에서 사용).

## 아직 코드에 남아 있는 것 (2단계 정책 JSON 대상)
- `copywriter.VARIANTS=3`, `MAX_ATTEMPTS=2`, 제목/본문/CTA 필드 연결(`HEADLINE_FIELDS` 등)
- `icons.COUNT=10`, viewBox 24 / live 20, 허용 태그·속성, 3D 동시 렌더 수 `WORKERS_3D=3`
- 3D 아이콘 렌더 방식·모델·품질(환경변수 `ICON_RENDER`, `ICON_IMAGE_MODEL`, `ICON_IMAGE_QUALITY`)
- 이미지 모델·품질·크기·일일 한도(환경변수)
