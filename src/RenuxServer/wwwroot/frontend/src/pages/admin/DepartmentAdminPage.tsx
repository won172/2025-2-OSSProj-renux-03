import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { DepartmentRequest, PendingAnswerReview } from '../../types/admin'

const pendingRequestMocks: DepartmentRequest[] = [
  {
    id: 'req-1',
    chatId: 'chat-101',
    title: '학생회 공지 업데이트 요청',
    departmentName: '컴퓨터공학과',
    createdAt: '2024-11-19',
    summary: '새로운 공모전 일정이 반영되지 않았다는 문의',
    requester: '김민지',
    originalQuestion: '전공 공모전 일정이 챗봇에서 예전 정보로 떠요. 최신 일정 알려주세요.',
  },
  {
    id: 'req-2',
    chatId: 'chat-102',
    title: 'AI 답변 템플릿 수정 요청',
    departmentName: '컴퓨터공학과',
    createdAt: '2024-11-18',
    summary: '학사 일정 안내 문구 보완',
    requester: '박정우',
    originalQuestion: '이번 주 학사 일정이 비어있다고 나오는데, 등록이 안 된 건가요?',
  },
]

const DepartmentAdminPage = () => {
  const navigate = useNavigate()
  const [pendingRequests, setPendingRequests] = useState(pendingRequestMocks)
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(pendingRequests[0]?.id ?? null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [submittedQueue, setSubmittedQueue] = useState<PendingAnswerReview[]>([])

  const selectedRequest = useMemo(
    () => pendingRequests.find((request) => request.id === selectedRequestId) ?? null,
    [pendingRequests, selectedRequestId],
  )

  const pendingCount = pendingRequests.length
  const selectedDraft = selectedRequest ? drafts[selectedRequest.id] ?? '' : ''

  const handleNavigateHome = () => navigate('/')

  const handleDraftChange = (value: string) => {
    if (!selectedRequest) return
    setDrafts((prev) => ({ ...prev, [selectedRequest.id]: value }))
  }

  const handleSubmitAnswer = () => {
    if (!selectedRequest) return
    if (!selectedDraft.trim()) {
      alert('답변 내용을 입력해주세요.')
      return
    }
    const newReview: PendingAnswerReview = {
      id: `review-${Date.now()}`,
      departmentName: selectedRequest.departmentName,
      submittedAt: new Date().toISOString(),
      handler: '컴퓨터공학과 학생회',
      question: selectedRequest.originalQuestion,
      answer: selectedDraft.trim(),
    }
    setSubmittedQueue((prev) => [newReview, ...prev])
    setPendingRequests((prev) => prev.filter((request) => request.id !== selectedRequest.id))
    setSelectedRequestId((prev) => {
      if (prev !== selectedRequest.id) return prev
      const remaining = pendingRequests.filter((request) => request.id !== selectedRequest.id)
      return remaining[0]?.id ?? null
    })
    setDrafts((prev) => {
      const { [selectedRequest.id]: _, ...rest } = prev
      return rest
    })
    alert('총학생회 검수를 위한 답변을 제출했습니다.')
  }

  return (
    <div className="admin-shell">
      <header className="admin-header glass-panel">
        <div>
          <p className="admin-eyebrow">DEPARTMENT COUNCIL</p>
          <h1 className="admin-title">학과 학생회 관리자</h1>
          <p className="admin-subtitle">챗봇이 전달한 문의를 검토하고 답변을 등록하세요.</p>
        </div>
        <button className="ghost-btn" type="button" onClick={handleNavigateHome}>
          메인페이지로 이동
        </button>
      </header>

      <section className="admin-metrics">
        <article className="admin-card admin-card--compact">
          <p className="admin-card__label">대기 중 요청</p>
          <strong className="admin-card__value">{pendingCount}</strong>
          <p className="admin-card__hint">최근 접수된 변경 요청을 확인하세요.</p>
        </article>
        <article className="admin-card admin-card--compact admin-card--muted">
          <p className="admin-card__label">최근 제출</p>
          <strong className="admin-card__value">{submittedQueue.length}</strong>
          <p className="admin-card__hint">총학생회 검수를 기다리는 답변 수</p>
        </article>
      </section>

      <section className="admin-panel admin-panel--split">
        <div className="admin-panel__column">
          <h2 className="admin-panel__title">대기 중인 요청</h2>
          <p className="admin-panel__subtitle">최근 전달된 질문을 확인하세요.</p>
          <ul className="admin-review-list">
            {pendingRequests.map((request) => (
              <li
                key={request.id}
                className={`admin-review-card ${selectedRequestId === request.id ? 'admin-review-card--active' : ''}`}
              >
                <button type="button" onClick={() => setSelectedRequestId(request.id)}>
                  <span className="admin-review-card__dept">{request.departmentName}</span>
                  <strong className="admin-review-card__title">{request.title}</strong>
                  <span className="admin-review-card__meta">
                    {request.requester ?? '익명'} · {request.createdAt}
                  </span>
                </button>
              </li>
            ))}
            {pendingRequests.length === 0 && (
              <li className="admin-table__empty">대기 중인 요청이 없습니다.</li>
            )}
          </ul>
        </div>
        <div className="admin-panel__column admin-panel__column--detail">
          {selectedRequest ? (
            <div className="admin-review-detail">
              <p className="admin-review-detail__eyebrow">{selectedRequest.departmentName}</p>
              <h3 className="admin-review-detail__title">{selectedRequest.title}</h3>
              <dl className="admin-review-detail__meta">
                <div>
                  <dt>요청자</dt>
                  <dd>{selectedRequest.requester ?? '익명'}</dd>
                </div>
                <div>
                  <dt>접수일</dt>
                  <dd>{selectedRequest.createdAt}</dd>
                </div>
              </dl>
              <div className="admin-review-detail__question">
                <p>{selectedRequest.originalQuestion}</p>
              </div>
              <label className="admin-form-field">
                <span>답변 내용</span>
                <textarea
                  value={selectedDraft}
                  onChange={(event) => handleDraftChange(event.target.value)}
                  placeholder="사용자에게 전달할 답변을 입력하세요."
                  rows={6}
                />
              </label>
              <div className="admin-review-detail__actions">
                <button className="hero-btn hero-btn--primary" type="button" onClick={handleSubmitAnswer}>
                  총학생회로 제출
                </button>
              </div>
            </div>
          ) : (
            <div className="admin-review-detail admin-review-detail--empty">
              <p>왼쪽에서 확인할 요청을 선택하세요.</p>
            </div>
          )}
        </div>
      </section>

      <section className="admin-panel">
        <h2 className="admin-panel__title">최근 제출 · 검수 대기</h2>
        <p className="admin-panel__subtitle">총학생회가 확인 중인 답변입니다.</p>
        <ul className="admin-history-list">
          {submittedQueue.map((submission) => (
            <li key={submission.id}>
              <div>
                <strong>{submission.question}</strong>
                <p className="admin-history__answer">{submission.answer}</p>
              </div>
              <span className="status-pill status-pill--pending">검수 대기</span>
            </li>
          ))}
          {submittedQueue.length === 0 && (
            <li className="admin-table__empty">제출한 답변이 아직 없습니다.</li>
          )}
        </ul>
      </section>
    </div>
  )
}

export default DepartmentAdminPage
