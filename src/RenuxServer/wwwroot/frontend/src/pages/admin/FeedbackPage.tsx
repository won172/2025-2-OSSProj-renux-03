import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchFeedback } from '../../admin/adminApi'
import {
  formatDateTime,
  formatFullDateTime,
  getApiErrorMessage,
  getFeedbackReasonLabel,
} from '../../admin/format'
import TrendChart, { type TrendPoint } from '../../components/admin/TrendChart'
import QuickFaqModal, { type QuickFaqSeed } from '../../components/admin/QuickFaqModal'
import { useAdminConsole } from '../../components/admin/adminConsoleContext'
import {
  EmptyState,
  ErrorNote,
  FilterBar,
  FilterChip,
  LoadingNote,
  MetricCard,
  PageHeader,
  Panel,
  StatusPill,
  TableScroll,
} from '../../components/admin/ui'
import type { RagFeedbackItem } from '../../types/admin'

type RatingFilter = 'all' | 'up' | 'down'

const RANGE_OPTIONS = [
  { key: '7', label: '7일' },
  { key: '30', label: '30일' },
  { key: 'all', label: '전체' },
]

const TREND_DAYS = 14

const rangeToFrom = (range: string): string | undefined => {
  if (range === 'all') return undefined
  const days = Number(range)
  if (!Number.isFinite(days)) return undefined
  const date = new Date()
  date.setDate(date.getDate() - (days - 1))
  date.setHours(0, 0, 0, 0)
  return date.toISOString()
}

const buildSatisfactionTrend = (items: RagFeedbackItem[]): TrendPoint[] => {
  const formatter = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' })
  const buckets = new Map<string, { up: number; total: number }>()
  const today = new Date()

  for (let offset = TREND_DAYS - 1; offset >= 0; offset -= 1) {
    const date = new Date(today)
    date.setDate(date.getDate() - offset)
    buckets.set(formatter.format(date), { up: 0, total: 0 })
  }

  items.forEach((item) => {
    if (!item.createdAt) return
    const date = new Date(item.createdAt)
    if (Number.isNaN(date.getTime())) return
    const bucket = buckets.get(formatter.format(date))
    if (!bucket) return
    bucket.total += 1
    if (item.rating === 1) bucket.up += 1
  })

  return Array.from(buckets.entries()).map(([key, bucket]) => ({
    label: key.slice(5).replace('-', '/'),
    value: bucket.total,
    secondary: bucket.total > 0 ? Math.round((bucket.up / bucket.total) * 100) : null,
  }))
}

const FeedbackPage = () => {
  const navigate = useNavigate()
  const { ragStatus, dataVersion } = useAdminConsole()

  const [items, setItems] = useState<RagFeedbackItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [ratingFilter, setRatingFilter] = useState<RatingFilter>('down')
  const [range, setRange] = useState('30')
  const [search, setSearch] = useState('')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [faqSeed, setFaqSeed] = useState<QuickFaqSeed | null>(null)

  const loadFeedback = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchFeedback({
        rating: ratingFilter === 'all' ? null : ratingFilter === 'up' ? 1 : -1,
        limit: 300,
        from: rangeToFrom(range),
      })
      setItems(data)
    } catch (fetchError) {
      setItems([])
      setError(getApiErrorMessage(fetchError, '사용자 피드백을 불러오지 못했습니다.'))
    } finally {
      setLoading(false)
    }
  }, [ratingFilter, range])

  useEffect(() => {
    void loadFeedback()
  }, [loadFeedback, dataVersion])

  const summary = ragStatus?.feedback
  const downReasons = summary?.down_reasons ?? summary?.downReasons ?? {}
  const satisfaction = summary?.satisfaction == null ? null : Math.round(summary.satisfaction * 100)

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (!term) return items
    return items.filter((item) => (
      (item.question ?? '').toLowerCase().includes(term)
      || (item.comment ?? '').toLowerCase().includes(term)
      || (item.answer ?? '').toLowerCase().includes(term)
    ))
  }, [items, search])

  const trend = useMemo(() => buildSatisfactionTrend(items), [items])

  return (
    <>
      <PageHeader
        title="피드백"
        description="답변 평가를 확인하고, 문제가 된 질문을 FAQ로 보완합니다."
        actions={
          <button type="button" className="ac-btn" onClick={() => { void loadFeedback() }} disabled={loading}>
            {loading ? '새로고침 중...' : '새로고침'}
          </button>
        }
      />

      <p className="ac-hint">상단 요약은 전체 기간 누적 평가입니다. 아래 목록·추이는 선택한 기간과 평가 필터의 최근 최대 300건을 기준으로 합니다.</p>
      <div className="ac-metrics">
        <MetricCard label="만족도" value={satisfaction == null ? '-' : `${satisfaction}%`} hint={`평가 ${summary?.total ?? 0}건`} />
        <MetricCard label="좋아요" value={`👍 ${summary?.up ?? 0}`} tone="success" />
        <MetricCard label="싫어요" value={`👎 ${summary?.down ?? 0}`} tone={(summary?.down ?? 0) > 0 ? 'warning' : undefined} />
        {Object.entries(downReasons).map(([reason, count]) => (
          <MetricCard key={reason} label={getFeedbackReasonLabel(reason)} value={count} />
        ))}
      </div>

      <Panel
        title={`평가 추이 (최근 ${TREND_DAYS}일)`}
        description="현재 기간·평가 필터로 불러온 목록의 최근 14일입니다. 면적은 건수, 파란 선은 해당 표본의 긍정 비율입니다. 전체 만족도 추세는 평가 필터를 전체로 바꿔 확인하세요."
      >
        {loading ? <LoadingNote /> : (
          <>
            <TrendChart points={trend} ariaLabel={`최근 ${TREND_DAYS}일 피드백 건수와 긍정 비율 추이`} />
            <div className="ac-legend" style={{ marginTop: '8px' }}>
              <span className="ac-legend__key"><i className="ac-legend__swatch" />평가 건수</span>
              <span className="ac-legend__key"><i className="ac-legend__swatch ac-legend__swatch--alt" />긍정 비율 (%)</span>
            </div>
          </>
        )}
      </Panel>

      <Panel padded={false}>
        <FilterBar>
          <FilterChip active={ratingFilter === 'down'} onClick={() => setRatingFilter('down')}>부정</FilterChip>
          <FilterChip active={ratingFilter === 'up'} onClick={() => setRatingFilter('up')}>긍정</FilterChip>
          <FilterChip active={ratingFilter === 'all'} onClick={() => setRatingFilter('all')}>전체</FilterChip>
          <span style={{ width: '1px', height: '20px', background: 'var(--ac-line)' }} aria-hidden="true" />
          {RANGE_OPTIONS.map((option) => (
            <FilterChip key={option.key} active={range === option.key} onClick={() => setRange(option.key)}>
              {option.label}
            </FilterChip>
          ))}
          <input
            type="search"
            className="ac-input ac-input--grow"
            placeholder="질문·코멘트 검색"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="피드백 검색"
          />
        </FilterBar>

        {loading ? (
          <LoadingNote />
        ) : error ? (
          <ErrorNote>{error}</ErrorNote>
        ) : filtered.length === 0 ? (
          <EmptyState>조건에 맞는 피드백이 없습니다.</EmptyState>
        ) : (
          <TableScroll mobileCards minWidth={960}>
            <table className="ac-table" role="table">
              <thead role="rowgroup">
                <tr role="row">
                  <th role="columnheader" scope="col">평가</th>
                  <th role="columnheader" scope="col">사유</th>
                  <th role="columnheader" scope="col">코멘트</th>
                  <th role="columnheader" scope="col">질문</th>
                  <th role="columnheader" scope="col">학과</th>
                  <th role="columnheader" scope="col">시각</th>
                  <th role="columnheader" scope="col" className="actions">조치</th>
                </tr>
              </thead>
              <tbody role="rowgroup">
                {filtered.map((item) => (
                  <tr role="row" key={item.id}>
                    <td role="cell" data-label="평가">
                      <StatusPill tone={item.rating === 1 ? 'success' : 'danger'}>
                        {item.rating === 1 ? '👍 긍정' : '👎 부정'}
                      </StatusPill>
                    </td>
                    <td role="cell" data-label="사유">{getFeedbackReasonLabel(item.reason)}</td>
                    <td role="cell" data-label="코멘트" className="truncate" title={item.comment ?? ''}><details className="ac-cell-details"><summary>{item.comment?.trim().slice(0, 70) || '코멘트 없음'}</summary><p>{item.comment}</p></details></td>
                    <td role="cell" data-label="질문·답변"
                      className={expandedId === item.id ? '' : 'truncate'}
                      title={item.question ?? ''}
                    >
                      {item.question?.trim() || '-'}
                      {expandedId === item.id && item.answer && (
                        <div className="ac-detail__content" style={{ marginTop: '8px' }}>{item.answer}</div>
                      )}
                    </td>
                    <td role="cell" data-label="학과">{item.major ?? '-'}</td>
                    <td role="cell" data-label="시각" className="num" title={formatFullDateTime(item.createdAt)}>{formatDateTime(item.createdAt)}</td>
                    <td role="cell" data-label="조치" className="actions">
                      <button
                        type="button"
                        className="ac-btn ac-btn--sm ac-btn--ghost"
                        onClick={() => setExpandedId(expandedId === item.id ? null : item.id)}
                        aria-expanded={expandedId === item.id}
                      >
                        {expandedId === item.id ? '접기' : '답변 보기'}
                      </button>{' '}
                      <button
                        type="button"
                        className="ac-btn ac-btn--sm"
                        onClick={() => setFaqSeed({
                          question: item.question ?? '',
                          // 부정 평가를 받은 답변은 그대로 재사용하지 않고 새로 작성하게 둔다.
                          answer: item.rating === 1 ? (item.answer ?? '') : '',
                          category: item.major,
                        })}
                      >
                        FAQ로 등록
                      </button>{' '}
                      <button
                        type="button"
                        className="ac-btn ac-btn--sm ac-btn--ghost"
                        onClick={() => navigate(`/admin/logs?search=${encodeURIComponent(item.question ?? '')}`)}
                      >
                        로그 보기
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </Panel>

      <QuickFaqModal seed={faqSeed} onClose={() => setFaqSeed(null)} />
    </>
  )
}

export default FeedbackPage
