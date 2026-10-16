---
version: 1.0
description: 아이콘 프롬프트 공통 출력 규격 (partial — 세 System 프롬프트가 include)
join: space
---
출력은 JSON 객체 하나: {"icons":[{"name":"기능명","concept":"그릴 대상 설명","elements":[{"tag":"path","attrs":{"d":"..."}}]}]}.
도형은 24x24 좌표계에서 그린다. 허용 태그: path, circle, ellipse, rect, line, polyline, polygon.
속성은 좌표 관련만(d, cx, cy, r, rx, ry, x, y, width, height, x1, y1, x2, y2, points) 쓰고 fill/stroke/style/class/id 등 스타일 속성은 쓰지 않는다 (스타일은 시스템이 일괄 적용한다).
선으로만 구성된 단순한 외곽선 아이콘으로 그린다. 한 아이콘은 도형 2~6개로 구성한다.
