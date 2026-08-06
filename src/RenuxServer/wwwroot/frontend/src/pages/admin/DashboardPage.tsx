import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  fetchAllItems,
  fetchChatLogs,
  fetchCouncilSignupRequests,
  fetchOrganizations,
  fetchProductKpis,
} from '../../admin/adminApi'
import { toPendingAnswerReview } from '../../admin/adminData'
import {
  formatCount,
  formatDateTime,
  formatRatio,
  formatRelativeTime,
  getRequestStatusLabel,
  getRequestStatusTone,
} from '../../admin/format'
import TrendChart, { type TrendPoint } from '../../components/admin/TrendChart'
import { useAdminConsole } from '../../components/admin/adminConsoleContext'
import {
  EmptyState,
  ErrorNote,
  LoadingNote,
  MetricCard,
  PageHeader,
  Panel,
  StatusPill,
} from '../../components/admin/ui'
import type { AdminActivityEntry, ProductKpiReport, RagChatLog } from '../../types/admin'

/** 추이 차트가 다루는 기간. 로그 조회량과 그래프 가독성의 절충. */
const TREND_DAYS = 14
const TREND_LOG_LIMIT = 1000

const dayKey = (value: string | null | undefined) => {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  // 서비스 기준 시간대(KST)로 하루를 끊는다.
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(date)
}

const buildTrend = (logs: RagChatLog[]): TrendPoint[] => {
  const counts = new Map<string, { total: number; fallback: number }>()
  const today = new Date()

  for (let offset = TREND_DAYS - 1; offset >= 0; offset -= 1) {
    const date = new Date(today)
    date.setDate(date.getDate() - offset)
    const key = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(date)
    counts.set(key, { total: 0, fallback: 0 })
  }

  logs.forEach((log) => {
    const key = dayKey(log.created_at)
    if (!key) return
    const bucket = counts.get(key)
    if (!bucket) return
    bucket.total += 1
    if (log.fallback_triggered) bucket.fallback += 1
  })

  return Array.from(counts.entries()).map(([key, bucket]) => ({
    label: key.slice(5).replace('-', '/'),
    value: bucket.total,
    // 답변 성공률(= 100 - fallback 비율)을 보조 계열로 겹쳐 본다.
    secondary: bucket.total > 0 ? Math.round(((bucket.total - bucket.fallback) / bucket.total) * 100) : null,
  }))
}

const DashboardPage = () => {
  const navigate = useNavigate()
  const {
    ragStatus,
    ragStatusError,
    pendingReviewCount,
    pendingSignupCount,
    dataVersion,
  } = useAdminConsole()

  const [organizationCount, setOrganizationCount] = useState<number | null>(null)
  const [activity, setActivity] = useState<AdminActivityEntry[]>([])
  const [activityError, setActivityError] = useState<string | null>(null)
  const [trend, setTrend] = useState<TrendPoint[]>([])
  const [trendError, setTrendError] = useState<string | null>(null)
  const [kpis, setKpis] = useState<ProductKpiReport | null>(null)
  const [kpiError, setKpiError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const loadDashboard = useCallback(async () => {
    setLoading(true)
    const [orgsResult, itemsResult, signupResult, logsResult, kpiResult] = await Promise.allSettled([
      fetchOrganizations(),
      fetchAllItems(),
      fetchCouncilSignupRequests(),
      fetchChatLogs({ limit: TREND_LOG_LIMIT }),
      fetchProductKpis(),
    ])

    setOrganizationCount(orgsResult.status === 'fulfilled' ? orgsResult.value.length : null)

    const entries: AdminActivityEntry[] = []
    if (itemsResult.status === 'fulfilled' && Array.isArray(itemsResult.value)) {
      itemsResult.value.map(toPendingAnswerReview).forEach((review) => {
        entries.push({
          id: `item-${review.id}`,
          kind: review.status === 'pending'
            ? 'submitted'
            : review.status === 'rejected' ? 'rejected' : 'approved',
          title: review.question,
          meta: `${review.departmentName} · ${review.handler}`,
          occurredAt: review.reviewedAt ?? review.submittedAt,
          status: review.status,
          href: '/admin/review',
        })
      })
      setActivityError(null)
    } else {
      setActivityError('최근 활동을 불러오지 못했습니다.')
    }

    if (signupResult.status === 'fulfilled' && Array.isArray(signupResult.value)) {
      signupResult.value.forEach((request) => {
        entries.push({
          id: `signup-${request.id}`,
          kind: 'signup',
          title: `${request.username} 학생회 가입 요청`,
          meta: request.majorName ?? '학과 미상',
          occurredAt: request.reviewedTime ?? request.createdTime,
          status: request.status,
          href: '/admin/users',
        })
      })
    }

    entries.sort((a, b) => new Date(b.occurredAt).getTime() - new Date(a.occurredAt).getTime())
    setActivity(entries.slice(0, 8))

    if (logsResult.status === 'fulfilled' && Array.isArray(logsResult.value)) {
      setTrend(buildTrend(logsResult.value))
      setTrendError(null)
    } else {
      setTrend([])
      setTrendError('질문 추이를 불러오지 못했습니다.')
    }

    if (kpiResult.status === 'fulfilled') {
      setKpis(kpiResult.value)
      setKpiError(null)
    } else {
      setKpis(null)
      setKpiError('프로덕트 지표를 불러오지 못했습니다.')
    }

    setLoading(false)
  }, [])

  useEffect(() => {
    void loadDashboard()
  }, [loadDashboard, dataVersion])

  const fallbackRate = useMemo(() => {
    if (!ragStatus || ragStatus.rag_logs.total_queries === 0) return null
    return Math.round((ragStatus.rag_logs.fallback_count / ragStatus.rag_logs.total_queries) * 100)
  }, [ragStatus])

  const satisfaction = ragStatus?.feedback?.satisfaction
  const fallbackReasons = ragStatus?.rag_logs.fallback_reasons ?? {}

  // 품질 지표는 누적이 아니라 최근 기간의 실제 사용자 트래픽만 센다. 기간을 밝히지
  // 않으면 누적으로 오해하고, 그러면 기능이 없던 시절 데이터가 최근 개선을 덮는다.
  const windowLabel = ragStatus?.rag_logs.window_days
    ? `최근 ${ragStatus.rag_logs.window_days}일`
    : undefined

  const metricsScope = useMemo(() => {
    if (!ragStatus) return undefined
    const windowDays = ragStatus.rag_logs.window_days
    const synthetic = ragStatus.rag_logs.synthetic_query_count
    const parts = [
      windowDays ? `최근 ${windowDays}일 · 실제 사용자 질문 기준` : '실제 사용자 질문 기준',
    ]
    if (synthetic != null && synthetic > 0) {
      parts.push(`평가 실행 ${synthetic.toLocaleString('ko-KR')}건 제외`)
    }
    parts.push(`갱신 ${formatDateTime(ragStatus.generated_at)}`)
    return parts.join(' · ')
  }, [ragStatus])

  return (
    <>
      <PageHeader
        title="대시보드"
        description="오늘 처리할 일과 서비스 상태를 한 화면에서 확인합니다."
      />

      <div className="ac-metrics">
        <MetricCard
          label="검수 대기"
          value={`${pendingReviewCount}건`}
          hint="검수함으로 이동 →"
          emphasis={pendingReviewCount > 0}
          onClick={() => navigate('/admin/review')}
        />
        <MetricCard
          label="가입 승인 대기"
          value={`${pendingSignupCount}건`}
          hint="사용자·조직으로 이동 →"
          emphasis={pendingSignupCount > 0}
          onClick={() => navigate('/admin/users?tab=signup')}
        />
        <MetricCard
          label="등록된 조직"
          value={organizationCount == null ? '—' : `${organizationCount}개`}
          onClick={() => navigate('/admin/users?tab=orgs')}
        />
      </div>

      <Panel title="핵심 지표" description={metricsScope}>
        {ragStatusError ? (
          <ErrorNote>{ragStatusError}</ErrorNote>
        ) : (
          <div className="ac-metrics">
            <MetricCard label="오늘 접속자" value={formatCount(ragStatus?.visitor_stats?.today, '명')} />
            {/* 접속자 누계는 성격상 누적이라 패널 헤더의 기간과 다르다. 카드에 명시한다. */}
            <MetricCard
              label="누적 접속자"
              value={formatCount(ragStatus?.visitor_stats?.total, '명')}
              hint="전체 기간"
            />
            <MetricCard
              label="질문 수"
              value={formatCount(ragStatus?.rag_logs.total_queries)}
              hint={windowLabel ? `${windowLabel} · 대화 로그 보기 →` : '대화 로그 보기 →'}
              onClick={() => navigate('/admin/logs')}
            />
            <MetricCard
              label="만족도"
              value={satisfaction == null ? '-' : `${Math.round(satisfaction * 100)}%`}
              hint={`👍 ${ragStatus?.feedback?.up ?? 0} · 👎 ${ragStatus?.feedback?.down ?? 0}`}
              onClick={() => navigate('/admin/feedback')}
            />
            <MetricCard
              label="Fallback 비율"
              value={fallbackRate == null ? '-' : `${fallbackRate}%`}
              hint={`${ragStatus?.rag_logs.fallback_count ?? 0}건`}
              tone={fallbackRate != null && fallbackRate >= 20 ? 'warning' : undefined}
              onClick={() => navigate('/admin/logs?fallback=1')}
            />
            <MetricCard
              label="도움 답변율"
              value={kpis ? formatRatio(kpis.helpfulAnswerRate.numerator, kpis.helpfulAnswerRate.denominator) : '-'}
              hint={kpis ? `${kpis.helpfulAnswerRate.numerator}/${kpis.helpfulAnswerRate.denominator}` : kpiError ?? '집계 중'}
            />
            <MetricCard
              label="7일 재사용률"
              value={kpis ? formatRatio(kpis.sevenDayValidReuseRate.numerator, kpis.sevenDayValidReuseRate.denominator) : '-'}
              hint={kpis ? `${kpis.sevenDayValidReuseRate.numerator}/${kpis.sevenDayValidReuseRate.denominator}` : kpiError ?? '집계 중'}
            />
            <MetricCard
              label="최근 질문"
              value={formatDateTime(ragStatus?.rag_logs.latest_query_at)}
            />
          </div>
        )}
      </Panel>

      {Object.keys(fallbackReasons).length > 0 && (
        <Panel title="Fallback 사유" description="답변을 만들지 못한 이유별 집계입니다.">
          <div className="ac-metrics">
            {Object.entries(fallbackReasons).map(([reason, count]) => (
              <MetricCard key={reason} label={reason} value={count} />
            ))}
          </div>
        </Panel>
      )}

      <div className="ac-split">
        <Panel
          title={`질문 추이 (최근 ${TREND_DAYS}일)`}
          description="막대 대신 면적으로 질문량을, 파란 선으로 답변 성공률을 겹쳐 봅니다."
        >
          {loading ? (
            <LoadingNote />
          ) : trendError ? (
            <ErrorNote>{trendError}</ErrorNote>
          ) : (
            <>
              <TrendChart points={trend} ariaLabel={`최근 ${TREND_DAYS}일 질문량과 답변 성공률 추이`} />
              <div className="ac-legend" style={{ marginTop: '8px' }}>
                <span className="ac-legend__key"><i className="ac-legend__swatch" />질문 수</span>
                <span className="ac-legend__key"><i className="ac-legend__swatch ac-legend__swatch--alt" />답변 성공률 (%)</span>
              </div>
            </>
          )}
        </Panel>

        <Panel title="최근 활동" description="제출·승인·반려·가입 요청 타임라인">
          {loading ? (
            <LoadingNote />
          ) : activityError ? (
            <ErrorNote>{activityError}</ErrorNote>
          ) : activity.length === 0 ? (
            <EmptyState>최근 활동이 없습니다.</EmptyState>
          ) : (
            <div className="ac-feed">
              {activity.map((entry) => (
                <div key={entry.id} className="ac-feed__item">
                  <StatusPill tone={getRequestStatusTone(entry.status)}>
                    {getRequestStatusLabel(entry.status)}
                  </StatusPill>
                  <button
                    type="button"
                    className="ac-feed__title ac-btn ac-btn--ghost"
                    style={{ justifyContent: 'flex-start', padding: '0 4px' }}
                    onClick={() => entry.href && navigate(entry.href)}
                  >
                    {entry.title}
                  </button>
                  <span className="ac-feed__meta">{entry.meta} · {formatRelativeTime(entry.occurredAt)}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </>
  )
}

export default DashboardPage
