"""문구 길이 계산. 규격마다 계수 방식이 달라 한 곳에서 처리한다."""
import unicodedata

METHODS = ("글자 수", "전각 2자 계산")


def is_fullwidth(ch: str) -> bool:
    """한글·한자·가나 등 전각(East Asian Wide/Fullwidth) 문자."""
    return unicodedata.east_asian_width(ch) in ("W", "F")


def count_len(text: str, method: str | None = "글자 수") -> int:
    if method == "전각 2자 계산":
        return sum(2 if is_fullwidth(c) else 1 for c in text)
    return len(text)
