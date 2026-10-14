/**
 * 관리자 콘솔 전역에서 쓰는 표시 형식 헬퍼.
 * 각 페이지가 제각각 formatDateTime을 재정의하던 중복을 한곳으로 모은다.
 */

const KO = 'ko-KR'

/** "7/30 14:32" — 목록·테이블의 기본 시각 표기 */
export const formatDateTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat(KO, {
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

/** "7/30" — 좁은 카드에서 날짜만 필요할 때 */
export const formatDate = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat(KO, { month: 'numeric', day: 'numeric' }).format(date)
}

/** "2026. 7. 30. 오후 2:32" — 상세 화면의 전체 표기 */
export const formatFullDateTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat(KO, {
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

/** "방금 전" / "12분 전" / "3시간 전" — 활동 피드용 */
export const formatRelativeTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  const diffMs = Date.now() - date.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return '방금 전'
  if (diffMin < 60) return `${diffMin}분 전`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour}시간 전`
  const diffDay = Math.floor(diffHour / 24)
  if (diffDay < 7) return `${diffDay}일 전`
  return formatDate(value)
}

/** 접속자 수처럼 자릿수가 큰 값을 천 단위로 끊는다. */
export const formatCount = (value?: number | null, suffix = '') => {
  if (value == null) return '—'
  return `${value.toLocaleString(KO)}${suffix}`
}

/** 0~1 비율을 백분율 문자열로. null이면 '-' */
export const formatPercent = (ratio?: number | null, digits = 0) => {
  if (ratio == null || Number.isNaN(ratio)) return '-'
  return `${(ratio * 100).toFixed(digits)}%`
}

/** 분자/분모 기반 비율 — 분모 0이면 0% 대신 '-' (표본 없음과 0%를 구분) */
export const formatRatio = (numerator?: number | null, denominator?: number | null) => {
  if (numerator == null || denominator == null || denominator === 0) return '-'
  return `${Math.round((numerator / denominator) * 100)}%`
}

export type StatusTone = 'success' | 'warning' | 'danger' | 'pending' | 'neutral'

/** RAG 상태 문자열 → 한글 라벨 */
export const getSystemStatusLabel = (status?: string | null) => {
  if (status === 'ok') return '정상'
  if (status === 'degraded') return '주의'
  if (status === 'error') return '오류'
  return '확인 중'
}

/** RAG 상태 문자열 → 상태 색 토큰 */
export const getSystemStatusTone = (status?: string | null): StatusTone => {
  if (status === 'ok') return 'success'
  if (status === 'degraded') return 'warning'
  if (status === 'error') return 'danger'
  return 'pending'
}

/** 검수/가입 요청 상태 → 한글 라벨 */
export const getRequestStatusLabel = (status?: string | null) => {
  const normalized = (status ?? '').toLowerCase()
  if (normalized === 'approved' || normalized === 'approved_manually') return '승인됨'
  if (normalized === 'rejected') return '반려됨'
  if (normalized === 'pending') return '대기 중'
  return status ?? '알 수 없음'
}

/** 검수/가입 요청 상태 → 상태 색 토큰 */
export const getRequestStatusTone = (status?: string | null): StatusTone => {
  const normalized = (status ?? '').toLowerCase()
  if (normalized === 'approved' || normalized === 'approved_manually') return 'success'
  if (normalized === 'rejected') return 'danger'
  if (normalized === 'pending') return 'pending'
  return 'neutral'
}

export const feedbackReasonLabels: Record<string, string> = {
  inaccurate: '부정확',
  outdated: '오래된 정보',
  no_source: '출처 없음',
  irrelevant: '관련 없음',
  other: '기타',
}

export const getFeedbackReasonLabel = (reason?: string | null) => {
  if (!reason) return '미지정'
  return feedbackReasonLabels[reason] ?? reason
}

export const sourceTypeLabels: Record<string, string> = {
  custom_knowledge: 'FAQ',
  event: '행사',
  announcement: '공지',
  staff_refresh: '교직원 갱신',
}

export const getSourceTypeLabel = (sourceType?: string | null) => {
  if (!sourceType) return '기타'
  return sourceTypeLabels[sourceType] ?? sourceType
}

/**
 * API 오류에서 사용자에게 보여줄 메시지를 뽑는다.
 * 서버는 { message } 또는 { detail } 형태로 응답하므로 둘 다 확인한다.
 */
export const getApiErrorMessage = (error: unknown, fallback: string) => {
  if (error && typeof error === 'object' && 'details' in error) {
    const details = (error as { details?: unknown }).details
    if (details && typeof details === 'object') {
      const record = details as Record<string, unknown>
      const message = record.message ?? record.detail
      if (typeof message === 'string' && message.trim()) {
        return message
      }
    }
  }

  if (error instanceof Error && error.message) {
    return error.message
  }

  return fallback
}
