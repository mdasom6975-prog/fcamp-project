---
version: 1.4
description: 카피 생성 User — 브랜드 규칙·브리프·지면 글자 수 제한 (매 요청마다 바뀌는 부분). 1.1 — 자동 재생성을 없애는 대신 제한의 90% 이내 여유분 지시. 1.2 — {{assets}}: PMax 처럼 문구를 여러 개 요구하는 지면의 추가 문구 자산 요구(없으면 빈 문자열). 1.3 — {{legal}}: 품목에서 감지한 규제 업종(식품·화장품·의료·금융 등)의 법적 주의 문단(없으면 빈 문자열). 1.4 — {{functional}} · {{law}} · {{guide}}: 입력한 기능성, 감지 업종의 법령 원문, 브랜드 지식 검색 결과(RAG, 없으면 빈 문자열)
---
[브랜드] {{brand_name}}
- 어조: {{voice}}
- 헤드라인 권장 길이: {{headline_len}}
- 존댓말: {{polite}}
- 반드시 포함할 표현(headline, body, cta 중 어디든): {{required}}
- 절대 쓰지 말 표현: {{forbidden}}

[상품·메시지]
- 상품/서비스명: {{product_name}}
- 설명: {{description}}
- 핵심 메시지: {{key_message}}
- 타깃 고객: {{target}}

[지면] {{platform}} · {{placement}}
글자 수 제한:
{{limit_headline}}
{{limit_body}}
{{limit_cta}}
제한을 넘지 않도록 글자 수를 직접 세어 확인한 뒤 출력한다. 세는 방식의 차이를 감안해 각 필드는 최대 제한의 90% 이내로 쓴다(예: 최대 45자면 40자 이내, 최대 30자면 27자 이내). 최소 제한이 있으면 그보다 길게 쓴다.{{assets}}{{legal}}{{functional}}{{law}}{{guide}}
