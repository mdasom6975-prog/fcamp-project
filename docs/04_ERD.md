# ERD

`backend/app/models.py` 기준. 실선은 ForeignKey 제약, 점선은 FK 제약 없이 정수 컬럼으로만 참조하는 관계.

```mermaid
erDiagram
    brands ||--o{ brand_versions : "버전 보존"
    brands |o..o{ assets : "brand_id"
    brands ||--o{ designs : ""
    brand_versions ||--o{ designs : "brand_version_id"
    brand_versions ||--o{ exports : "brand_version_id"
    brands |o..o| brand_versions : "current_version_id"
    assets |o..o{ brand_versions : "logo_asset_id"

    platforms ||--o{ placements : ""
    placements ||--o{ preset_versions : "규격 버전"

    designs ||--o{ design_snapshots : ""
    designs ||--o{ elements : ""
    designs ||--o{ review_results : ""
    designs ||--o{ exports : ""
    designs ||--o{ copy_variants : ""
    designs ||--o{ generated_images : ""
    designs ||--o{ icon_assets : ""

    preset_versions |o--o{ elements : ""
    preset_versions |o--o{ review_results : ""
    preset_versions ||--o{ copy_variants : ""
    preset_versions ||--o{ generated_images : ""

    copy_variants |o..o{ generated_images : "copy_variant_id"
    brand_versions |o..o{ generated_images : "brand_version_id"
    brand_versions ||..o{ icon_assets : "brand_version_id"

    brands {
        int id PK
        string name
        int current_version_id
        datetime created_at
    }
    brand_versions {
        int id PK
        int brand_id FK
        int version_no
        int logo_asset_id
        json logo_margin
        json levels
        json colors
        json fonts
        json icon_style
        json image_tone
        json voice
        json required_phrases
        json forbidden_phrases
        datetime created_at
    }
    assets {
        int id PK
        int brand_id FK
        string kind
        string file_path
        string mime
        int bytes
        int width
        int height
        datetime created_at
    }
    platforms {
        int id PK
        string name UK
    }
    placements {
        int id PK
        int platform_id FK
        string ad_type
        string name
        string content_kind
        string purpose
    }
    preset_versions {
        int id PK
        int placement_id FK
        int version_no
        bool active
        int production_width
        int production_height
        string ratio
        json conditions
        json text_limits
        json safe_area
        json required_assets
        string status
        text note
        json source_urls
        string checked_at
    }
    designs {
        int id PK
        int brand_id FK
        int brand_version_id FK
        json preset_version_ids
        json brief
        string kind
        string status
        datetime created_at
        datetime updated_at
    }
    design_snapshots {
        int id PK
        int design_id FK
        datetime saved_at
        json elements
    }
    elements {
        int id PK
        int design_id FK
        int preset_version_id FK
        string type
        string scope
        json props
        int z_index
        bool locked
    }
    review_results {
        int id PK
        int design_id FK
        int preset_version_id FK
        string category
        string item
        string status
        string level
        text message
        bool blocking
        bool needs_human
        bool confirmed
        json location
        bool valid
        datetime created_at
    }
    exports {
        int id PK
        int design_id FK
        int brand_version_id FK
        json preset_version_ids
        json files
        json review_summary
        string zip_path
        datetime created_at
    }
    copy_variants {
        int id PK
        int design_id FK
        int preset_version_id FK
        int batch_no
        int variant_no
        text headline
        text body
        text cta
        json limits
        json violations
        int attempts
        bool selected
        datetime created_at
    }
    generated_images {
        int id PK
        int design_id FK
        int preset_version_id FK
        string kind
        string file_path
        int width
        int height
        text prompt
        int copy_variant_id
        int brand_version_id
        json layout
        datetime created_at
    }
    icon_assets {
        int id PK
        int design_id FK
        int brand_version_id
        int batch_no
        int position
        string name
        string concept
        json elements
        json style
        text svg
        string png_path
        int width
        int height
        json violations
        datetime created_at
    }
```

## 참고
- `designs.preset_version_ids`, `exports.preset_version_ids`는 JSON 배열이라 `preset_versions`와 N:M 관계지만 연결 테이블이 없다.
- `brands.current_version_id`, `brand_versions.logo_asset_id`, `generated_images.copy_variant_id`/`brand_version_id`, `icon_assets.brand_version_id`는 FK 제약이 없는 정수 컬럼이다.
