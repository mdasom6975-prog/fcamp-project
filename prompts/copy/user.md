---
version: 1.0
description: 카피 생성 User — 브랜드 규칙·브리프·지면 글자 수 제한 (매 요청마다 바뀌는 부분)
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
제한을 넘지 않도록 글자 수를 직접 세어 확인한 뒤 출력한다.
