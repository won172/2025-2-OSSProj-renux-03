import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { CouncilOrganization, PendingAnswerReview, RagFeedbackItem } from '../../types/admin'
import { apiFetch } from '../../api/client'

// Interfaces for API responses
interface ApiPendingItem {
  id: number
  source_type: string
  data: string // JSON string
  status: string
  created_at: string
}

interface ApiOrganization {
    id: string
    major: {
        id: string
        majorname?: string
        Majorname?: string
    }
    managerName?: string
    ManagerName?: string
}

interface CouncilSignupRequest {
  id: string
  userId: string
  username: string
  majorId: string
  majorName?: string | null
  status: string
  createdTime: string
  reviewedTime?: string | null
  reviewNote?: string | null
}

interface ApiMessageResponse {
  message?: string
}

interface MajorOption {
  id: string
  majorname?: string
  Majorname?: string
}

interface AdminRoleOption {
  id: string
  roleName: string
}

interface AdminUserAccount {
  id: string
  userId: string
  username: string
  majorId: string
  majorName?: string | null
  roleId: string
  roleName?: string | null
  createdTime: string
  updatedTime: string
}

interface ApiRagFeedbackItem {
  id: number
  rating: number
  reason: string | null
  comment: string | null
  major: string | null
  created_at: string | null
  question: string | null
  answer: string | null
}

interface RagDatasetStatus {
  key: string
  collection: string
  chroma_count: number | null
  cached_chunk_count: number
  chunk_artifact_exists: boolean
  chunk_artifact_mtime: string | null
  latest_document_published_at?: string | null
  vectorizer_exists: boolean
  vectorizer_mtime: string | null
  last_successful_indexed_at?: string | null
  vectorizer_sklearn_version?: string | null
  status: 'ok' | 'degraded' | 'error'
  error?: string | null
}

interface RagAdminStatus {
  status: 'ok' | 'degraded' | 'error'
  generated_at: string
  datasets: RagDatasetStatus[]
  pending_items: {
    pending: number
    approved: number
    rejected: number
  }
  rag_logs: {
    total_queries: number
    fallback_count: number
    latest_query_at: string | null
    fallback_reasons?: Record<string, number>
  }
  feedback?: {
    total: number
    up: number
    down: number
    satisfaction: number | null
    downReasons?: Record<string, number>
    down_reasons?: Record<string, number>
  }
  notices_ingestion?: {
    last_collection_at: string | null
    last_successful_ingestion_at: string | null
    ingestion_summary: {
      status: string | null
      documents_seen: number
      documents_new: number
      documents_updated: number
      documents_deleted: number
      documents_failed: number
    }
    stage_summary: {
      raw_documents: number
      normalized_documents: number
      indexed_documents: number
    }
    quality_summary: {
      parse_failed: number
      severities: Record<string, number>
      recent_checks: Array<{
        document_key: string
        check_type: string
        severity: string
        message: string
        created_at: string
      }>
    }
  }
  error?: string
}

const getStatusLabel = (status?: string) => {
  if (status === 'ok') return '정상'
  if (status === 'degraded') return '주의'
  if (status === 'error') return '오류'
  return '확인 중'
}

const getStatusPillClass = (status?: string) => {
  if (status === 'ok') return 'success'
  if (status === 'error') return 'danger'
  return 'pending'
}

const getRequestStatusLabel = (status?: string) => {
  if (status === 'approved') return '승인됨'
  if (status === 'rejected') return '반려됨'
  return '대기 중'
}

const getRequestStatusPillClass = (status?: string) => {
  if (status === 'approved') return 'success'
  if (status === 'rejected') return 'danger'
  return 'pending'
}

const getApiErrorMessage = (error: unknown, fallback: string) => {
  if (error && typeof error === 'object' && 'details' in error) {
    const details = (error as { details?: unknown }).details
    if (details && typeof details === 'object' && 'message' in details) {
      return String((details as ApiMessageResponse).message)
    }
  }

  if (error instanceof Error) {
    return error.message
  }

  return fallback
}

const formatDateTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

const feedbackReasonLabels: Record<string, string> = {
  inaccurate: '부정확',
  outdated: '오래된 정보',
  no_source: '출처 없음',
  irrelevant: '관련 없음',
  other: '기타',
}

const getFeedbackReasonLabel = (reason?: string | null) => {
  if (!reason) return '미지정'
  return feedbackReasonLabels[reason] ?? reason
}

const UniversityAdminPage = () => {
  const navigate = useNavigate()
  const [organizations, setOrganizations] = useState<CouncilOrganization[]>([])
  const [pendingReviews, setPendingReviews] = useState<PendingAnswerReview[]>([])
  const [selectedReviewId, setSelectedReviewId] = useState<string | null>(null)
  const [councilSignupRequests, setCouncilSignupRequests] = useState<CouncilSignupRequest[]>([])
  const [councilSignupError, setCouncilSignupError] = useState<string | null>(null)
  const [accountUsers, setAccountUsers] = useState<AdminUserAccount[]>([])
  const [accountRoles, setAccountRoles] = useState<AdminRoleOption[]>([])
  const [accountMajors, setAccountMajors] = useState<MajorOption[]>([])
  const [accountError, setAccountError] = useState<string | null>(null)
  const [accountSearchTerm, setAccountSearchTerm] = useState('')
  const [accountRoleFilter, setAccountRoleFilter] = useState('all')
  const [accountMajorFilter, setAccountMajorFilter] = useState('all')
  const [pendingReviewsError, setPendingReviewsError] = useState<string | null>(null)
  
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [ragStatus, setRagStatus] = useState<RagAdminStatus | null>(null)
  const [ragStatusError, setRagStatusError] = useState<string | null>(null)
  const [negativeFeedback, setNegativeFeedback] = useState<RagFeedbackItem[]>([])
  const [feedbackLoading, setFeedbackLoading] = useState(false)
  const [feedbackError, setFeedbackError] = useState<string | null>(null)

  const [actionNotice, setActionNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  // 성공 알림 자동 소거: 3초 후 사라지도록
  useEffect(() => {
    if (!actionNotice) return
    const timer = setTimeout(() => setActionNotice(null), 3000)
    return () => clearTimeout(timer)
  }, [actionNotice])

  // 선택 항목 변경 시 이전 알림 소거
  useEffect(() => {
    setActionNotice(null)
    setActionError(null)
  }, [selectedReviewId])

  // Fetch Data — 새로고침 버튼에서도 재사용할 수 있도록 useEffect 밖으로 분리
  const fetchData = useCallback(async () => {
        setLoading(true)
        setFeedbackLoading(true)
        setError(null)
        try {
            // 1. Fetch Organizations
            try {
                const orgsData = await apiFetch<ApiOrganization[]>('/req/orgs')
                if (Array.isArray(orgsData)) {
                    const mappedOrgs: CouncilOrganization[] = orgsData.map(org => ({
                        id: org.id,
                        name: `${org.major.majorname || org.major.Majorname || '알수없음'} 학생회`,
                        manager: org.managerName || org.ManagerName || '-', 
                        updatedAt: new Date().toISOString().split('T')[0], 
                        status: '활성',
                        pendingRequests: 0 
                    }))
                    setOrganizations(mappedOrgs)
                }
            } catch (e) {
                console.error('Failed to fetch orgs:', e)
                // Don't set global error for orgs fetch failure, it might not be critical
            }

            // 2. Fetch Pending Reviews (ALL items for history)
            try {
                const pendingData = await apiFetch<ApiPendingItem[]>(`/admin/items?t=${new Date().getTime()}`)
                if (Array.isArray(pendingData)) {
                    const mappedReviews: PendingAnswerReview[] = pendingData
                        .map(item => {
                            let title = '제목 없음';
                            let content = '';
                            let category = '공통';
                            let parsedData: Record<string, string | undefined> = {};
                            
                            try {
                                parsedData = JSON.parse(item.data);
                                
                                if (item.source_type === 'custom_knowledge') {
                                    title = parsedData.question || '질문 없음';
                                    content = parsedData.answer || '';
                                    category = parsedData.category || '공통';
                                } else if (item.source_type === 'event') {
                                    title = `[행사] ${parsedData.title || ''}`;
                                    content = `일시: ${parsedData.start_date} ~ ${parsedData.end_date}\n장소: ${parsedData.location}\n\n${parsedData.description}`;
                                    category = parsedData.department || '공통';
                                } else if (item.source_type === 'announcement') {
                                    title = `[공지] ${parsedData.title || ''}`;
                                    content = `게시일: ${parsedData.date}\n분류: ${parsedData.category}\n\n${parsedData.content}`;
                                    category = parsedData.department || '공통';
                                }
                            } catch (e) { 
                                console.error('JSON parse error for item data:', item.data, e);
                            }
                            
                            return {
                                id: item.id.toString(),
                                departmentName: category,
                                submittedAt: item.created_at,
                                handler: parsedData.requester || '정보 없음', // Name or 'Info Missing'
                                question: title,
                                answer: content,
                                status: item.status 
                            }
                        })
                    // Sort: Pending first, then by date desc
                    mappedReviews.sort((a, b) => {
                        const isAPending = a.status === 'pending';
                        const isBPending = b.status === 'pending';
                        if (isAPending && !isBPending) return -1;
                        if (!isAPending && isBPending) return 1;
                        return new Date(b.submittedAt).getTime() - new Date(a.submittedAt).getTime();
                    });
                    
                    setPendingReviews(mappedReviews)
                    setPendingReviewsError(null)
                    if (mappedReviews.length > 0) {
                        // Keep selection if exists, else select first
                        setSelectedReviewId((prev) => prev ?? mappedReviews[0].id)
                    } else {
                        setSelectedReviewId(null)
                    }
                }
            } catch (e) {
                console.error('Failed to fetch pending reviews:', e);
                setPendingReviews([])
                setSelectedReviewId(null)
                setPendingReviewsError('검수 대기 데이터를 불러오는데 실패했습니다.')
            }

            // 3. Fetch Council signup requests
            try {
                const requests = await apiFetch<CouncilSignupRequest[]>(`/auth/council-signup-requests?t=${new Date().getTime()}`)
                if (Array.isArray(requests)) {
                    const sortedRequests = [...requests].sort((a, b) => {
                        const isAPending = a.status === 'pending'
                        const isBPending = b.status === 'pending'
                        if (isAPending && !isBPending) return -1
                        if (!isAPending && isBPending) return 1
                        return new Date(b.createdTime).getTime() - new Date(a.createdTime).getTime()
                    })
                    setCouncilSignupRequests(sortedRequests)
                    setCouncilSignupError(null)
                }
            } catch (e) {
                console.error('Failed to fetch council signup requests:', e)
                setCouncilSignupRequests([])
                setCouncilSignupError('학생회 가입 요청을 불러오지 못했습니다.')
            }

            // 4. Fetch Account management data
            try {
                const [users, roles, majors] = await Promise.all([
                    apiFetch<AdminUserAccount[]>(`/auth/admin/users?t=${new Date().getTime()}`),
                    apiFetch<AdminRoleOption[]>(`/auth/admin/users/roles?t=${new Date().getTime()}`),
                    apiFetch<MajorOption[]>('/req/major'),
                ])

                setAccountUsers(Array.isArray(users) ? users : [])
                setAccountRoles(Array.isArray(roles) ? roles : [])
                setAccountMajors(Array.isArray(majors) ? majors : [])
                setAccountError(null)
            } catch (e) {
                console.error('Failed to fetch account users:', e)
                setAccountUsers([])
                setAccountError('계정 관리는 관리자 계정만 사용할 수 있습니다.')
            }

            // 5. Fetch RAG operation status
            try {
                const statusData = await apiFetch<RagAdminStatus>(`/admin/rag/status?t=${new Date().getTime()}`)
                setRagStatus(statusData)
                setRagStatusError(null)
            } catch (e) {
                console.error('Failed to fetch RAG status:', e);
                setRagStatus(null)
                setRagStatusError('RAG 운영 상태를 불러오지 못했습니다.')
            }

            // 6. Fetch recent negative RAG feedback
            try {
                const feedbackData = await apiFetch<ApiRagFeedbackItem[]>(`/admin/rag-feedback?rating=-1&limit=50&t=${new Date().getTime()}`)
                const mappedFeedback: RagFeedbackItem[] = Array.isArray(feedbackData)
                    ? feedbackData.map((item) => ({
                        id: item.id,
                        rating: item.rating,
                        reason: item.reason,
                        comment: item.comment,
                        major: item.major,
                        createdAt: item.created_at,
                        question: item.question,
                        answer: item.answer,
                    }))
                    : []
                setNegativeFeedback(mappedFeedback)
                setFeedbackError(null)
            } catch (e) {
                console.error('Failed to fetch RAG feedback:', e)
                setNegativeFeedback([])
                setFeedbackError('사용자 피드백을 불러오지 못했습니다.')
            } finally {
                setFeedbackLoading(false)
            }

        } catch (e) { 
            console.error('An unexpected error occurred during admin data fetch:', e);
            setError('관리자 데이터를 불러오는 중 예상치 못한 오류가 발생했습니다.');
        } finally {
            setLoading(false)
            setFeedbackLoading(false)
        }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleRefresh = async () => {
    setRefreshing(true)
    setActionNotice(null)
    setActionError(null)
    try {
      await fetchData()
    } finally {
      setRefreshing(false)
    }
  }

  const selectedReview = useMemo(
    () => pendingReviews.find((review) => review.id === selectedReviewId) ?? null,
    [pendingReviews, selectedReviewId],
  )

  const filteredUsers = useMemo(() => {
    const normalizedSearch = accountSearchTerm.trim().toLowerCase()

    return accountUsers.filter((user) => {
      const matchesSearch = !normalizedSearch
        || user.username.toLowerCase().includes(normalizedSearch)
        || user.userId.toLowerCase().includes(normalizedSearch)

      const matchesRole = accountRoleFilter === 'all' || user.roleId === accountRoleFilter
      const matchesMajor = accountMajorFilter === 'all' || user.majorId === accountMajorFilter

      return matchesSearch && matchesRole && matchesMajor
    })
  }, [accountMajorFilter, accountRoleFilter, accountSearchTerm, accountUsers])

  const registeredCount = organizations.length
  const pendingCount = pendingReviews.filter(r => r.status === 'pending').length
  const councilSignupPendingCount = councilSignupRequests.filter((request) => request.status === 'pending').length
  const systemStatus = ragStatus?.status ?? (ragStatusError ? 'error' : undefined)
  const systemStatusLabel = getStatusLabel(systemStatus)
  const fallbackRate = ragStatus && ragStatus.rag_logs.total_queries > 0
    ? Math.round((ragStatus.rag_logs.fallback_count / ragStatus.rag_logs.total_queries) * 100)
    : 0
  const feedbackSummary = ragStatus?.feedback
  const satisfactionPercent = feedbackSummary?.satisfaction == null
    ? null
    : Math.round(feedbackSummary.satisfaction * 100)
  const downReasons = feedbackSummary?.down_reasons ?? feedbackSummary?.downReasons ?? {}

  const handleNavigateHome = () => navigate('/')

  const handleCouncilSignupAction = async (requestId: string, action: 'approve' | 'reject') => {
    if (!confirm(`학생회 가입 요청을 ${action === 'approve' ? '승인' : '반려'}하시겠습니까?`)) {
      return
    }

    setActionNotice(null)
    setActionError(null)
    try {
      const result = await apiFetch<ApiMessageResponse>(`/auth/council-signup-requests/${requestId}/${action}`, {
        method: 'POST',
        json: { note: null },
      })

      setCouncilSignupRequests((prev) =>
        prev.map((request) =>
          request.id === requestId
            ? {
                ...request,
                status: action === 'approve' ? 'approved' : 'rejected',
                reviewedTime: new Date().toISOString(),
              }
            : request,
        ),
      )
      setActionNotice(result.message ?? `학생회 가입 요청을 ${action === 'approve' ? '승인' : '반려'}했습니다.`)
    } catch (e) {
      console.error('Council signup action failed:', e)
      setActionError(getApiErrorMessage(e, '학생회 가입 요청 처리에 실패했습니다.'))
    }
  }

  const handleAccountUpdate = async (
    targetUserId: string,
    update: { majorId?: string; roleId?: string },
  ) => {
    setActionNotice(null)
    setActionError(null)
    try {
      const result = await apiFetch<ApiMessageResponse>(`/auth/admin/users/${targetUserId}`, {
        method: 'PATCH',
        json: update,
      })

      setAccountUsers((prev) =>
        prev.map((user) => {
          if (user.id !== targetUserId) return user

          const nextMajor = update.majorId
            ? accountMajors.find((major) => major.id === update.majorId)
            : null
          const nextRole = update.roleId
            ? accountRoles.find((role) => role.id === update.roleId)
            : null

          return {
            ...user,
            majorId: update.majorId ?? user.majorId,
            majorName: nextMajor ? (nextMajor.majorname ?? nextMajor.Majorname ?? user.majorName) : user.majorName,
            roleId: update.roleId ?? user.roleId,
            roleName: nextRole ? nextRole.roleName : user.roleName,
            updatedTime: new Date().toISOString(),
          }
        }),
      )
      setActionNotice(result.message ?? '계정 정보가 수정되었습니다.')
    } catch (e) {
      console.error('Account update failed:', e)
      setActionError(getApiErrorMessage(e, '계정 정보 수정에 실패했습니다.'))
    }
  }

  const handlePasswordReset = async (targetUserId: string, displayUserId: string) => {
    const password = prompt(`${displayUserId} 계정의 새 비밀번호를 입력하세요. (10~30자)`)
    if (!password) return

    setActionNotice(null)
    setActionError(null)
    try {
      const result = await apiFetch<ApiMessageResponse>(`/auth/admin/users/${targetUserId}/reset-password`, {
        method: 'POST',
        json: { password },
      })
      setActionNotice(result.message ?? '비밀번호가 재설정되었습니다.')
    } catch (e) {
      console.error('Password reset failed:', e)
      setActionError(getApiErrorMessage(e, '비밀번호 재설정에 실패했습니다.'))
    }
  }

  const handleAccountDelete = async (targetUserId: string, displayUserId: string) => {
    if (!confirm(`${displayUserId} 계정을 삭제하시겠습니까? 삭제된 계정은 복구할 수 없습니다.`)) {
      return
    }

    setActionNotice(null)
    setActionError(null)
    try {
      await apiFetch(`/auth/admin/users/${targetUserId}`, {
        method: 'DELETE',
      })
      setAccountUsers((prev) => prev.filter((user) => user.id !== targetUserId))
      setActionNotice(`${displayUserId} 계정을 삭제했습니다.`)
    } catch (e) {
      console.error('Account delete failed:', e)
      setActionError(getApiErrorMessage(e, '계정 삭제에 실패했습니다.'))
    }
  }

  const handleReviewAction = async (reviewId: string, action: 'approve' | 'reject') => {
    if (!confirm(`${action === 'approve' ? '승인' : '반려'} 하시겠습니까?`)) {
      return;
    }

    setActionNotice(null)
    setActionError(null)
    try {
        await apiFetch(`/admin/${action}/${reviewId}`, { method: 'POST' })

        // Update UI: update status instead of removing
        setPendingReviews((prev) => prev.map((review) =>
            review.id === reviewId ? { ...review, status: action === 'approve' ? 'approved' : 'rejected' } : review
        ))

        // alert() 대신 인라인 알림 — 블로킹 없이 결과 확인 가능
        setActionNotice(`검수 내역을 ${action === 'approve' ? '승인' : '반려'}했습니다.`)
    } catch (e) {
        console.error('Action failed:', e);
        let message = '요청 처리에 실패했습니다.';
        if (e instanceof Error) {
            message += ` (${e.message})`;
            const status = (e as Error & { status?: number }).status;
            if (status) message += ` [Status: ${status}]`;
        }
        // setError는 전체 페이지를 에러 화면으로 교체하므로 액션 실패는 인라인으로만 표시
        setActionError(message)
    }
  }

  if (loading) {
    return (
      <div className="admin-page-wrapper">
        <div className="admin-shell compact-mode">
          <header className="admin-header glass-panel compact">
            <h1 className="admin-title compact">관리자 제어 센터</h1>
          </header>
          <section className="admin-metrics compact">로딩 중...</section>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="admin-page-wrapper">
        <div className="admin-shell compact-mode">
          <header className="admin-header glass-panel compact">
            <h1 className="admin-title compact">관리자 제어 센터</h1>
          </header>
          <section className="admin-metrics compact" style={{color: 'red'}}>오류: {error}</section>
        </div>
      </div>
    );
  }

  return (
    <div className="admin-page-wrapper">
      <div className="admin-shell compact-mode">
        <header className="admin-header glass-panel compact">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
            <div>
              <p className="admin-eyebrow">ADMINISTRATION</p>
              <h1 className="admin-title compact">관리자 제어 센터</h1>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button className="ghost-btn" type="button" onClick={handleRefresh} disabled={refreshing}>
                {refreshing ? '새로고침 중...' : '새로고침'}
              </button>
              <button className="hero-btn hero-btn--primary" type="button" onClick={handleNavigateHome}>
                메인페이지로 이동
              </button>
            </div>
          </div>
        </header>

        <section className="admin-metrics compact">
          <div className="admin-metrics" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
            <article className="admin-card admin-card--accent admin-card--compact">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                <div>
                    <p className="admin-card__label">등록된 조직</p>
                    <strong className="admin-card__value">{registeredCount}</strong>
                </div>
                <span className="admin-card__icon" aria-hidden="true">👥</span>
              </div>
            </article>
            <article className="admin-card admin-card--compact">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                <div>
                    <p className="admin-card__label">RAG 검수 대기</p>
                    <strong className="admin-card__value">{pendingCount}</strong>
                </div>
                <span className="admin-card__icon admin-card__icon--blue" aria-hidden="true">!</span>
              </div>
            </article>
            <article className="admin-card admin-card--compact">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                <div>
                    <p className="admin-card__label">가입 승인 대기</p>
                    <strong className="admin-card__value">{councilSignupPendingCount}</strong>
                </div>
                <span className="admin-card__icon admin-card__icon--blue" aria-hidden="true">+</span>
              </div>
            </article>
            <article className="admin-card admin-card--compact">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                <div>
                    <p className="admin-card__label">시스템 상태</p>
                    <strong className="admin-card__value">{systemStatusLabel}</strong>
                </div>
                <span className={`admin-card__icon ${systemStatus === 'ok' ? 'admin-card__icon--green' : 'admin-card__icon--blue'}`} aria-hidden="true">
                  {systemStatus === 'ok' ? '●' : '!'}
                </span>
              </div>
            </article>
          </div>
        </section>

        <section className="admin-panel glass-panel" style={{ marginBottom: '16px' }}>
          <header className="admin-panel__header">
            <div>
              <h2 className="admin-panel__title">학생회 가입 요청</h2>
              <p className="admin-panel__subtitle">학생회 계정 신청을 확인하고 승인 또는 반려합니다.</p>
            </div>
            <span className="status-pill status-pill--pending">
              대기 {councilSignupPendingCount}
            </span>
          </header>

          {councilSignupError ? (
            <div className="admin-alert admin-alert--danger" style={{ marginTop: '14px' }}>{councilSignupError}</div>
          ) : (
            <div className="admin-table" style={{ marginTop: '14px', overflowX: 'auto' }}>
              <div style={{ minWidth: '760px' }}>
                <div className="admin-table__head" style={{ gridTemplateColumns: '1fr 1fr 1.4fr 1fr 0.8fr 1.2fr' }}>
                  <span>아이디</span>
                  <span>이름</span>
                  <span>전공</span>
                  <span>신청일</span>
                  <span>상태</span>
                  <span>처리</span>
                </div>
                <ul className="admin-table__body">
                  {councilSignupRequests.map((request) => (
                    <li
                      key={request.id}
                      className="admin-table__row"
                      style={{ gridTemplateColumns: '1fr 1fr 1.4fr 1fr 0.8fr 1.2fr' }}
                    >
                      <span>{request.userId}</span>
                      <span>{request.username}</span>
                      <span>{request.majorName ?? '-'}</span>
                      <span>{formatDateTime(request.createdTime)}</span>
                      <span className={`status-pill status-pill--${getRequestStatusPillClass(request.status)}`}>
                        {getRequestStatusLabel(request.status)}
                      </span>
                      <span style={{ display: 'flex', gap: '8px', justifyContent: 'flex-start', flexWrap: 'wrap' }}>
                        {request.status === 'pending' ? (
                          <>
                            <button
                              className="ghost-btn ghost-btn--muted small"
                              type="button"
                              onClick={() => handleCouncilSignupAction(request.id, 'reject')}
                            >
                              반려
                            </button>
                            <button
                              className="ghost-btn ghost-btn--accent small"
                              type="button"
                              onClick={() => handleCouncilSignupAction(request.id, 'approve')}
                            >
                              승인
                            </button>
                          </>
                        ) : (
                          <span style={{ color: 'var(--buddy-muted)', fontSize: '0.85rem' }}>
                            {request.reviewedTime ? `${formatDateTime(request.reviewedTime)} 처리` : '처리 완료'}
                          </span>
                        )}
                      </span>
                    </li>
                  ))}
                  {councilSignupRequests.length === 0 && (
                    <li className="admin-table__empty">학생회 가입 요청이 없습니다.</li>
                  )}
                </ul>
              </div>
            </div>
          )}

          {(actionNotice || actionError) && (
            <div style={{ marginTop: '12px' }}>
              {actionNotice && (
                <div className="admin-alert" style={{ color: '#15803d', background: '#f0fdf4', borderColor: '#bbf7d0' }}>
                  {actionNotice}
                </div>
              )}
              {actionError && (
                <div className="admin-alert admin-alert--danger" style={{ marginTop: actionNotice ? '8px' : 0 }}>
                  {actionError}
                </div>
              )}
            </div>
          )}
        </section>

        <section className="admin-panel glass-panel" style={{ marginBottom: '16px' }}>
          <header className="admin-panel__header">
            <div>
              <h2 className="admin-panel__title">계정 관리</h2>
              <p className="admin-panel__subtitle">사용자 역할과 전공을 변경하고 비밀번호를 재설정합니다.</p>
            </div>
            <span className="status-pill status-pill--success">
              계정 {accountUsers.length}
            </span>
          </header>

          {accountError ? (
            <div className="admin-alert admin-alert--danger" style={{ marginTop: '14px' }}>{accountError}</div>
          ) : (
            <>
              <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginTop: '14px' }}>
                <input
                  type="search"
                  value={accountSearchTerm}
                  onChange={(event) => setAccountSearchTerm(event.target.value)}
                  placeholder="이름/이메일(아이디) 검색"
                  aria-label="계정 이름 또는 이메일 검색"
                  style={{ flex: '1 1 240px', minHeight: '38px', borderRadius: '8px', border: '1px solid #d1d5db', padding: '8px 10px', background: '#fff' }}
                />
                <select
                  value={accountRoleFilter}
                  onChange={(event) => setAccountRoleFilter(event.target.value)}
                  aria-label="역할 필터"
                  style={{ flex: '0 1 180px', minHeight: '38px', borderRadius: '8px', border: '1px solid #d1d5db', padding: '8px 10px', background: '#fff' }}
                >
                  <option value="all">전체 역할</option>
                  {accountRoles.map((role) => (
                    <option key={role.id} value={role.id}>
                      {role.roleName}
                    </option>
                  ))}
                </select>
                <select
                  value={accountMajorFilter}
                  onChange={(event) => setAccountMajorFilter(event.target.value)}
                  aria-label="전공 필터"
                  style={{ flex: '0 1 200px', minHeight: '38px', borderRadius: '8px', border: '1px solid #d1d5db', padding: '8px 10px', background: '#fff' }}
                >
                  <option value="all">전체 학과</option>
                  {accountMajors.map((major) => (
                    <option key={major.id} value={major.id}>
                      {major.majorname ?? major.Majorname ?? '전공 미상'}
                    </option>
                  ))}
                </select>
              </div>
              <div className="admin-table" style={{ marginTop: '14px', maxHeight: '420px', overflow: 'auto' }}>
              <div style={{ minWidth: '1120px' }}>
                <div className="admin-table__head" style={{ gridTemplateColumns: '1fr 1fr 1.3fr 1fr 0.85fr 1.6fr' }}>
                  <span>아이디</span>
                  <span>이름</span>
                  <span>전공</span>
                  <span>역할</span>
                  <span>수정일</span>
                  <span>관리</span>
                </div>
                <ul className="admin-table__body">
                  {filteredUsers.map((user) => (
                    <li
                      key={user.id}
                      className="admin-table__row"
                      style={{ gridTemplateColumns: '1fr 1fr 1.3fr 1fr 0.85fr 1.6fr', alignItems: 'center' }}
                    >
                      <span>{user.userId}</span>
                      <span>{user.username}</span>
                      <span>
                        <select
                          value={user.majorId}
                          onChange={(event) => handleAccountUpdate(user.id, { majorId: event.target.value })}
                          style={{ width: '100%', minHeight: '36px', borderRadius: '8px', border: '1px solid #d1d5db', padding: '6px 8px', background: '#fff' }}
                        >
                          {accountMajors.map((major) => (
                            <option key={major.id} value={major.id}>
                              {major.majorname ?? major.Majorname ?? '전공 미상'}
                            </option>
                          ))}
                        </select>
                      </span>
                      <span>
                        <select
                          value={user.roleId}
                          onChange={(event) => handleAccountUpdate(user.id, { roleId: event.target.value })}
                          style={{ width: '100%', minHeight: '36px', borderRadius: '8px', border: '1px solid #d1d5db', padding: '6px 8px', background: '#fff' }}
                        >
                          {accountRoles.map((role) => (
                            <option key={role.id} value={role.id}>
                              {role.roleName}
                            </option>
                          ))}
                        </select>
                      </span>
                      <span>{formatDateTime(user.updatedTime)}</span>
                      <span style={{ display: 'flex', gap: '8px', justifyContent: 'flex-start', flexWrap: 'wrap' }}>
                        <button
                          className="ghost-btn ghost-btn--muted small"
                          type="button"
                          onClick={() => handlePasswordReset(user.id, user.userId)}
                        >
                          비밀번호 재설정
                        </button>
                        <button
                          className="ghost-btn ghost-btn--danger small"
                          type="button"
                          onClick={() => handleAccountDelete(user.id, user.userId)}
                        >
                          삭제
                        </button>
                      </span>
                    </li>
                  ))}
                  {filteredUsers.length === 0 && (
                    <li className="admin-table__empty">
                      {accountUsers.length === 0 ? '관리할 계정이 없습니다.' : '검색 결과가 없습니다.'}
                    </li>
                  )}
                </ul>
              </div>
            </div>
            </>
          )}
        </section>

        <div className="admin-dashboard-grid">
          {/* Left Panel: Organizations */}
          <section className="admin-panel admin-panel--organizations glass-panel full-height">
            <header className="admin-panel__header">
              <div>
                <h2 className="admin-panel__title">학생회 조직 현황</h2>
                <p className="admin-panel__subtitle">최근 업데이트 및 상태</p>
              </div>
            </header>
            
            <div className="admin-panel-content-scroll">
                <div className="admin-table">
                <div className="admin-table__head">
                    <span>조직명</span>
                    <span>담당자</span>
                    <span>상태</span>
                </div>
                <ul className="admin-table__body">
                    {organizations.map((org) => (
                    <li key={org.id} className="admin-table__row" style={{ gridTemplateColumns: '1.6fr 1fr 0.8fr' }}>
                        <span>{org.name}</span>
                        <span>{org.manager}</span>
                        <span className={`status-pill status-pill--${org.status === '활성' ? 'success' : 'pending'}`}>
                        {org.status}
                        </span>
                    </li>
                    ))}
                    {organizations.length === 0 && <li className="admin-table__empty">등록된 조직이 없습니다.</li>}
                </ul>
                </div>
            </div>
          </section>

          {/* Right Panel: Reviews */}
          <section className="admin-panel admin-panel--split glass-panel full-height">
            <div className="admin-panel__column full-height">
              <h2 className="admin-panel__title">검수 대기 내역</h2>
              <p className="admin-panel__subtitle">제출된 답변 승인/반려</p>
              <div className="admin-review-list-scroll">
                  {pendingReviewsError && (
                    <div className="admin-alert admin-alert--danger" style={{ marginBottom: '12px' }}>
                      {pendingReviewsError}
                    </div>
                  )}
                  {pendingReviews.map((review) => (
                    <div
                        key={review.id}
                        className={`admin-review-card ${selectedReviewId === review.id ? 'admin-review-card--active' : ''}`}
                    >
                        <button
                          type="button"
                          style={{all: 'unset', cursor: 'pointer', display: 'block', width: '100%', padding: '10px'}}
                          onClick={() => setSelectedReviewId(review.id)}
                          aria-pressed={selectedReviewId === review.id}
                        >
                        <div style={{display: 'flex', alignItems: 'center', marginBottom: '6px'}}>
                            <span className="admin-review-card__dept" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginRight: '4px' }}>{review.departmentName}</span>
                            {review.status !== 'pending' && (
                                <span className={`status-pill status-pill--${review.status === 'approved' || review.status === 'approved_manually' ? 'success' : 'danger'}`} style={{fontSize: '0.7rem', padding: '2px 8px', flexShrink: 0, whiteSpace: 'nowrap'}}>
                                    {review.status === 'approved' || review.status === 'approved_manually' ? '승인됨' : '반려됨'}
                                </span>
                            )}
                        </div>
                        <strong className="admin-review-card__title" style={{ fontSize: '0.95rem', display: 'block', marginBottom: '4px' }}>{review.question}</strong>
                        <div className="admin-review-card__meta">
                            {review.handler} · {new Intl.DateTimeFormat('ko-KR', { month: 'numeric', day: 'numeric' }).format(new Date(review.submittedAt))}
                        </div>
                        </button>
                    </div>
                  ))}
                  {!pendingReviewsError && pendingReviews.length === 0 && (
                    <div className="admin-table__empty">검수할 요청이 없습니다.</div>
                  )}
              </div>
            </div>
            
            <div className="admin-panel__column full-height admin-panel__column--detail">
              <div className="admin-review-detail-scroll">
                  {selectedReview ? (
                    <div className="admin-review-detail" style={{ border: 'none', background: 'transparent', padding: 0 }}>
                      <p className="admin-review-detail__eyebrow">{selectedReview.departmentName}</p>
                      <h3 className="admin-review-detail__title">{selectedReview.question}</h3>
                      <dl className="admin-review-detail__meta">
                        <div>
                          <dt>담당자</dt>
                          <dd>{selectedReview.handler}</dd>
                        </div>
                        <div>
                          <dt>시각</dt>
                          <dd>
                            {new Intl.DateTimeFormat('ko-KR', {
                              month: 'numeric',
                              day: 'numeric',
                              hour: 'numeric',
                              minute: '2-digit',
                            }).format(new Date(selectedReview.submittedAt))}
                          </dd>
                        </div>
                        <div>
                            <dt>상태</dt>
                            <dd>
                                <span className={`status-pill status-pill--${selectedReview.status === 'pending' ? 'pending' : (selectedReview.status === 'approved' || selectedReview.status === 'approved_manually' ? 'success' : 'danger')}`}>
                                    {selectedReview.status === 'pending' ? '대기 중' : (selectedReview.status === 'approved' || selectedReview.status === 'approved_manually' ? '승인됨' : '반려됨')}
                                </span>
                            </dd>
                        </div>
                      </dl>
                      <div className="admin-review-detail__answer">
                        <p>{selectedReview.answer}</p>
                      </div>

                      {actionNotice && (
                        <div className="admin-alert" style={{ marginTop: '12px', color: '#15803d', background: '#f0fdf4', padding: '10px 14px', borderRadius: '8px' }}>
                          {actionNotice}
                        </div>
                      )}
                      {actionError && (
                        <div className="admin-alert admin-alert--danger" style={{ marginTop: '12px' }}>
                          {actionError}
                        </div>
                      )}

                      {selectedReview.status === 'pending' && (
                          <div className="admin-review-detail__actions" style={{ marginTop: '20px' }}>
                            <button
                              className="ghost-btn ghost-btn--muted"
                              type="button"
                              onClick={() => handleReviewAction(selectedReview.id, 'reject')}
                            >
                              반려
                            </button>
                            <button
                              className="hero-btn hero-btn--primary"
                              type="button"
                              onClick={() => handleReviewAction(selectedReview.id, 'approve')}
                            >
                              승인
                            </button>
                          </div>
                      )}
                    </div>
                  ) : (
                    <div className="admin-review-detail admin-review-detail--empty" style={{ height: '100%' }}>
                      <p>목록에서 요청을 선택하세요.</p>
                    </div>
                  )}
              </div>
            </div>
          </section>
        </div>

        <section className="admin-panel glass-panel" style={{ marginTop: '16px' }}>
          <header className="admin-panel__header">
            <div>
              <h2 className="admin-panel__title">RAG 인덱스 상태</h2>
              <p className="admin-panel__subtitle">
                ChromaDB 컬렉션, 로컬 아티팩트, 평가 로그 현황
              </p>
            </div>
            <span className={`status-pill status-pill--${getStatusPillClass(systemStatus)}`}>
              {systemStatusLabel}
            </span>
          </header>

          {ragStatusError ? (
            <div className="admin-alert admin-alert--danger">{ragStatusError}</div>
          ) : ragStatus ? (
            <>
              <div className="admin-metrics" style={{ gridTemplateColumns: 'repeat(4, 1fr)', marginBottom: '16px' }}>
                <button
                  type="button"
                  className="admin-card admin-card--compact"
                  onClick={() => navigate('/admin/logs')}
                  style={{ cursor: 'pointer', transition: 'transform 0.2s', textAlign: 'left', width: '100%', border: 'none', background: 'inherit' }}
                  onMouseOver={(e) => (e.currentTarget.style.transform = 'translateY(-2px)')}
                  onMouseOut={(e) => (e.currentTarget.style.transform = 'translateY(0)')}
                  aria-label="총 질문 로그 보기"
                >
                  <p className="admin-card__label">총 질문 로그</p>
                  <strong className="admin-card__value">{ragStatus.rag_logs.total_queries}</strong>
                </button>
                <article className="admin-card admin-card--compact">
                  <p className="admin-card__label">Fallback 비율</p>
                  <strong className="admin-card__value">{fallbackRate}%</strong>
                </article>
                <article className="admin-card admin-card--compact">
                  <p className="admin-card__label">승인 대기</p>
                  <strong className="admin-card__value">{ragStatus.pending_items.pending}</strong>
                </article>
                <article className="admin-card admin-card--compact">
                  <p className="admin-card__label">최근 질문</p>
                  <strong className="admin-card__value" style={{ fontSize: '1rem' }}>
                    {formatDateTime(ragStatus.rag_logs.latest_query_at)}
                  </strong>
                </article>
              </div>

              {!!ragStatus.rag_logs.fallback_reasons && Object.keys(ragStatus.rag_logs.fallback_reasons).length > 0 && (
                <div className="admin-metrics" style={{ gridTemplateColumns: 'repeat(4, 1fr)', marginBottom: '16px' }}>
                  {Object.entries(ragStatus.rag_logs.fallback_reasons).map(([reason, count]) => (
                    <article key={reason} className="admin-card admin-card--compact">
                      <p className="admin-card__label">{reason}</p>
                      <strong className="admin-card__value">{count}</strong>
                    </article>
                  ))}
                </div>
              )}

              {/* 10컬럼 테이블이 좁은 화면에서 찌그러지지 않도록 가로 스크롤 허용 */}
              <div className="admin-table" style={{ overflowX: 'auto' }}>
                <div style={{ minWidth: '960px' }}>
                <div className="admin-table__head" style={{ gridTemplateColumns: '0.8fr 1.1fr 0.7fr 0.7fr 0.9fr 0.9fr 0.9fr 0.9fr 0.8fr 0.7fr' }}>
                  <span>Dataset</span>
                  <span>Collection</span>
                  <span>Chroma</span>
                  <span>Cache</span>
                  <span>Chunk</span>
                  <span>Vectorizer</span>
                  <span>최신 문서일</span>
                  <span>마지막 인덱싱</span>
                  <span>TF-IDF 버전</span>
                  <span>상태</span>
                </div>
                <ul className="admin-table__body">
                  {ragStatus.datasets.map((dataset) => (
                    <li
                      key={dataset.key}
                      className="admin-table__row"
                      style={{ gridTemplateColumns: '0.8fr 1.1fr 0.7fr 0.7fr 0.9fr 0.9fr 0.9fr 0.9fr 0.8fr 0.7fr' }}
                    >
                      <span>{dataset.key}</span>
                      <span>{dataset.collection}</span>
                      <span>{dataset.chroma_count ?? '-'}</span>
                      <span>{dataset.cached_chunk_count}</span>
                      <span title={formatDateTime(dataset.chunk_artifact_mtime)}>
                        {dataset.chunk_artifact_exists ? '있음' : '없음'}
                      </span>
                      <span title={formatDateTime(dataset.vectorizer_mtime)}>
                        {dataset.vectorizer_exists ? '있음' : '없음'}
                      </span>
                      <span>{formatDateTime(dataset.latest_document_published_at)}</span>
                      <span title={formatDateTime(dataset.last_successful_indexed_at)}>{formatDateTime(dataset.last_successful_indexed_at)}</span>
                      <span>{dataset.vectorizer_sklearn_version ?? '-'}</span>
                      <span className={`status-pill status-pill--${getStatusPillClass(dataset.status)}`}>
                        {getStatusLabel(dataset.status)}
                      </span>
                    </li>
                  ))}
                </ul>
                </div>
              </div>

              {ragStatus.notices_ingestion && (
                <section style={{ marginTop: '16px' }}>
                  <header className="admin-panel__header" style={{ padding: 0, marginBottom: '12px' }}>
                    <div>
                      <h3 className="admin-panel__title" style={{ fontSize: '1rem' }}>Notices 운영 상태</h3>
                      <p className="admin-panel__subtitle">
                        raw / normalized / indexed 수집 상태와 품질 경고
                      </p>
                    </div>
                    <span className={`status-pill status-pill--${getStatusPillClass(ragStatus.notices_ingestion.ingestion_summary.status === 'failed' ? 'error' : ragStatus.notices_ingestion.ingestion_summary.documents_failed > 0 ? 'degraded' : 'ok')}`}>
                      {ragStatus.notices_ingestion.ingestion_summary.status ?? '미실행'}
                    </span>
                  </header>

                  <div className="admin-metrics" style={{ gridTemplateColumns: 'repeat(4, 1fr)', marginBottom: '16px' }}>
                    <article className="admin-card admin-card--compact">
                      <p className="admin-card__label">마지막 수집</p>
                      <strong className="admin-card__value" style={{ fontSize: '1rem' }}>
                        {formatDateTime(ragStatus.notices_ingestion.last_collection_at)}
                      </strong>
                    </article>
                    <article className="admin-card admin-card--compact">
                      <p className="admin-card__label">마지막 성공 run</p>
                      <strong className="admin-card__value" style={{ fontSize: '1rem' }}>
                        {formatDateTime(ragStatus.notices_ingestion.last_successful_ingestion_at)}
                      </strong>
                    </article>
                    <article className="admin-card admin-card--compact">
                      <p className="admin-card__label">파싱 실패</p>
                      <strong className="admin-card__value">{ragStatus.notices_ingestion.quality_summary.parse_failed}</strong>
                    </article>
                    <article className="admin-card admin-card--compact">
                      <p className="admin-card__label">품질 경고</p>
                      <strong className="admin-card__value">{ragStatus.notices_ingestion.quality_summary.severities.warning ?? 0}</strong>
                    </article>
                  </div>

                  <div className="admin-metrics" style={{ gridTemplateColumns: 'repeat(5, 1fr)', marginBottom: '16px' }}>
                    <article className="admin-card admin-card--compact">
                      <p className="admin-card__label">Seen</p>
                      <strong className="admin-card__value">{ragStatus.notices_ingestion.ingestion_summary.documents_seen}</strong>
                    </article>
                    <article className="admin-card admin-card--compact">
                      <p className="admin-card__label">신규</p>
                      <strong className="admin-card__value">{ragStatus.notices_ingestion.ingestion_summary.documents_new}</strong>
                    </article>
                    <article className="admin-card admin-card--compact">
                      <p className="admin-card__label">수정</p>
                      <strong className="admin-card__value">{ragStatus.notices_ingestion.ingestion_summary.documents_updated}</strong>
                    </article>
                    <article className="admin-card admin-card--compact">
                      <p className="admin-card__label">숨김/삭제</p>
                      <strong className="admin-card__value">{ragStatus.notices_ingestion.ingestion_summary.documents_deleted}</strong>
                    </article>
                    <article className="admin-card admin-card--compact">
                      <p className="admin-card__label">실패</p>
                      <strong className="admin-card__value">{ragStatus.notices_ingestion.ingestion_summary.documents_failed}</strong>
                    </article>
                  </div>

                  <div className="admin-metrics" style={{ gridTemplateColumns: 'repeat(3, 1fr)', marginBottom: '16px' }}>
                    <article className="admin-card admin-card--compact">
                      <p className="admin-card__label">Raw 문서</p>
                      <strong className="admin-card__value">{ragStatus.notices_ingestion.stage_summary.raw_documents}</strong>
                    </article>
                    <article className="admin-card admin-card--compact">
                      <p className="admin-card__label">Normalized 문서</p>
                      <strong className="admin-card__value">{ragStatus.notices_ingestion.stage_summary.normalized_documents}</strong>
                    </article>
                    <article className="admin-card admin-card--compact">
                      <p className="admin-card__label">Indexed 문서</p>
                      <strong className="admin-card__value">{ragStatus.notices_ingestion.stage_summary.indexed_documents}</strong>
                    </article>
                  </div>

                  {ragStatus.notices_ingestion.quality_summary.recent_checks.length > 0 && (
                    <div className="admin-table">
                      <div className="admin-table__head" style={{ gridTemplateColumns: '0.9fr 0.8fr 0.8fr 2fr 0.9fr' }}>
                        <span>문서</span>
                        <span>검사</span>
                        <span>심각도</span>
                        <span>메시지</span>
                        <span>시각</span>
                      </div>
                      <ul className="admin-table__body">
                        {ragStatus.notices_ingestion.quality_summary.recent_checks.map((check) => (
                          <li
                            key={`${check.document_key}-${check.check_type}-${check.created_at}`}
                            className="admin-table__row"
                            style={{ gridTemplateColumns: '0.9fr 0.8fr 0.8fr 2fr 0.9fr' }}
                          >
                            <span title={check.document_key}>{check.document_key}</span>
                            <span>{check.check_type}</span>
                            <span>{check.severity}</span>
                            <span>{check.message}</span>
                            <span title={formatDateTime(check.created_at)}>{formatDateTime(check.created_at)}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </section>
              )}
            </>
          ) : (
            <div className="admin-table__empty">RAG 운영 상태를 확인하는 중입니다.</div>
          )}
        </section>

        <section className="admin-panel glass-panel" style={{ marginTop: '16px' }}>
          <header className="admin-panel__header">
            <div>
              <h2 className="admin-panel__title">사용자 피드백</h2>
              <p className="admin-panel__subtitle">RAG 답변 평가와 최근 부정 피드백</p>
            </div>
            <span className="status-pill status-pill--pending">
              부정 {feedbackSummary?.down ?? 0}
            </span>
          </header>

          <div className="admin-metrics" style={{ gridTemplateColumns: 'repeat(3, 1fr)', marginBottom: '16px' }}>
            <article className="admin-card admin-card--compact">
              <p className="admin-card__label">만족도</p>
              <strong className="admin-card__value">
                {satisfactionPercent == null ? '-' : `${satisfactionPercent}%`}
              </strong>
            </article>
            <article className="admin-card admin-card--compact">
              <p className="admin-card__label">좋아요</p>
              <strong className="admin-card__value">👍 {feedbackSummary?.up ?? 0}</strong>
            </article>
            <article className="admin-card admin-card--compact">
              <p className="admin-card__label">싫어요</p>
              <strong className="admin-card__value">👎 {feedbackSummary?.down ?? 0}</strong>
            </article>
          </div>

          {Object.keys(downReasons).length > 0 && (
            <div className="admin-metrics" style={{ gridTemplateColumns: 'repeat(5, 1fr)', marginBottom: '16px' }}>
              {Object.entries(downReasons).map(([reason, count]) => (
                <article key={reason} className="admin-card admin-card--compact">
                  <p className="admin-card__label">{getFeedbackReasonLabel(reason)}</p>
                  <strong className="admin-card__value">{count}</strong>
                </article>
              ))}
            </div>
          )}

          {feedbackLoading ? (
            <div className="admin-table__empty">사용자 피드백을 불러오는 중입니다.</div>
          ) : feedbackError ? (
            <div className="admin-alert admin-alert--danger">{feedbackError}</div>
          ) : negativeFeedback.length > 0 ? (
            <div className="admin-table" style={{ overflowX: 'auto' }}>
              <div style={{ minWidth: '760px' }}>
                <div className="admin-table__head" style={{ gridTemplateColumns: '0.8fr 1.4fr 2fr 0.9fr' }}>
                  <span>사유</span>
                  <span>코멘트</span>
                  <span>질문</span>
                  <span>시각</span>
                </div>
                <ul className="admin-table__body">
                  {negativeFeedback.map((item) => (
                    <li
                      key={item.id}
                      className="admin-table__row"
                      style={{ gridTemplateColumns: '0.8fr 1.4fr 2fr 0.9fr' }}
                    >
                      <span>
                        <span className="status-pill status-pill--danger">
                          {getFeedbackReasonLabel(item.reason)}
                        </span>
                      </span>
                      <span title={item.comment ?? ''}>{item.comment?.trim() || '-'}</span>
                      <span title={item.question ?? ''}>{item.question?.trim() || '-'}</span>
                      <span title={formatDateTime(item.createdAt)}>{formatDateTime(item.createdAt)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : (
            <div className="admin-table__empty">최근 부정 피드백이 없습니다.</div>
          )}
        </section>

      </div>
    </div>
  )
}

export default UniversityAdminPage
