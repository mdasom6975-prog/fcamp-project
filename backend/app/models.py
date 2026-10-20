"""docs/01_데이터구조_설계.md 의 엔티티를 그대로 옮긴 테이블 정의."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now()


class Brand(Base):
    __tablename__ = "brands"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(300), default="", server_default="")  # 서비스 설명(선택)
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    versions: Mapped[list["BrandVersion"]] = relationship(back_populates="brand")


class BrandVersion(Base):
    """브랜드 규칙 묶음. 변경 시 새 버전을 만들고 기존 버전은 보존한다."""
    __tablename__ = "brand_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    logo_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logo_square_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # 정사각 로고(1:1, Google PMax 등 필수 자산). 합성에는 쓰지 않는다
    logo_margin: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # {percent}
    levels: Mapped[dict] = mapped_column(JSON, default=dict)         # 항목별 필수|권장|수정가능
    colors: Mapped[list] = mapped_column(JSON, default=list)         # [{name, hex, role, level}]
    fonts: Mapped[list] = mapped_column(JSON, default=list)          # [{family, role, level}]
    icon_style: Mapped[dict] = mapped_column(JSON, default=dict)     # {stroke_width, corner, level}
    image_tone: Mapped[dict] = mapped_column(JSON, default=dict)
    voice: Mapped[dict] = mapped_column(JSON, default=dict)
    required_phrases: Mapped[list] = mapped_column(JSON, default=list)
    forbidden_phrases: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    brand: Mapped[Brand] = relationship(back_populates="versions")


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))  # logo | product | background | icon | generated
    file_path: Mapped[str] = mapped_column(String(500))
    mime: Mapped[str] = mapped_column(String(50))
    bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Platform(Base):
    __tablename__ = "platforms"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    placements: Mapped[list["Placement"]] = relationship(back_populates="platform")


class Placement(Base):
    """플랫폼 > 광고 유형/지면"""
    __tablename__ = "placements"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"))
    ad_type: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    content_kind: Mapped[str] = mapped_column(String(20))  # 완성형 | 분리 자산형
    purpose: Mapped[str] = mapped_column(String(20), default="광고")  # 광고 | 웹용 | 사용자 지정
    platform: Mapped[Platform] = relationship(back_populates="placements")
    versions: Mapped[list["PresetVersion"]] = relationship(back_populates="placement")


class PresetVersion(Base):
    """규격 버전. 수치는 공식 문서에서 확인된 값만 저장한다 (AI 추측 금지)."""
    __tablename__ = "preset_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    placement_id: Mapped[int] = mapped_column(ForeignKey("placements.id"))
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    production_width: Mapped[int | None] = mapped_column(Integer, nullable=True)   # 제작 기본값
    production_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ratio: Mapped[str | None] = mapped_column(String(20), nullable=True)
    conditions: Mapped[list] = mapped_column(JSON, default=list)   # [{name, value, level, status, note}]
    text_limits: Mapped[list] = mapped_column(JSON, default=list)  # [{field, min_chars, max_chars, max_px, count_method, required, level}]
    safe_area: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    required_assets: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20))  # 확인됨 | 충돌 | 확인 필요
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_urls: Mapped[list] = mapped_column(JSON, default=list)
    checked_at: Mapped[str | None] = mapped_column(String(10), nullable=True)
    placement: Mapped[Placement] = relationship(back_populates="versions")


class Design(Base):
    __tablename__ = "designs"
    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"))
    brand_version_id: Mapped[int] = mapped_column(ForeignKey("brand_versions.id"))
    preset_version_ids: Mapped[list] = mapped_column(JSON, default=list)
    brief: Mapped[dict] = mapped_column(JSON, default=dict)
    kind: Mapped[str] = mapped_column(String(20))   # 아이콘 세트 | 광고 이미지
    status: Mapped[str] = mapped_column(String(20), default="미검수")  # 미검수 | 검수 완료 | 수정됨
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class DesignSnapshot(Base):
    """저장 시점 보관. 복원·비교 UI는 만들지 않는다 (확정된 결정)."""
    __tablename__ = "design_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    design_id: Mapped[int] = mapped_column(ForeignKey("designs.id"))
    saved_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    elements: Mapped[list] = mapped_column(JSON, default=list)


class Element(Base):
    __tablename__ = "elements"
    id: Mapped[int] = mapped_column(primary_key=True)
    design_id: Mapped[int] = mapped_column(ForeignKey("designs.id"))
    preset_version_id: Mapped[int | None] = mapped_column(ForeignKey("preset_versions.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(10))  # text | logo | image | shape
    scope: Mapped[str] = mapped_column(String(10), default="공통")  # 공통 | 지면별
    props: Mapped[dict] = mapped_column(JSON, default=dict)  # x, y, w, h, color, font, text, asset_id ...
    z_index: Mapped[int] = mapped_column(Integer, default=0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)


class ReviewResult(Base):
    """검수 결과 한 항목. 시안을 수정하면 valid=False 로 무효화되고 다시 검수해야 한다."""
    __tablename__ = "review_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    design_id: Mapped[int] = mapped_column(ForeignKey("designs.id"))
    preset_version_id: Mapped[int | None] = mapped_column(ForeignKey("preset_versions.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(10), default="규격")   # 규격 | 문구 | 브랜드 | 시안
    item: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(10))  # 통과 | 경고 | 수정 필요 | 확인 필요
    level: Mapped[str | None] = mapped_column(String(10), nullable=True)   # 근거가 된 구분값
    message: Mapped[str] = mapped_column(Text, default="")
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)         # 검수 완료 내보내기를 막는 항목
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)      # 사람이 확인해야 하는 항목
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)        # 사람이 '확인함' 표시
    location: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    valid: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Export(Base):
    __tablename__ = "exports"
    id: Mapped[int] = mapped_column(primary_key=True)
    design_id: Mapped[int] = mapped_column(ForeignKey("designs.id"))
    brand_version_id: Mapped[int] = mapped_column(ForeignKey("brand_versions.id"))
    preset_version_ids: Mapped[list] = mapped_column(JSON, default=list)
    files: Mapped[list] = mapped_column(JSON, default=list)
    review_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    zip_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CopyVariant(Base):
    """생성된 카피 시안. 지면별 문구 제한을 조건으로 생성하고 코드로 재검사한 결과를 함께 저장한다."""
    __tablename__ = "copy_variants"
    id: Mapped[int] = mapped_column(primary_key=True)
    design_id: Mapped[int] = mapped_column(ForeignKey("designs.id"))
    preset_version_id: Mapped[int] = mapped_column(ForeignKey("preset_versions.id"))
    batch_no: Mapped[int] = mapped_column(Integer, default=1)       # 같은 시안·지면에서 생성한 차수
    variant_no: Mapped[int] = mapped_column(Integer)
    headline: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    cta: Mapped[str] = mapped_column(Text, default="")
    limits: Mapped[dict] = mapped_column(JSON, default=dict)         # 생성 시 적용한 제한
    violations: Mapped[list] = mapped_column(JSON, default=list)     # [{type, field, detail, severity}]
    attempts: Mapped[int] = mapped_column(Integer, default=1)        # 자동 재생성 포함 시도 횟수
    selected: Mapped[bool] = mapped_column(Boolean, default=False)   # 합성에 사용할 카피 (지면당 1개)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class GeneratedImage(Base):
    """배경 이미지와 합성 결과. layout 은 이후 편집 요소(Element)의 출발점이 된다."""
    __tablename__ = "generated_images"
    id: Mapped[int] = mapped_column(primary_key=True)
    design_id: Mapped[int] = mapped_column(ForeignKey("designs.id"))
    preset_version_id: Mapped[int] = mapped_column(ForeignKey("preset_versions.id"))
    kind: Mapped[str] = mapped_column(String(12))          # background | composite
    file_path: Mapped[str] = mapped_column(String(500))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    copy_variant_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    brand_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    layout: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class IconAsset(Base):
    """아이콘 세트의 개별 아이콘.
    - 3d (기본): 이미지 모델이 투명 PNG 를 렌더. prompt·source_path(1024 원본) 를 남기고 elements=[] svg="" 이다.
    - svg: 도형(elements)은 모델이 만들고, 선 굵기·모서리·색·여백은 코드가 강제한다.
    어느 방식인지는 style.render ("3d" | "svg") 로 구분한다."""
    __tablename__ = "icon_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    design_id: Mapped[int] = mapped_column(ForeignKey("designs.id"))
    brand_version_id: Mapped[int] = mapped_column(Integer)
    batch_no: Mapped[int] = mapped_column(Integer, default=1)
    position: Mapped[int] = mapped_column(Integer)          # 세트 안의 순서
    name: Mapped[str] = mapped_column(String(60))           # 기능명
    concept: Mapped[str] = mapped_column(String(200), default="")
    elements: Mapped[list] = mapped_column(JSON, default=list)   # 정리된 도형 목록
    style: Mapped[dict] = mapped_column(JSON, default=dict)      # 적용한 스타일 값
    svg: Mapped[str] = mapped_column(Text)
    png_path: Mapped[str] = mapped_column(String(500))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    violations: Mapped[list] = mapped_column(JSON, default=list)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)              # 3d: 렌더에 쓴 이미지 프롬프트
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 3d: 1024 원본 PNG
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SourceCheck(Base):
    """규격 공식 페이지 확인 이력. 규격 수치는 자동으로 바꾸지 않고, 원문 변경 여부만 기록한다."""
    __tablename__ = "source_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(50))
    url: Mapped[str] = mapped_column(String(500), unique=True)
    last_fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    status: Mapped[str] = mapped_column(String(20))        # 기준 저장 | 변경 없음 | 변경 감지 | 실패
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 마지막으로 변경을 감지한 시각
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
