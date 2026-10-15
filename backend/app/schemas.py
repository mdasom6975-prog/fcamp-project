import re

from pydantic import BaseModel, Field, field_validator

LEVELS = ("필수", "권장", "수정가능")
ITEMS = ("logo", "colors", "fonts", "icon_style", "image_tone", "voice",
         "required_phrases", "forbidden_phrases")
# 확정된 구분 기본값 (docs/03)
DEFAULT_LEVELS = {
    "logo": "필수", "colors": "필수", "fonts": "필수",
    "icon_style": "권장", "image_tone": "권장", "voice": "권장",
    "required_phrases": "필수", "forbidden_phrases": "필수",
}
DEFAULT_COLOR_ROLES = ["주조색", "보조색", "강조색", "배경색", "텍스트색"]
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ColorIn(BaseModel):
    role: str = Field(min_length=1, max_length=30)
    hex: str

    @field_validator("hex")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not HEX.match(v):
            raise ValueError("색상 코드는 #RRGGBB 형식이어야 합니다")
        return v.upper()


class FontIn(BaseModel):
    role: str = Field(min_length=1, max_length=30)   # 제목 | 본문 | 사용자 지정
    source: str                                      # provided | upload
    name: str | None = None                          # provided: 예) Pretendard-Bold
    asset_id: int | None = None                      # upload: Asset id

    @field_validator("source")
    @classmethod
    def _source(cls, v: str) -> str:
        if v not in ("provided", "upload"):
            raise ValueError("source 는 provided 또는 upload")
        return v


class IconStyleIn(BaseModel):
    stroke_width: float = Field(ge=0.5, le=12)
    corner: str          # 각진 | 둥근
    fill: str            # 윤곽 | 채움

    @field_validator("corner")
    @classmethod
    def _c(cls, v: str) -> str:
        if v not in ("각진", "둥근"):
            raise ValueError("모서리는 각진 또는 둥근")
        return v

    @field_validator("fill")
    @classmethod
    def _f(cls, v: str) -> str:
        if v not in ("윤곽", "채움"):
            raise ValueError("채움 여부는 윤곽 또는 채움")
        return v


class ToneIn(BaseModel):
    description: str = ""
    reference_asset_ids: list[int] = []


class VoiceIn(BaseModel):
    description: str = ""
    headline_max_chars: int | None = Field(default=None, ge=5, le=40)   # 헤드라인 권장 길이(자 이내)
    polite: bool = False                                                # 존댓말로 작성


class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    logo_asset_id: int | None = None
    logo_margin_percent: float | None = Field(default=None, ge=0, le=100)
    colors: list[ColorIn] = []
    fonts: list[FontIn] = []
    icon_style: IconStyleIn | None = None
    image_tone: ToneIn = ToneIn()
    voice: VoiceIn = VoiceIn()
    required_phrases: list[str] = []
    forbidden_phrases: list[str] = []
    levels: dict[str, str] = {}

    @field_validator("levels")
    @classmethod
    def _levels(cls, v: dict) -> dict:
        for k, lv in v.items():
            if k not in ITEMS:
                raise ValueError(f"알 수 없는 항목: {k}")
            if lv not in LEVELS:
                raise ValueError(f"구분값은 {LEVELS} 중 하나: {lv}")
        return v
