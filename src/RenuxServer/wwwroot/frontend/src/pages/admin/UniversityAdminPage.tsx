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
        majorname: string
    }
}

const UniversityAdminPage = () => {
  const navigate = useNavigate()
  const [organizations, setOrganizations] = useState<CouncilOrganization[]>([])
  const [pendingReviews, setPendingReviews] = useState<PendingAnswerReview[]>([])
  const [selectedReviewId, setSelectedReviewId] = useState<string | null>(null)
  
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

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
                console.log('Received organizations data:', orgsData); // Added log
                if (Array.isArray(orgsData)) {
                    const mappedOrgs: CouncilOrganization[] = orgsData.map(org => ({
                        id: org.id,
                        name: `${org.major.majorname} 학생회`,
                        manager: '-', // Not available in API
                        updatedAt: new Date().toISOString().split('T')[0], // Placeholder
                        status: '활성',
                        pendingRequests: 0 // Placeholder
                    }))
                    setOrganizations(mappedOrgs)
                }
            } catch (e) {
                console.warn('Failed to fetch orgs:', e); // Added log
                // Don't set global error for orgs fetch failure, it might not be critical
            }

            // 2. Fetch Pending Reviews
            try {
                const pendingData = await apiFetch<ApiPendingItem[]>('/admin/pending')
                console.log('Received pending reviews data:', pendingData); // Added log
                
                if (Array.isArray(pendingData)) {
                    const mappedReviews: PendingAnswerReview[] = pendingData
                        .filter(item => item.source_type === 'custom_knowledge')
                        .map(item => {
                            let parsedData = { question: '', answer: '', category: '' }
                            try {
                                parsedData = JSON.parse(item.data)
                            } catch (e) { console.error('JSON parse error for item data:', item.data, e) } // More detailed log
                            
                            return {
                                id: item.id.toString(),
                                departmentName: parsedData.category || '공통',
                                submittedAt: item.created_at,
                                handler: 'System', // Placeholder
                                question: parsedData.question,
                                answer: parsedData.answer
                            }
                        })
                    setPendingReviews(mappedReviews)
                    if (mappedReviews.length > 0) {
                        setSelectedReviewId(mappedReviews[0].id) // Automatically select the first one
                    } else {
                        setSelectedReviewId(null) // Clear selection if no reviews
                    }
                    console.log('Mapped pending reviews:', mappedReviews); // Added log
                }
            } catch (e) {
                console.error('Failed to fetch pending reviews:', e); // More detailed log
                setError('검수 대기 데이터를 불러오는데 실패했습니다.'); // Set global error
            }

        } catch (e) { // Catch-all for other unexpected errors during fetchData
            console.error('An unexpected error occurred during admin data fetch:', e);
            setError('관리자 데이터를 불러오는 중 예상치 못한 오류가 발생했습니다.');
        } finally {
            setLoading(false)
        }
    }
    fetchData()
  }, [])

  const selectedReview = useMemo(
    () => pendingReviews.find((review) => review.id === selectedReviewId) ?? null,
    [pendingReviews, selectedReviewId],
  )

  const registeredCount = organizations.length
  const pendingCount = pendingReviews.length

  const handleNavigateHome = () => navigate('/')

  const handleReviewAction = async (reviewId: string, action: 'approve' | 'reject') => {
    console.log(`handleReviewAction called for ID: ${reviewId}, action: ${action}`); // Added log
    if (!confirm(`${action === 'approve' ? '승인' : '반려'} 하시겠습니까?`)) {
      console.log('User cancelled action.'); // Added log
      return;
    }

    try {
        console.log(`Sending ${action} request to /admin/${action}/${reviewId}`); // Added log
        await apiFetch(`/admin/${action}/${reviewId}`, { method: 'POST' })
        console.log(`Request for ID ${reviewId} with action ${action} successful.`); // Added log
        
        // Update UI: remove the approved/rejected item
        setPendingReviews((prev) => prev.filter((review) => review.id !== reviewId))
        
        // If the currently selected item was just handled, select next or clear
        if (selectedReviewId === reviewId) {
            const remainingReviews = pendingReviews.filter(review => review.id !== reviewId);
            setSelectedReviewId(remainingReviews.length > 0 ? remainingReviews[0].id : null);
        }

        const actionLabel = action === 'approve' ? '승인했습니다.' : '반려했습니다.'
        alert(`검수 내역을 ${actionLabel}`)
    } catch (e) {
        console.error('Action failed:', e); // More detailed log
        let message = '요청 처리에 실패했습니다.';
        if (e instanceof Error) {
            message += ` (${e.message})`;
            // @ts-ignore
            if (e.status) message += ` [Status: ${e.status}]`;
        }
        setError(message) // Set error state for display
        alert(message)
    }
  }

  // Display loading, error, or main content
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
                    <strong className="admin-card__value">양호</strong>
                </div>
                <span className="admin-card__icon admin-card__icon--green" aria-hidden="true">🟢</span>
              </div>
            </article>
          </div>
        </section>

        <div className="admin-dashboard-grid">
          {/* Left Panel: Organizations */}
          <section className="admin-panel glass-panel full-height">
            <header className="admin-panel__header">
              <div>
                <h2 className="admin-panel__title">학생회 조직 현황</h2>
                <p className="admin-panel__subtitle">최근 업데이트 및 상태</p>
              </div>
              {/* <button className="ghost-btn small" type="button">+ 추가</button> */}
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
                        onClick={() => setSelectedReviewId(review.id)} // Click on card selects it
                    >
                        <button type="button" style={{all: 'unset', cursor: 'pointer', display: 'block', width: '100%', padding: '10px'}}>
                        <span className="admin-review-card__dept">{review.departmentName}</span>
                        <strong className="admin-review-card__title" style={{ fontSize: '0.95rem' }}>{review.question}</strong>
                        <span className="admin-review-card__meta">
                            {review.handler} · {new Intl.DateTimeFormat('ko-KR', { month: 'numeric', day: 'numeric' }).format(new Date(review.submittedAt))}
                        </span>
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
                      </dl>
                      <div className="admin-review-detail__answer">
                        <p>{selectedReview.answer}</p>
                      </div>
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

      </div>
    </div>
  )
}

export default UniversityAdminPage