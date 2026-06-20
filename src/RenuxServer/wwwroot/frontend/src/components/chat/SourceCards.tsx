import { useEffect, useMemo, useRef, useState } from 'react'

export type ChatSource = {
  source?: string | null
  citationNumber?: number | null
  citation_number?: number | null
  chunkId?: string | null
  chunk_id?: string | null
  title?: string | null
  url?: string | null
  publishedAt?: string | null
  published_at?: string | null
  snippet?: string | null
  vectorScore?: number | null
  vector_score?: number | null
  sparseScore?: number | null
  sparse_score?: number | null
  hybridScore?: number | null
  hybrid_score?: number | null
  recencyScore?: number | null
  recency_score?: number | null
  finalScore?: number | null
  final_score?: number | null
}

type SourceCardsProps = {
  sources?: ChatSource[] | null
  showScores: boolean
  isFallback?: boolean
  activeCitationNumber?: number | null
}

const datasetLabels: Record<string, string> = {
  notices: '공지사항',
  rules: '학칙',
  schedule: '학사일정',
  courses: '강의/수업',
  staff: '전화번호부',
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

const getCitationNumber = (source: ChatSource) => source.citationNumber ?? source.citation_number ?? null
const getChunkId = (source: ChatSource) => source.chunkId ?? source.chunk_id ?? null
const getPublishedAt = (source: ChatSource) => source.publishedAt ?? source.published_at ?? null
const getVectorScore = (source: ChatSource) => source.vectorScore ?? source.vector_score ?? null
const getSparseScore = (source: ChatSource) => source.sparseScore ?? source.sparse_score ?? null
const getHybridScore = (source: ChatSource) => source.hybridScore ?? source.hybrid_score ?? null
const getRecencyScore = (source: ChatSource) => source.recencyScore ?? source.recency_score ?? null
const getFinalScore = (source: ChatSource) => source.finalScore ?? source.final_score ?? null

const buildSourceKey = (source: ChatSource) => {
  const url = source.url?.trim()
  if (url) return url

  return [
    source.source?.trim() ?? '',
    source.title?.trim() ?? '',
    getPublishedAt(source)?.trim() ?? '',
  ].join('::')
}

const mergeSources = (sources: ChatSource[]) => {
  const grouped = new Map<string, ChatSource>()

  sources.forEach((source) => {
    const key = buildSourceKey(source)
    const existing = grouped.get(key)

    if (!existing) {
      grouped.set(key, { ...source })
      return
    }

    const currentSnippetLength = existing.snippet?.trim().length ?? 0
    const nextSnippetLength = source.snippet?.trim().length ?? 0

    grouped.set(key, {
      ...existing,
      citationNumber: getCitationNumber(existing) ?? getCitationNumber(source),
      chunkId: getChunkId(existing) ?? getChunkId(source),
      title: existing.title ?? source.title,
      url: existing.url ?? source.url,
      publishedAt: getPublishedAt(existing) ?? getPublishedAt(source),
      snippet: nextSnippetLength > currentSnippetLength ? source.snippet : existing.snippet,
      vectorScore: Math.max(getVectorScore(existing) ?? Number.NEGATIVE_INFINITY, getVectorScore(source) ?? Number.NEGATIVE_INFINITY),
      sparseScore: Math.max(getSparseScore(existing) ?? Number.NEGATIVE_INFINITY, getSparseScore(source) ?? Number.NEGATIVE_INFINITY),
      hybridScore: Math.max(getHybridScore(existing) ?? Number.NEGATIVE_INFINITY, getHybridScore(source) ?? Number.NEGATIVE_INFINITY),
      recencyScore: Math.max(getRecencyScore(existing) ?? Number.NEGATIVE_INFINITY, getRecencyScore(source) ?? Number.NEGATIVE_INFINITY),
      finalScore: Math.max(getFinalScore(existing) ?? Number.NEGATIVE_INFINITY, getFinalScore(source) ?? Number.NEGATIVE_INFINITY),
    })
  })

  return Array.from(grouped.values()).map((source) => ({
    ...source,
    citationNumber: getCitationNumber(source),
    chunkId: getChunkId(source),
    publishedAt: getPublishedAt(source),
    vectorScore: Number.isFinite(getVectorScore(source) ?? Number.NaN) ? getVectorScore(source) : null,
    sparseScore: Number.isFinite(getSparseScore(source) ?? Number.NaN) ? getSparseScore(source) : null,
    hybridScore: Number.isFinite(getHybridScore(source) ?? Number.NaN) ? getHybridScore(source) : null,
    recencyScore: Number.isFinite(getRecencyScore(source) ?? Number.NaN) ? getRecencyScore(source) : null,
    finalScore: Number.isFinite(getFinalScore(source) ?? Number.NaN) ? getFinalScore(source) : null,
  }))
}

const SourceCards = ({ sources, showScores, isFallback = false, activeCitationNumber = null }: SourceCardsProps) => {
  const detailsRef = useRef<HTMLDetailsElement | null>(null)
  const [isOpen, setIsOpen] = useState(false)
  const normalizedSources = useMemo(() => {
    if (!Array.isArray(sources) || sources.length === 0) return []
    return mergeSources(sources)
  }, [sources])

  const hasSources = normalizedSources.length > 0

  useEffect(() => {
    if (!activeCitationNumber || !hasSources) return
    setIsOpen(true)

    window.setTimeout(() => {
      const activeCard = detailsRef.current?.querySelector<HTMLElement>(
        `[data-citation-number="${activeCitationNumber}"]`,
      )
      activeCard?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }, 0)
  }, [activeCitationNumber, hasSources])

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
      <div className="source-cards__list">
        {normalizedSources.map((source, index) => {
          const title = source.title?.trim() || source.snippet?.trim().slice(0, 42) || `출처 ${index + 1}`
          const citationNumber = getCitationNumber(source) ?? index + 1
          const isActive = citationNumber === activeCitationNumber
          return (
            <article
              className={`source-card${isActive ? ' source-card--active' : ''}`}
              data-citation-number={citationNumber}
              key={`${buildSourceKey(source)}-${index}`}
            >
              <div className="source-card__meta">
                <div className="source-card__badges">
                  <span className="source-card__citation">문서{citationNumber}</span>
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
