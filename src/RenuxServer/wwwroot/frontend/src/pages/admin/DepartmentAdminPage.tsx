import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import type { DepartmentKnowledge } from '../../types/admin'

// Mock data for initial development
const mockKnowledgeList: DepartmentKnowledge[] = [
  {
    id: 'k-1',
    title: '2025학년도 1학기 졸업논문 제출 안내',
    content: '졸업논문 제출 기한은 2025년 5월 30일까지입니다. 학과 사무실로 방문 제출 또는 이메일 제출 가능합니다. (cs_dept@dgu.ac.kr)',
    status: 'APPROVED',
    createdAt: '2025-03-10T09:00:00Z',
  },
  {
    id: 'k-2',
    title: '학과 스터디룸 이용 수칙 개정',
    content: '스터디룸 예약은 최대 3시간으로 제한되며, 음식물 반입이 금지됩니다. 위반 시 1개월 예약 불가 페널티가 부여됩니다.',
    status: 'PENDING',
    createdAt: '2025-03-12T14:30:00Z',
  },
  {
    id: 'k-3',
    title: '지난 학기 성적 장학금 커트라인',
    content: '지난 학기 1학년 4.2, 2학년 4.15, 3학년 4.0, 4학년 4.3 이었습니다. 이는 매 학기 변동될 수 있습니다.',
    status: 'REJECTED',
    createdAt: '2025-03-01T10:00:00Z',
    rejectionReason: '정보가 불확실합니다. 정확한 소수점 둘째 자리까지 확인 후 다시 제출해주세요.',
  },
]

const DepartmentAdminPage = () => {
  const navigate = useNavigate()
  const [knowledgeList, setKnowledgeList] = useState<DepartmentKnowledge[]>(mockKnowledgeList) // Use mock data
  const [isLoading, setIsLoading] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isCreating, setIsCreating] = useState(false)

  // Form State
  const [contentType, setContentType] = useState<'knowledge' | 'event' | 'announcement'>('knowledge')
  const [newTitle, setNewTitle] = useState('')
  const [newContent, setNewContent] = useState('')
  // Additional fields for Event/Announcement
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [location, setLocation] = useState('')
  const [category, setCategory] = useState('')

  // Initialize with mock data if needed
  useEffect(() => {
    if (knowledgeList.length === 0) {
      setKnowledgeList(mockKnowledgeList);
    }
  }, []);

  const selectedItem = useMemo(
    () => knowledgeList.find((item) => item.id === selectedId) ?? null,
    [knowledgeList, selectedId],
  )

  const handleNavigateHome = () => navigate('/')

  const handleCreateClick = () => {
    setSelectedId(null)
    setIsCreating(true)
    setContentType('knowledge')
    setNewTitle('')
    setNewContent('')
    setStartDate('')
    setEndDate('')
    setLocation('')
    setCategory('')
  }

  const handleItemClick = (id: string) => {
    setIsCreating(false)
    setSelectedId(id)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newTitle.trim() || !newContent.trim()) {
      alert('제목과 내용을 입력해주세요.')
      return
    }

    let payloadData: any = {}
    let sourceType = ''

    if (contentType === 'knowledge') {
      payloadData = {
        question: newTitle,
        answer: newContent,
        category: '학과정보'
      }
      sourceType = 'custom_knowledge'
    } else if (contentType === 'event') {
      if (!startDate) {
        alert('시작일을 입력해주세요.')
        return
      }
      payloadData = {
        title: newTitle,
        start_date: startDate,
        end_date: endDate || startDate,
        location: location,
        department: '컴퓨터공학과', // 추후 Context에서 가져오도록 수정
        description: newContent
      }
      sourceType = 'event'
    } else if (contentType === 'announcement') {
      if (!startDate) {
        alert('게시일을 입력해주세요.')
        return
      }
      payloadData = {
        title: newTitle,
        content: newContent,
        date: startDate,
        category: category || '일반',
        department: '컴퓨터공학과' // 추후 Context에서 가져오도록 수정
      }
      sourceType = 'announcement'
    }

    const submitRequestPayload = {
      source_type: sourceType,
      data: JSON.stringify(payloadData)
    }

    try {
      setIsLoading(true)
      const submitResponse = await apiFetch<{ status: string, id: number }>('/admin/submit', { 
        method: 'POST', 
        json: submitRequestPayload 
      })
      
      if (submitResponse.status === 'ok' && submitResponse.id) {
        const newKnowledgeItem: DepartmentKnowledge = {
          id: submitResponse.id.toString(),
          title: `[${contentType === 'knowledge' ? '정보' : contentType === 'event' ? '행사' : '공지'}] ${newTitle}`,
          content: newContent,
          status: 'PENDING',
          createdAt: new Date().toISOString(),
        };
        setKnowledgeList((prev) => [newKnowledgeItem, ...prev])
        alert('성공적으로 제출되었습니다.')
      } else {
        alert('제출에 실패했습니다. 응답 형식이 올바르지 않습니다.');
      }
      
      setIsCreating(false)
      setNewTitle('')
      setNewContent('')
      
    } catch (e) {
      console.error('Failed to submit', e)
      alert('제출에 실패했습니다. 다시 시도해주세요.')
    } finally {
      setIsLoading(false)
    }
  }

  const handleDelete = async (id: string) => {
    if (!confirm('정말 삭제하시겠습니까?')) return

    try {
      setIsLoading(true)
      
      // If it's a mock ID (starts with 'k-'), skip the API call
      if (!id.startsWith('k-')) {
         // Treat deletion of a pending request as a 'reject' action
         await apiFetch(`/admin/reject/${id}`, { method: 'POST' })
      }

      setKnowledgeList((prev) => prev.filter((item) => item.id !== id))
      if (selectedId === id) setSelectedId(null)
      alert('정보가 삭제되었습니다.')
    } catch (e) {
      console.error('Failed to delete knowledge', e)
      alert('정보 삭제에 실패했습니다. 다시 시도해주세요.')
    } finally {
      setIsLoading(false)
    }
  }

  const getStatusLabel = (status: string) => {
    switch (status) {
      case 'APPROVED': return '승인됨'
      case 'REJECTED': return '반려됨'
      case 'PENDING': return '검수 대기'
      default: return status
    }
  }

  const getStatusClass = (status: string) => {
    switch (status) {
      case 'APPROVED': return 'success'
      case 'REJECTED': return 'danger' // or error
      case 'PENDING': return 'pending' // warning
      default: return 'secondary'
    }
  }

  return (
    <div className="admin-page-wrapper">
      <div className="admin-shell compact-mode">
        <header className="admin-header glass-panel compact">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
            <div>
              <p className="admin-eyebrow">DEPARTMENT COUNCIL</p>
              <h1 className="admin-title compact">학과 정보 관리소</h1>
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
                <p className="admin-card__label">등록된 정보</p>
                <strong className="admin-card__value">{knowledgeList.length}</strong>
              </div>
              <span className="admin-card__icon" aria-hidden="true">📝</span>
            </div>
          </article>
          <article className="admin-card admin-card--compact admin-card--muted">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
              <div>
                <p className="admin-card__label">승인된 정보</p>
                <strong className="admin-card__value">{knowledgeList.filter(k => k.status === 'APPROVED').length}</strong>
              </div>
              <span className="admin-card__icon admin-card__icon--green" aria-hidden="true">✅</span>
            </div>
          </article>
          <article className="admin-card admin-card--compact" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <button className="hero-btn hero-btn--primary" onClick={handleCreateClick} disabled={isLoading}>
              + 새 정보 등록하기
            </button>
          </article>
        </div>
      </section>

        <div className="admin-dashboard-grid">
          {/* Left Panel: Knowledge List */}
          <section className="admin-panel glass-panel full-height" style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 240px)', minHeight: '500px' }}>
            <header className="admin-panel__header" style={{ flexShrink: 0, marginBottom: '16px' }}>
              <div>
                <h2 className="admin-panel__title">등록 내역</h2>
                <p className="admin-panel__subtitle">등록된 정보 목록</p>
              </div>
            </header>
            
            <div className="admin-panel-content-scroll" style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
              {isLoading ? (
                <p className="admin-status">정보를 불러오는 중...</p>
              ) : (
                <ul className="admin-review-list admin-review-list-scroll">
                  {knowledgeList.map((item) => (
                    <li
                      key={item.id}
                      className={`admin-review-card ${selectedId === item.id ? 'admin-review-card--active' : ''}`}
                    >
                      <button type="button" onClick={() => handleItemClick(item.id)}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', marginBottom: '4px' }}>
                          <span className={`status-pill status-pill--${getStatusClass(item.status)}`}>
                            {getStatusLabel(item.status)}
                          </span>
                          <span className="admin-review-card__meta">
                            {new Date(item.createdAt).toLocaleDateString()}
                          </span>
                        </div>
                        <strong className="admin-review-card__title">{item.title}</strong>
                      </button>
                    </li>
                  ))}
                  {knowledgeList.length === 0 && (
                    <li className="admin-table__empty">등록된 정보가 없습니다.</li>
                  )}
                </ul>
              )}
            </div>
          </section>

          {/* Right Panel: Detail or Create Form */}
          <section className="admin-panel glass-panel full-height" style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 240px)', minHeight: '500px' }}>
            <div className="admin-panel__column full-height admin-panel__column--detail" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                <div className="admin-panel-content-scroll admin-review-detail-scroll" style={{ flex: 1, overflowY: 'auto' }}>
                  {isCreating ? (
                    <div className="admin-review-detail" style={{ border: 'none', background: 'transparent', padding: 0 }}>
                      <p className="admin-review-detail__eyebrow">새 정보 등록</p>
                      <h3 className="admin-review-detail__title">정보 입력</h3>
                      
                      <form onSubmit={handleSubmit}>
                        <div className="mb-3">
                          <label className="admin-form-label">등록 유형</label>
                          <select 
                            className="admin-input" 
                            value={contentType} 
                            onChange={(e) => setContentType(e.target.value as any)}
                            disabled={isLoading}
                          >
                            <option value="knowledge">❓ 자주 묻는 질문 (FAQ)</option>
                            <option value="event">📅 학과 행사 (Event)</option>
                            <option value="announcement">📢 공지사항 (Notice)</option>
                          </select>
                        </div>

                        <div className="mb-3">
                          <label className="admin-form-label">
                            {contentType === 'knowledge' ? '질문 (Question)' : contentType === 'event' ? '행사명 (Title)' : '제목 (Title)'}
                          </label>
                          <input 
                            type="text" 
                            className="admin-input" 
                            placeholder={contentType === 'knowledge' ? "예: 졸업논문 제출 기한" : "제목을 입력하세요"}
                            value={newTitle}
                            onChange={(e) => setNewTitle(e.target.value)}
                            disabled={isLoading}
                          />
                        </div>

                        {/* Date Fields for Event/Announcement */}
                        {(contentType === 'event' || contentType === 'announcement') && (
                          <div className="mb-3" style={{ display: 'flex', gap: '10px' }}>
                            <div style={{ flex: 1 }}>
                              <label className="admin-form-label">{contentType === 'event' ? '시작일' : '게시일'}</label>
                              <input 
                                type="date" 
                                className="admin-input"
                                value={startDate}
                                onChange={(e) => setStartDate(e.target.value)}
                                disabled={isLoading}
                              />
                            </div>
                            {contentType === 'event' && (
                              <div style={{ flex: 1 }}>
                                <label className="admin-form-label">종료일 (선택)</label>
                                <input 
                                  type="date" 
                                  className="admin-input"
                                  value={endDate}
                                  onChange={(e) => setEndDate(e.target.value)}
                                  disabled={isLoading}
                                />
                              </div>
                            )}
                          </div>
                        )}

                        {/* Location for Event */}
                        {contentType === 'event' && (
                          <div className="mb-3">
                            <label className="admin-form-label">장소</label>
                            <input 
                              type="text" 
                              className="admin-input"
                              placeholder="예: 공학관 101호"
                              value={location}
                              onChange={(e) => setLocation(e.target.value)}
                              disabled={isLoading}
                            />
                          </div>
                        )}

                        {/* Category for Announcement */}
                        {contentType === 'announcement' && (
                          <div className="mb-3">
                            <label className="admin-form-label">카테고리</label>
                            <input 
                              type="text" 
                              className="admin-input"
                              placeholder="예: 학사, 장학, 채용"
                              value={category}
                              onChange={(e) => setCategory(e.target.value)}
                              disabled={isLoading}
                            />
                          </div>
                        )}
                        
                        <div className="mb-3">
                          <label className="admin-form-label">
                            {contentType === 'knowledge' ? '답변 (Answer)' : '상세 내용'}
                          </label>
                          <textarea 
                            className="admin-textarea" 
                            rows={10} 
                            placeholder="상세 내용을 입력하세요."
                            value={newContent}
                            onChange={(e) => setNewContent(e.target.value)}
                            disabled={isLoading}
                          />
                        </div>

                        <div className="admin-review-detail__actions">
                          <button className="hero-btn hero-btn--primary" type="submit" disabled={isLoading}>
                            {isLoading ? '제출 중...' : '제출하기'}
                          </button>
                        </div>
                      </form>
                    </div>
                  ) : selectedItem ? (
                    <div className="admin-review-detail" style={{ border: 'none', background: 'transparent', padding: 0 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                        <span className={`status-pill status-pill--${getStatusClass(selectedItem.status)}`}>
                          {getStatusLabel(selectedItem.status)}
                        </span>
                        <button className="ghost-btn small ghost-btn--danger" onClick={() => handleDelete(selectedItem.id)} disabled={isLoading}>
                          {isLoading ? '삭제 중...' : '삭제'}
                        </button>
                      </div>
                      
                      <h3 className="admin-review-detail__title" style={{ marginTop: '1rem' }}>{selectedItem.title}</h3>
                      <dl className="admin-review-detail__meta">
                        <div>
                          <dt>등록일</dt>
                          <dd>{new Date(selectedItem.createdAt).toLocaleString()}</dd>
                        </div>
                      </dl>
                      
                      <div className="admin-review-detail__question">
                        <p style={{ whiteSpace: 'pre-wrap' }}>{selectedItem.content}</p>
                      </div>

                      {selectedItem.status === 'REJECTED' && selectedItem.rejectionReason && (
                        <div className="admin-alert admin-alert--danger">
                          <strong>반려 사유:</strong> {selectedItem.rejectionReason}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="admin-review-detail admin-review-detail--empty" style={{ height: '100%' }}>
                      <p>왼쪽 목록에서 정보를 선택하거나,<br/>'새 정보 등록하기' 버튼을 눌러주세요.</p>
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

export default DepartmentAdminPage
