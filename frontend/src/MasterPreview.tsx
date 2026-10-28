// 보기 전용 시안: 광고가 실제로 어떻게 보이는지의 목업. 이미지는 합성 파이프라인이 만든 '진짜 합성본'(브랜드 폰트·스크림·로고 그대로)이고,
// 본문·제목·CTA 는 매체가 이미지 밖에 그려 주는 텍스트 영역을 흉내 낸다(이 프로젝트의 원칙: 이미지에는 제목만 합성, 본문·CTA 는 매체 텍스트 필드).
// 저장·검수·내보내기 대상이 아니다.
import { useState } from 'react'
import type { WorkspaceData } from './useWorkspace'

type PresetLite = { id: number; platform: string; name: string; width: number | null; height: number | null }

export default function MasterPreview({ data, presets, compact }: { data: WorkspaceData; presets: PresetLite[]; compact?: boolean }) {
  const v = data.selected
  const logo = data.logoAssetId ? `/api/assets/${data.logoAssetId}/file` : null
  // 대표 합성본: 완성형(제목·로고를 합성한) 지면 우선, 없으면 분리 자산형, 그것도 없으면 배경
  const candidates = data.composites.map((c) => ({ ...c, preset: presets.find((p) => p.id === c.preset_id) }))
  const ordered = [...candidates].sort((a, b) => Number(a.separate) - Number(b.separate))
  const [pick, setPick] = useState<number | null>(null)
  const cur = ordered.find((c) => c.preset_id === pick) ?? ordered[0] ?? null
  // 카드에 그려질 긴 변(CSS px) × 화면 픽셀 배율을 보내 흐리지 않게 한다(서버가 720·1080·1600 단계로 올림). 카드 폭은 큰 카드 약 460, 작은 카드 약 320
  const longSide = Math.round((compact ? 320 : 460) * Math.max(1, cur && cur.width ? cur.height / cur.width : 1) * (window.devicePixelRatio || 1))
  const img = cur ? `/api/generated/${cur.id}/thumb?side=${longSide}&v=${cur.file ?? cur.id}`
    : data.backgroundId ? `/api/generated/${data.backgroundId}/thumb?side=${longSide}&v=${data.backgroundFile ?? data.backgroundId}` : null
  const card = (
    <div className={`feed-card ${compact ? 'compact' : ''}`}>
      <div className="feed-head">
        {logo ? <img className="feed-avatar" src={logo} alt="" /> : <span className="feed-avatar text">{data.brandName.slice(0, 1)}</span>}
        <div className="feed-who"><strong>{data.brandName}</strong><span className="muted">광고 · 매체가 그리는 영역</span></div>
      </div>
      <div className="feed-image" style={cur ? { aspectRatio: `${cur.width} / ${cur.height}` } : undefined}>
        {img ? <img src={img} alt="합성본" /> : <span className="muted">합성본이 없습니다. ④ 이미지에서 생성하면 여기 그대로 보입니다.</span>}
        <span className="feed-tag">이미지 안 — 합성본 그대로 ({cur ? `${cur.preset?.platform ?? ''} ${cur.preset?.name ?? ''}`.trim() : '배경만'})</span>
      </div>
      <div className="feed-text">
        {v?.body && <p className="feed-body">{v.body}</p>}
        <div className="feed-row">
          <div className="feed-headline">{v?.headline || <span className="muted">제목 — 카피를 고르면 표시됩니다</span>}</div>
          {v?.cta && <span className="feed-cta">{v.cta}</span>}
        </div>
      </div>
    </div>
  )
  if (compact) return card
  return (
    <div className="master">
      <div>
        {ordered.length > 1 && (
          <label className="muted" style={{ display: 'block', marginBottom: 6 }}>보여 줄 합성본{' '}
            <select value={cur?.preset_id ?? ''} onChange={(e) => setPick(Number(e.target.value))}>
              {ordered.map((c) => <option key={c.preset_id} value={c.preset_id}>{c.preset?.platform} · {c.preset?.name}{c.separate ? ' (분리 자산형)' : ''}{c.edited ? ' · 편집됨' : ''}</option>)}
            </select>
          </label>
        )}
        {card}
      </div>
      <div className="master-side">
        <dl className="master-fields">
          <dt>이미지 안</dt><dd>배경 + 로고 + <strong>제목</strong>(브랜드 폰트·색으로 합성). 분리 자산형 지면은 배경만.</dd>
          <dt>이미지 밖</dt><dd>본문·CTA 는 매체의 텍스트 필드(기본 문구·설명·버튼)로 제출되어 매체가 자기 글꼴로 그립니다. 어느 매체도 본문·CTA 를 이미지에 합성하지 않습니다.</dd>
          <dt>제목</dt><dd>{v ? <>{v.headline} <span className="muted n">{v.headline.length}자 · 이미지 안 + 매체 제목 필드</span></> : <span className="muted">선택된 카피 없음</span>}</dd>
          <dt>본문</dt><dd>{v?.body ? <>{v.body} <span className="muted n">{v.body.length}자 · 매체 '기본 문구/설명' 필드</span></> : <span className="muted">—</span>}</dd>
          <dt>CTA</dt><dd>{v?.cta ? <>{v.cta} <span className="muted n">매체 버튼 설정(카카오 '버튼명' 필드는 글자 수 검수)</span></> : <span className="muted">—</span>}</dd>
          <dt>배경</dt><dd>{data.backgroundId ? <>{data.backgroundQuality ?? ''}{data.backgroundModel ? `${data.backgroundQuality ? ' · ' : ''}${data.backgroundModel}` : ''}</> : <span className="muted">아직 없음</span>}</dd>
        </dl>
        <p className="muted master-note">
          위 카드는 실제 합성본 파일을 그대로 보여 주고, 그 아래 텍스트 영역은 매체(Meta·카카오·Google)가 이미지 밖에 그려 주는 부분을 흉내 낸 것입니다.
          이 장은 저장·검수·내보내기 대상이 아닙니다.
        </p>
      </div>
    </div>
  )
}
