import { routeLabel } from '../../admin/display'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { resolveApiUrl, withNgrokHeader } from '../../api/client'
import { fetchChatLogs } from '../../admin/adminApi'
import { getCsvFilename, triggerBlobDownload } from '../../admin/csvDownload'
import { formatDateTime, formatFullDateTime, getApiErrorMessage } from '../../admin/format'
import QuickFaqModal, { type QuickFaqSeed } from '../../components/admin/QuickFaqModal'
import { useAdminConsole } from '../../components/admin/adminConsoleContext'
import {
  EmptyState,
  ErrorNote,
  FilterBar,
  FilterChip,
  LoadingNote,
  PageHeader,
  Panel,
  StatusPill,
} from '../../components/admin/ui'
import type { RagChatLog } from '../../types/admin'

const PAGE_SIZE = 50

const RANGE_OPTIONS = [
  { key: '1', label: '오늘' },
  { key: '7', label: '7일' },
  { key: '30', label: '30일' },
  { key: 'all', label: '전체' },
]

/** 기간 칩 → API에 넘길 ISO 날짜. 'all'이면 조건 없음. */
const rangeToFrom = (range: string): string | undefined => {
  if (range === 'all') return undefined
  const days = Number(range)
  if (!Number.isFinite(days)) return undefined
  const date = new Date()
  date.setDate(date.getDate() - (days - 1))
  date.setHours(0, 0, 0, 0)
  return date.toISOString()
}

const ChatLogPage = () => {
  const { showToast } = useAdminConsole()
  const [searchParams, setSearchParams] = useSearchParams()

  const [logs, setLogs] = useState<RagChatLog[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [hasMore, setHasMore] = useState(false)

  const [range, setRange] = useState('7')
  const [fallbackOnly, setFallbackOnly] = useState(searchParams.get('fallback') === '1')
  const [routeFilter, setRouteFilter] = useState('all')
  const [userFilter, setUserFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)

  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [faqSeed, setFaqSeed] = useState<QuickFaqSeed | null>(null)

  const loadLogs = useCallback(async (targetPage: number) => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchChatLogs({
        limit: PAGE_SIZE,
        offset: targetPage * PAGE_SIZE,
        from: rangeToFrom(range),
        route: routeFilter === 'all' ? undefined : routeFilter,
        fallbackOnly,
        search: search.trim() || undefined,
      })
      if (Array.isArray(data)) {
        setLogs(data)
        // 서버가 요청한 만큼 채워 보냈으면 다음 페이지가 있을 가능성이 있다.
        setHasMore(data.length === PAGE_SIZE)
      } else {
        setLogs([])
        setHasMore(false)
        setError('서버에서 올바르지 않은 데이터를 반환했습니다.')
      }
    } catch (fetchError) {
      setLogs([])
      setHasMore(false)
      setError(getApiErrorMessage(fetchError, '질문 로그를 불러오지 못했습니다.'))
    } finally {
      setLoading(false)
    }
  }, [range, routeFilter, fallbackOnly, search])

  useEffect(() => {
    void loadLogs(page)
  }, [loadLogs, page])

  // 필터가 바뀌면 항상 첫 페이지로 돌아간다.
  useEffect(() => { setPage(0) }, [range, routeFilter, fallbackOnly, search])

  useEffect(() => {
    if (fallbackOnly) searchParams.set('fallback', '1')
    else searchParams.delete('fallback')
    setSearchParams(searchParams, { replace: true })
  }, [fallbackOnly, searchParams, setSearchParams])

  const routeOptions = useMemo(
    () => Array.from(new Set(logs.map((log) => log.route).filter(Boolean))).sort(),
    [logs],
  )
  const userOptions = useMemo(
    () => Array.from(new Set(logs.map((log) => log.username ?? '게스트'))).sort(),
    [logs],
  )

  // 사용자 필터는 서버가 username을 조인해 준 뒤에야 알 수 있어 클라이언트에서 적용한다.
  const visibleLogs = useMemo(
    () => (userFilter === 'all' ? logs : logs.filter((log) => (log.username ?? '게스트') === userFilter)),
    [logs, userFilter],
  )

  const handleExport = async () => {
    setExporting(true)
    try {
      const params = new URLSearchParams({ limit: '1000' })
      const from = rangeToFrom(range)
      if (from) params.set('from', from)
      if (routeFilter !== 'all') params.set('route', routeFilter)
      if (fallbackOnly) params.set('fallback_only', '1')
      if (search.trim()) params.set('search', search.trim())

      const url = resolveApiUrl(`/admin/rag-logs/export?${params.toString()}`)
      const response = await fetch(url, {
        method: 'GET',
        credentials: 'include',
        headers: withNgrokHeader(url, { Accept: 'text/csv' }),
      })

      if (!response.ok) {
        let message = `CSV 내보내기 요청이 실패했습니다. (Status: ${response.status})`
        try {
          const body = await response.json() as { detail?: string; message?: string }
          message = body.detail ?? body.message ?? message
        } catch {
          // JSON 오류 본문이 아닌 경우에는 상태 코드가 포함된 기본 메시지를 사용한다.
        }
        throw new Error(message)
      }

      const blob = await response.blob()
      if (blob.size === 0) {
        throw new Error('서버가 빈 CSV 파일을 반환했습니다.')
      }

      const filename = getCsvFilename(response.headers.get('Content-Disposition'))
      triggerBlobDownload(blob, filename)
      showToast(`${filename} 다운로드를 시작했습니다.`, 'success')
    } catch (exportError) {
      showToast(getApiErrorMessage(exportError, 'CSV 파일을 내려받지 못했습니다.'), 'error')
    } finally {
      setExporting(false)
    }
  }

  return (
    <>
      <PageHeader
        title="대화 로그"
        description="사용자 질문과 챗봇 답변을 확인하고, 부족한 답변은 바로 FAQ로 등록합니다."
        actions={
          <>
            <button type="button" className="ac-btn" onClick={handleExport} disabled={exporting || loading}>
              {exporting ? 'CSV 생성 중...' : '현재 조건 CSV (최대 1,000건)'}
            </button>
            <button type="button" className="ac-btn" onClick={() => { void loadLogs(page) }} disabled={loading}>
              {loading ? '새로고침 중...' : '새로고침'}
            </button>
          </>
        }
      />

      <Panel padded={false}>
        <FilterBar>
          {RANGE_OPTIONS.map((option) => (
            <FilterChip key={option.key} active={range === option.key} onClick={() => setRange(option.key)}>
              {option.label}
            </FilterChip>
          ))}
          <FilterChip active={fallbackOnly} onClick={() => setFallbackOnly((on) => !on)}>
            Fallback만
          </FilterChip>
          <select
            className="ac-select"
            value={routeFilter}
            onChange={(event) => setRouteFilter(event.target.value)}
            aria-label="분류(route) 필터"
          >
            <option value="all">전체 분류</option>
            {routeOptions.map((route) => <option key={route} value={route}>{routeLabel(route)}</option>)}
          </select>
          <select
            className="ac-select"
            value={userFilter}
            onChange={(event) => setUserFilter(event.target.value)}
            aria-label="사용자 필터"
          >
            <option value="all">전체 사용자</option>
            {userOptions.map((username) => <option key={username} value={username}>{username}</option>)}
          </select>
          <input
            type="search"
            className="ac-input ac-input--grow"
            placeholder="질문·답변 내용 검색"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="로그 검색"
          />
        </FilterBar>

        {loading ? (
          <LoadingNote />
        ) : error ? (
          <ErrorNote>{error}</ErrorNote>
        ) : visibleLogs.length === 0 ? (
          <EmptyState>조건에 맞는 질문 로그가 없습니다.</EmptyState>
        ) : (
          <>
            <div className="ac-list" style={{ maxHeight: 'none' }}>
              {visibleLogs.map((log) => {
                const expanded = expandedId === log.id
                return (
                  <div key={log.id} className="ac-list-item" style={{ flexDirection: 'column', alignItems: 'stretch', cursor: 'default' }}>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', marginBottom: '6px' }}>
                      <span className="ac-list-item__meta" title={formatFullDateTime(log.created_at)} style={{ margin: 0 }}>
                        {formatDateTime(log.created_at)}
                      </span>
                      <span className="ac-list-item__meta" style={{ margin: 0 }}>{log.username ?? '게스트'}</span>
                      <StatusPill tone="pending">{routeLabel(log.route)}</StatusPill>
                      {log.fallback_triggered && (
                        <StatusPill tone="danger">Fallback: {log.fallback_reason ?? '사유 미상'}</StatusPill>
                      )}
                      <span className="ac-list-item__meta" style={{ margin: 0 }}>참조 {log.source_count}개</span>
                    </div>

                    {expanded && <details className="ac-cell-details"><summary>기술 정보</summary><p>검색 분류: {log.route}</p></details>}
                    <div className="ac-qa">
                      <div className="ac-qa__row">
                        <span className="ac-qa__tag ac-qa__tag--q">Q.</span>
                        <span className="ac-qa__text">{log.question}</span>
                      </div>
                      <div className="ac-qa__row">
                        <span className="ac-qa__tag ac-qa__tag--a">A.</span>
                        <span className={`ac-qa__text ${expanded ? '' : 'ac-qa__text--clamp'}`}>{log.answer}</span>
                      </div>
                    </div>

                    <div style={{ display: 'flex', gap: '6px', justifyContent: 'flex-end', marginTop: '8px', flexWrap: 'wrap' }}>
                      <button
                        type="button"
                        className="ac-btn ac-btn--sm ac-btn--ghost"
                        onClick={() => setExpandedId(expanded ? null : log.id)}
                        aria-expanded={expanded}
                      >
                        {expanded ? '접기' : '전체 보기'}
                      </button>
                      <button
                        type="button"
                        className="ac-btn ac-btn--sm"
                        onClick={() => setFaqSeed({ question: log.question, answer: log.fallback_triggered ? '' : log.answer })}
                      >
                        FAQ로 등록
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>

            <div className="ac-filterbar" style={{ justifyContent: 'center', borderTop: '1px solid var(--ac-line)', borderBottom: 0 }}>
              <button type="button" className="ac-btn ac-btn--sm" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
                이전
              </button>
              <span className="ac-hint">{page + 1}페이지 · {visibleLogs.length}건 표시</span>
              <button type="button" className="ac-btn ac-btn--sm" onClick={() => setPage((p) => p + 1)} disabled={!hasMore}>
                다음
              </button>
            </div>
          </>
        )}
      </Panel>

      <QuickFaqModal seed={faqSeed} onClose={() => setFaqSeed(null)} />
    </>
  )
}

export default ChatLogPage
