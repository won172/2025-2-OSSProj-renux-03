import { useMemo } from 'react'

export type ChatSource = {
  source?: string | null
  chunkId?: string | null
  title?: string | null
  url?: string | null
  publishedAt?: string | null
  snippet?: string | null
  vectorScore?: number | null
  sparseScore?: number | null
  hybridScore?: number | null
  recencyScore?: number | null
  finalScore?: number | null
}

type SourceCardsProps = {
  sources?: ChatSource[] | null
  showScores: boolean
  isFallback?: boolean
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

const buildSourceKey = (source: ChatSource) => {
  const url = source.url?.trim()
  if (url) return url

  return [
    source.source?.trim() ?? '',
    source.title?.trim() ?? '',
    source.publishedAt?.trim() ?? '',
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
      chunkId: existing.chunkId ?? source.chunkId,
      title: existing.title ?? source.title,
      url: existing.url ?? source.url,
      publishedAt: existing.publishedAt ?? source.publishedAt,
      snippet: nextSnippetLength > currentSnippetLength ? source.snippet : existing.snippet,
      vectorScore: Math.max(existing.vectorScore ?? Number.NEGATIVE_INFINITY, source.vectorScore ?? Number.NEGATIVE_INFINITY),
      sparseScore: Math.max(existing.sparseScore ?? Number.NEGATIVE_INFINITY, source.sparseScore ?? Number.NEGATIVE_INFINITY),
      hybridScore: Math.max(existing.hybridScore ?? Number.NEGATIVE_INFINITY, source.hybridScore ?? Number.NEGATIVE_INFINITY),
      recencyScore: Math.max(existing.recencyScore ?? Number.NEGATIVE_INFINITY, source.recencyScore ?? Number.NEGATIVE_INFINITY),
      finalScore: Math.max(existing.finalScore ?? Number.NEGATIVE_INFINITY, source.finalScore ?? Number.NEGATIVE_INFINITY),
    })
  })

  return Array.from(grouped.values()).map((source) => ({
    ...source,
    vectorScore: Number.isFinite(source.vectorScore ?? Number.NaN) ? source.vectorScore : null,
    sparseScore: Number.isFinite(source.sparseScore ?? Number.NaN) ? source.sparseScore : null,
    hybridScore: Number.isFinite(source.hybridScore ?? Number.NaN) ? source.hybridScore : null,
    recencyScore: Number.isFinite(source.recencyScore ?? Number.NaN) ? source.recencyScore : null,
    finalScore: Number.isFinite(source.finalScore ?? Number.NaN) ? source.finalScore : null,
  }))
}

const SourceCards = ({ sources, showScores, isFallback = false }: SourceCardsProps) => {
  const normalizedSources = useMemo(() => {
    if (!Array.isArray(sources) || sources.length === 0) return []
    return mergeSources(sources)
  }, [sources])

  const hasSources = normalizedSources.length > 0

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
    <details className="source-cards" aria-label="답변 출처">
      <summary className="source-cards__header">
        <span className="source-cards__title">사용 출처</span>
        <span className="source-cards__count">{normalizedSources.length}개</span>
      </summary>
      <div className="source-cards__list">
        {normalizedSources.map((source, index) => {
          const title = source.title?.trim() || source.snippet?.trim().slice(0, 42) || `출처 ${index + 1}`
          return (
            <article className="source-card" key={`${buildSourceKey(source)}-${index}`}>
              <div className="source-card__meta">
                <span className="source-card__dataset">{getDatasetLabel(source.source)}</span>
                <span className="source-card__date">{formatDate(source.publishedAt)}</span>
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
