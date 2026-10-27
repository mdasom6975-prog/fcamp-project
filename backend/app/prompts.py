"""프롬프트 로더. 프롬프트 문장은 코드가 아니라 저장소 루트의 prompts/*.md 에 둔다.

파일 형식 (prompts/README.md):
- 맨 앞 `---` ... `---` 사이는 메타(version, description, join). 그 아래가 본문.
- `{{이름}}`         : 변수. render() 에 넘긴 값으로 치환한다. 빠진 변수는 KeyError (조용히 빈칸이 되지 않게).
- `{{include 경로}}` : 다른 프롬프트 파일(partial)을 그 자리에 넣는다. 경로는 prompts/ 기준, 확장자 없이.
- `join: space`      : 본문 줄을 공백 한 칸으로 이어 한 줄로 만든다 (긴 System 지시문을 읽기 좋게 나눠 쓰기 위한 것).
                        기본(join 없음)은 줄바꿈을 그대로 둔다.
"""
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

_FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
_INCLUDE = re.compile(r"\{\{\s*include\s+([\w./\-가-힣]+)\s*\}\}")
_VAR = re.compile(r"\{\{\s*([A-Za-z_]\w*)\s*\}\}")


@dataclass(frozen=True)
class Prompt:
    name: str          # 예) copy/system
    meta: dict         # version, description, join ...
    body: str          # include·변수 치환 전 원문
    sha256: str        # 파일 전체 해시 (결과에 어떤 프롬프트였는지 남길 때 사용)

    @property
    def version(self) -> str:
        return str(self.meta.get("version", "0"))

    @property
    def ref(self) -> str:
        """결과 기록용 식별자: 이름@버전#해시8자리"""
        return f"{self.name}@{self.version}#{self.sha256[:8]}"


def path_of(name: str) -> Path:
    return PROMPTS_DIR / f"{name}.md"


def load(name: str) -> Prompt:
    path = path_of(name)
    if not path.exists():
        raise FileNotFoundError(f"프롬프트 파일이 없습니다: {path}")
    raw = path.read_text(encoding="utf-8")
    meta: dict = {}
    body = raw
    m = _FRONT.match(raw)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = raw[m.end():]
    return Prompt(name=name, meta=meta, body=body, sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())


def render(name: str, /, **vars) -> str:
    """프롬프트 파일을 읽어 include → 변수 치환 → join 순서로 문자열을 만든다.
    (name 은 위치 전용 인자 — 템플릿 변수 이름으로 'name' 을 쓸 수 있게)"""
    p = load(name)
    text = _INCLUDE.sub(lambda m: render(m.group(1), **vars), p.body)

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in vars:
            raise KeyError(f"프롬프트 {name} 의 변수 '{key}' 값이 없습니다")
        return str(vars[key])

    text = _VAR.sub(sub, text)
    if p.meta.get("join") == "space":
        return " ".join(line for line in text.splitlines() if line.strip())
    return text.strip("\n")


def ref(name: str) -> str:
    return load(name).ref
