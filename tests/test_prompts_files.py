"""prompts/*.md 파일 자체의 형식 검사와 로더(app.prompts) 동작 검사. 문장 내용은 test_prompts_snapshot.py 가 고정한다."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROMPT_FILES = sorted(p for p in (ROOT / "prompts").rglob("*.md") if p.name != "README.md")


@pytest.mark.parametrize("path", PROMPT_FILES, ids=lambda p: str(p.relative_to(ROOT / "prompts")))
def test_every_prompt_has_version_and_description(path):
    from app import prompts
    p = prompts.load(str(path.relative_to(ROOT / "prompts").with_suffix("")))
    assert p.meta.get("version"), "version 이 있어야 결과에 어떤 프롬프트였는지 남길 수 있다"
    assert p.meta.get("description"), "description 한 줄"
    assert p.meta.get("join", "lines") in ("space", "lines")
    assert p.body.strip(), "본문이 비어 있다"


def test_loader_lists_all_files():
    from app import prompts
    assert prompts.list_names() == sorted(str(p.relative_to(ROOT / "prompts").with_suffix("")) for p in PROMPT_FILES)


def test_missing_variable_fails_loudly():
    from app import prompts
    with pytest.raises(KeyError):
        prompts.render("copy/system")          # variants 를 안 넘김


def test_missing_file_fails_loudly():
    from app import prompts
    with pytest.raises(FileNotFoundError):
        prompts.render("copy/없는_파일")


def test_ref_changes_when_file_changes(tmp_path, monkeypatch):
    from app import prompts
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    (tmp_path / "a.md").write_text("---\nversion: 1.0\ndescription: t\n---\n안녕 {{x}}", encoding="utf-8")
    r1 = prompts.ref("a")
    assert r1.startswith("a@1.0#") and prompts.render("a", x="y") == "안녕 y"
    (tmp_path / "a.md").write_text("---\nversion: 1.1\ndescription: t\n---\n안녕 {{x}}!", encoding="utf-8")
    assert prompts.ref("a") != r1 and prompts.ref("a").startswith("a@1.1#")


def test_include_and_join_space(tmp_path, monkeypatch):
    from app import prompts
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    (tmp_path / "_p.md").write_text("---\nversion: 1\ndescription: p\njoin: space\n---\n둘\n셋", encoding="utf-8")
    (tmp_path / "m.md").write_text("---\nversion: 1\ndescription: m\njoin: space\n---\n하나\n{{include _p}}\n\n넷 {{n}}", encoding="utf-8")
    assert prompts.render("m", n=4) == "하나 둘 셋 넷 4"
