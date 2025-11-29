import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { CouncilOrganization, PendingAnswerReview } from '../../types/admin'

const organizationMocks: CouncilOrganization[] = [
  {
    id: 'org-1',
    name: '총학생회',
    manager: '박지원',
    updatedAt: '2024-11-18',
    status: '활성',
    pendingRequests: 2,
  },
  {
    id: 'org-2',
    name: '컴퓨터공학과 학생회',
    manager: '이서준',
    updatedAt: '2024-11-15',
    status: '활성',
    pendingRequests: 1,
  },
  {
    id: 'org-3',
    name: '경영학과 학생회',
    manager: '최민서',
    updatedAt: '2024-11-10',
    status: '검토 중',
    pendingRequests: 0,
  },
]

const pendingReviewMocks: PendingAnswerReview[] = [
  {
    id: 'rev-1',
    departmentName: '컴퓨터공학과',
    submittedAt: '2024-11-19T08:32:00Z',
    handler: '이서준',
    question: '2025년 1학기 등록금 고지서는 언제 발송되나요?',
    answer:
      '총무팀에서 2월 7일(금) 이메일과 문자로 일괄 발송합니다. 학과 홈페이지 공지사항에서도 같은 날 확인 가능합니다.',
  },
  {
    id: 'rev-2',
    departmentName: '경영학과',
    submittedAt: '2024-11-18T05:15:00Z',
    handler: '최민서',
    question: '학과 스터디룸 예약이 안 되는데, 방법을 알려주세요.',
    answer:
      '학생지원센터 예약 시스템에서 경영학과 > 스터디룸 선택 후 주당 최대 2시간까지 예약할 수 있습니다. 잔여 회차가 없으면 다음 주 월요일 09시에 새로 열립니다.',
  },
]

const UniversityAdminPage = () => {
  const navigate = useNavigate()
  const [organizations] = useState(organizationMocks)
  const [pendingReviews, setPendingReviews] = useState(pendingReviewMocks)
  const [selectedReviewId, setSelectedReviewId] = useState<string | null>(pendingReviews[0]?.id ?? null)

  const selectedReview = useMemo(
    () => pendingReviews.find((review) => review.id === selectedReviewId) ?? null,
    [pendingReviews, selectedReviewId],
  )

  const registeredCount = organizations.length
  const pendingCount = pendingReviews.length

  const handleNavigateHome = () => navigate('/')

  const handleReviewAction = (reviewId: string, action: 'approve' | 'reject') => {
    setPendingReviews((prev) => prev.filter((review) => review.id !== reviewId))
    if (selectedReviewId === reviewId) {
      setSelectedReviewId(null)
    }
    const actionLabel = action === 'approve' ? '승인했습니다.' : '반려했습니다.'
    alert(`검수 내역을 ${actionLabel}`)
  }

  return (
    <div className="admin-shell">
      <header className="admin-header glass-panel">
        <div>
          <p className="admin-eyebrow">ADMINISTRATION</p>
          <h1 className="admin-title">관리자 제어 센터</h1>
          <p className="admin-subtitle">학생회 조직, 공지사항, 챗봇 전달 문의를 한 곳에서 확인하세요.</p>
        </div>
        <button className="hero-btn hero-btn--primary" type="button" onClick={handleNavigateHome}>
          메인페이지로 이동
        </button>
      </header>

      <section className="admin-metrics">
        <article className="admin-card admin-card--accent">
          <div>
            <p className="admin-card__label">등록된 조직</p>
            <strong className="admin-card__value">{registeredCount}</strong>
          </div>
          <span className="admin-card__icon" aria-hidden="true">
            👥
          </span>
        </article>
        <article className="admin-card">
          <div>
            <p className="admin-card__label">대기 중 요청</p>
            <strong className="admin-card__value">{pendingCount}</strong>
          </div>
          <span className="admin-card__icon admin-card__icon--blue" aria-hidden="true">
            📄
          </span>
        </article>
        <article className="admin-card">
          <div>
            <p className="admin-card__label">시스템 상태</p>
            <strong className="admin-card__value">양호</strong>
          </div>
          <span className="admin-card__icon admin-card__icon--green" aria-hidden="true">
            🟢
          </span>
        </article>
      </section>

      <section className="admin-panel glass-panel">
        <header className="admin-panel__header">
          <div>
            <h2 className="admin-panel__title">학생회 조직 현황</h2>
            <p className="admin-panel__subtitle">최근 업데이트 일자와 담당자를 확인하세요.</p>
          </div>
          <button className="ghost-btn" type="button">
            + 새로운 조직 추가
          </button>
        </header>
        <div className="admin-table">
          <div className="admin-table__head">
            <span>조직명</span>
            <span>담당자</span>
            <span>최근 업데이트</span>
            <span>상태</span>
          </div>
          <ul className="admin-table__body">
            {organizations.map((org) => (
              <li key={org.id} className="admin-table__row">
                <span>{org.name}</span>
                <span>{org.manager}</span>
                <span>{org.updatedAt}</span>
                <span className={`status-pill status-pill--${org.status === '활성' ? 'success' : 'pending'}`}>
                  {org.status}
                </span>
              </li>
            ))}
            {organizations.length === 0 && <li className="admin-table__empty">등록된 조직이 없습니다.</li>}
          </ul>
        </div>
      </section>

      <section className="admin-panel admin-panel--split">
        <div className="admin-panel__column">
          <h2 className="admin-panel__title">검수 대기 내역</h2>
          <p className="admin-panel__subtitle">학과 학생회에서 제출한 답변을 확인하고 승인하세요.</p>
          <ul className="admin-review-list">
            {pendingReviews.map((review) => (
              <li
                key={review.id}
                className={`admin-review-card ${selectedReviewId === review.id ? 'admin-review-card--active' : ''}`}
              >
                <button type="button" onClick={() => setSelectedReviewId(review.id)}>
                  <span className="admin-review-card__dept">{review.departmentName}</span>
                  <strong className="admin-review-card__title">{review.question}</strong>
                  <span className="admin-review-card__meta">
                    {review.handler} ·{' '}
                    {new Intl.DateTimeFormat('ko-KR', { month: 'long', day: 'numeric' }).format(
                      new Date(review.submittedAt),
                    )}
                  </span>
                </button>
              </li>
            ))}
            {pendingReviews.length === 0 && (
              <li className="admin-table__empty">검수할 요청이 모두 처리되었습니다.</li>
            )}
          </ul>
        </div>
        <div className="admin-panel__column admin-panel__column--detail">
          {selectedReview ? (
            <div className="admin-review-detail">
              <p className="admin-review-detail__eyebrow">{selectedReview.departmentName}</p>
              <h3 className="admin-review-detail__title">{selectedReview.question}</h3>
              <dl className="admin-review-detail__meta">
                <div>
                  <dt>담당자</dt>
                  <dd>{selectedReview.handler}</dd>
                </div>
                <div>
                  <dt>제출 시각</dt>
                  <dd>
                    {new Intl.DateTimeFormat('ko-KR', {
                      month: 'long',
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
              <div className="admin-review-detail__actions">
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
            <div className="admin-review-detail admin-review-detail--empty">
              <p>왼쪽 목록에서 검수할 요청을 선택하세요.</p>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

export default UniversityAdminPage
