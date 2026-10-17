import { useEffect, useMemo, useRef, useState } from 'react'

import { normalizeSources, type ChatSource } from '../../chat/sources'
export type { ChatSource } from '../../chat/sources'

type SourceCardsProps = {
  sources?: ChatSource[] | null
  showScores: boolean
  isFallback?: boolean
  activeCitationNumber?: number | null
  citationRequest?: number
}

const datasetLabels: Record<string, string> = {
  notices: '공지사항',
  rules: '학칙',
  schedule: '학사일정',
  courses: '강의/수업',
  staff: '전화번호부',
  meals: '학식',
  guidance: '공식 안내',
  attachments: '첨부자료',
}

const formatDate = (value?: string | null) => {
  if (!value) return '날짜 없음'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('ko-KR', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(date)
}

const formatScore = (value?: number | null) => {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-'
  return value.toFixed(3)
}

const getDatasetLabel = (source?: string | null) => {
  if (!source) return '데이터셋 없음'
  return datasetLabels[source] ?? source
}

const getPublishedAt = (source: ChatSource) => source.publishedAt ?? source.published_at ?? null

const SourceCards = ({ sources, showScores, isFallback = false, activeCitationNumber = null, citationRequest = 0 }: SourceCardsProps) => {
  const detailsRef = useRef<HTMLDetailsElement | null>(null)
  const [isOpen, setIsOpen] = useState(false)
  const normalizedSources = useMemo(() => {
    if (!Array.isArray(sources) || sources.length === 0) return []
    return normalizeSources(sources)
  }, [sources])

  const hasSources = normalizedSources.length > 0

  useEffect(() => {
    if (!activeCitationNumber || !hasSources) return
    setIsOpen(true)

    const timer = window.setTimeout(() => {
      const activeCard = detailsRef.current?.querySelector<HTMLElement>(
        `[data-citation-number="${activeCitationNumber}"]`,
      )
      activeCard?.focus({ preventScroll: true })
      activeCard?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [activeCitationNumber, hasSources, citationRequest])

  if (!hasSources) {
    if (!isFallback) return null

    return (
      <div className="source-cards source-cards--empty">
        <span className="source-cards__empty-title">확인된 출처 없음</span>
        <span className="source-cards__empty-text">검색 기준을 충족하는 학교 자료가 없어 근거 기반 답변을 생성하지 않았습니다.</span>
      </div>
    )
  }

  return (
    <details
      className="source-cards"
      aria-label="답변 출처"
      open={isOpen}
      ref={detailsRef}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
    >
      <summary className="source-cards__header">
        <span className="source-cards__title">사용 출처</span>
        <span className="source-cards__count">{normalizedSources.length}개</span>
      </summary>
      {activeCitationNumber && !normalizedSources.some((source) => source.citationNumber === activeCitationNumber) && <p role="status">문서{activeCitationNumber}에 해당하는 출처 번호가 이 응답에 없습니다.</p>}
      <div className="source-cards__list">
        {normalizedSources.map((source, index) => {
          const title = source.title?.trim() || source.snippet?.trim().slice(0, 42) || `출처 ${index + 1}`
          const citationNumber = source.citationNumber
          const isActive = citationNumber !== null && citationNumber === activeCitationNumber
          return (
            <article
              className={`source-card${isActive ? ' source-card--active' : ''}`}
              data-citation-number={citationNumber ?? undefined}
              tabIndex={-1}
              key={`${citationNumber}:${source.sourceRef ?? source.chunkId ?? 'legacy'}:${index}`}
            >
              <div className="source-card__meta">
                <div className="source-card__badges">
                  <span className="source-card__citation">{citationNumber === null ? `출처 ${index + 1} · 번호 없음` : `문서${citationNumber}`}</span>
                  <span className="source-card__dataset">{getDatasetLabel(source.source)}</span>
                </div>
                <span className="source-card__date">{formatDate(getPublishedAt(source))}</span>
              </div>
              <h4 className="source-card__title">{title}</h4>
              {source.snippet && <p className="source-card__snippet">{source.snippet}</p>}
              <div className="source-card__footer">
                {source.url ? (
                  <a className="source-card__link" href={source.url} target="_blank" rel="noopener noreferrer">
                    원문 보기
                  </a>
                ) : (
                  <span className="source-card__link source-card__link--disabled">URL 없음</span>
                )}
                {showScores && (
                  <span className="source-card__score">
                    final {formatScore(source.finalScore)}
                    {source.hybridScore !== undefined && source.hybridScore !== null ? ` / hybrid ${formatScore(source.hybridScore)}` : ''}
                  </span>
                )}
              </div>
            </article>
          )
        })}
      </div>
    </details>
  )
}

export default SourceCards
