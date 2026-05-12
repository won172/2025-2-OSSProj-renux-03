import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { CouncilOrganization, PendingAnswerReview } from '../../types/admin'
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

const UniversityAdminPage = () => {
  const navigate = useNavigate()
  const [organizations, setOrganizations] = useState<CouncilOrganization[]>([])
  const [pendingReviews, setPendingReviews] = useState<PendingAnswerReview[]>([])
  const [selectedReviewId, setSelectedReviewId] = useState<string | null>(null)
  
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [ragStatus, setRagStatus] = useState<RagAdminStatus | null>(null)
  const [ragStatusError, setRagStatusError] = useState<string | null>(null)

  // Fetch Data
  useEffect(() => {
    const fetchData = async () => {
        console.log('Fetching admin data...'); // Added log
        setLoading(true)
        setError(null) // Clear previous errors
        try {
            // 1. Fetch Organizations
            try {
                const orgsData = await apiFetch<ApiOrganization[]>('/req/orgs')
                console.log('Received organizations data:', orgsData); 
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
                console.warn('Failed to fetch orgs:', e); // Added log
                // Don't set global error for orgs fetch failure, it might not be critical
            }

            // 2. Fetch Pending Reviews (ALL items for history)
            try {
                const pendingData = await apiFetch<ApiPendingItem[]>(`/admin/items?t=${new Date().getTime()}`)
                console.log('Received all reviews data:', pendingData); 
                
                if (Array.isArray(pendingData)) {
                    const mappedReviews: PendingAnswerReview[] = pendingData
                        .map(item => {
                            let title = '제목 없음';
                            let content = '';
                            let category = '공통';
                            let parsedData: any = {};
                            
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
                    if (mappedReviews.length > 0) {
                        // Keep selection if exists, else select first
                        if (!selectedReviewId) setSelectedReviewId(mappedReviews[0].id)
                    } else {
                        setSelectedReviewId(null)
                    }
                }
            } catch (e) {
                console.error('Failed to fetch pending reviews:', e);
                setError('검수 대기 데이터를 불러오는데 실패했습니다.');
            }

            // 3. Fetch RAG operation status
            try {
                const statusData = await apiFetch<RagAdminStatus>(`/admin/rag/status?t=${new Date().getTime()}`)
                setRagStatus(statusData)
                setRagStatusError(null)
            } catch (e) {
                console.error('Failed to fetch RAG status:', e);
                setRagStatus(null)
                setRagStatusError('RAG 운영 상태를 불러오지 못했습니다.')
            }

        } catch (e) { 
            console.error('An unexpected error occurred during admin data fetch:', e);
            setError('관리자 데이터를 불러오는 중 예상치 못한 오류가 발생했습니다.');
        } finally {
            setLoading(false)
        }
    }
    fetchData()
  }, []) // Dependency array empty to run once on mount

  const selectedReview = useMemo(
    () => pendingReviews.find((review) => review.id === selectedReviewId) ?? null,
    [pendingReviews, selectedReviewId],
  )

  const registeredCount = organizations.length
  const pendingCount = pendingReviews.filter(r => r.status === 'pending').length
  const systemStatus = ragStatus?.status ?? (ragStatusError ? 'error' : undefined)
  const systemStatusLabel = getStatusLabel(systemStatus)
  const fallbackRate = ragStatus && ragStatus.rag_logs.total_queries > 0
    ? Math.round((ragStatus.rag_logs.fallback_count / ragStatus.rag_logs.total_queries) * 100)
    : 0

  const handleNavigateHome = () => navigate('/')

  const handleReviewAction = async (reviewId: string, action: 'approve' | 'reject') => {
    console.log(`handleReviewAction called for ID: ${reviewId}, action: ${action}`);
    if (!confirm(`${action === 'approve' ? '승인' : '반려'} 하시겠습니까?`)) {
      return;
    }

    try {
        await apiFetch(`/admin/${action}/${reviewId}`, { method: 'POST' })
        
        // Update UI: update status instead of removing
        setPendingReviews((prev) => prev.map((review) => 
            review.id === reviewId ? { ...review, status: action === 'approve' ? 'approved' : 'rejected' } : review
        ))
        
        const actionLabel = action === 'approve' ? '승인했습니다.' : '반려했습니다.'
        alert(`검수 내역을 ${actionLabel}`)
    } catch (e) {
        console.error('Action failed:', e);
        let message = '요청 처리에 실패했습니다.';
        if (e instanceof Error) {
            message += ` (${e.message})`;
            // @ts-ignore
            if (e.status) message += ` [Status: ${e.status}]`;
        }
        setError(message) 
        alert(message)
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
            <button className="hero-btn hero-btn--primary" type="button" onClick={handleNavigateHome}>
              메인페이지로 이동
            </button>
          </div>
        </header>

        <section className="admin-metrics compact">
          <div className="admin-metrics" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
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
                    <p className="admin-card__label">대기 중 요청</p>
                    <strong className="admin-card__value">{pendingCount}</strong>
                </div>
                <span className="admin-card__icon admin-card__icon--blue" aria-hidden="true">📄</span>
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
                  {pendingReviews.map((review) => (
                    <div
                        key={review.id}
                        className={`admin-review-card ${selectedReviewId === review.id ? 'admin-review-card--active' : ''}`}
                        onClick={() => setSelectedReviewId(review.id)} 
                    >
                        <button type="button" style={{all: 'unset', cursor: 'pointer', display: 'block', width: '100%', padding: '10px'}}>
                        <div style={{display: 'flex', alignItems: 'center', marginBottom: '6px'}}>
                            <span className="admin-review-card__dept" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginRight: '4px' }}>{review.departmentName}</span>
                            {review.status !== 'pending' && (
                                <span className={`status-pill status-pill--${review.status === 'approved' || review.status === 'approved_manually' ? 'success' : 'pending'}`} style={{fontSize: '0.7rem', padding: '2px 8px', flexShrink: 0, whiteSpace: 'nowrap'}}>
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
                  {pendingReviews.length === 0 && (
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
                <article 
                  className="admin-card admin-card--compact"
                  onClick={() => navigate('/admin/logs')}
                  style={{ cursor: 'pointer', transition: 'transform 0.2s' }}
                  onMouseOver={(e) => e.currentTarget.style.transform = 'translateY(-2px)'}
                  onMouseOut={(e) => e.currentTarget.style.transform = 'translateY(0)'}
                >
                  <p className="admin-card__label">총 질문 로그</p>
                  <strong className="admin-card__value">{ragStatus.rag_logs.total_queries}</strong>
                </article>
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

              <div className="admin-table">
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
                      <span>{dataset.latest_document_published_at ?? '-'}</span>
                      <span title={formatDateTime(dataset.last_successful_indexed_at)}>{formatDateTime(dataset.last_successful_indexed_at)}</span>
                      <span>{dataset.vectorizer_sklearn_version ?? '-'}</span>
                      <span className={`status-pill status-pill--${getStatusPillClass(dataset.status)}`}>
                        {getStatusLabel(dataset.status)}
                      </span>
                    </li>
                  ))}
                </ul>
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

      </div>
    </div>
  )
}

export default UniversityAdminPage
