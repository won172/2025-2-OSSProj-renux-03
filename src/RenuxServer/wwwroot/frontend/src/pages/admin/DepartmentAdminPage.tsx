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
  const [knowledgeList, setKnowledgeList] = useState<DepartmentKnowledge[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isCreating, setIsCreating] = useState(false)

  // Form State
  const [newTitle, setNewTitle] = useState('')
  const [newContent, setNewContent] = useState('')

  useEffect(() => {
    const loadKnowledge = async () => {
      setIsLoading(true)
      try {
        const data = await apiFetch<DepartmentKnowledge[]>('/admin/dept/knowledge', { method: 'GET' })
        if (Array.isArray(data)) {
            setKnowledgeList(data)
        } else {
            setKnowledgeList([])
        }
      } catch (e) {
        console.error('Failed to load knowledge', e)
        setKnowledgeList(mockKnowledgeList) // Fallback to mock data on error
      } finally {
        setIsLoading(false)
      }
    }
    loadKnowledge()
  }, [])

  const selectedItem = useMemo(
    () => knowledgeList.find((item) => item.id === selectedId) ?? null,
    [knowledgeList, selectedId],
  )

  const handleNavigateHome = () => navigate('/')

  const handleCreateClick = () => {
    setSelectedId(null)
    setIsCreating(true)
    setNewTitle('')
    setNewContent('')
  }

  const handleItemClick = (id: string) => {
    setIsCreating(false)
    setSelectedId(id)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newTitle.trim() || !newContent.trim()) {
      alert('제목과 내용을 모두 입력해주세요.')
      return
    }

    const newKnowledgeData = {
      title: newTitle,
      content: newContent,
    }

    try {
      setIsLoading(true)
      const submittedKnowledge = await apiFetch<DepartmentKnowledge>('/admin/dept/knowledge', { 
        method: 'POST', 
        json: newKnowledgeData 
      })
      
      setKnowledgeList((prev) => [submittedKnowledge, ...prev])
      setIsCreating(false)
      setNewTitle('')
      setNewContent('')
      alert('새로운 정보가 등록되었습니다. 총학생회 승인 후 챗봇에 반영됩니다.')
    } catch (e) {
      console.error('Failed to submit knowledge', e)
      alert('정보 등록에 실패했습니다. 다시 시도해주세요.')
    } finally {
      setIsLoading(false)
    }
  }

  const handleDelete = async (id: string) => {
    if (!confirm('정말 삭제하시겠습니까?')) return

    try {
      setIsLoading(true)
      await apiFetch(`/admin/dept/knowledge/${id}`, { method: 'DELETE' })

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
    <div className="admin-shell">
      <header className="admin-header glass-panel">
        <div>
          <p className="admin-eyebrow">DEPARTMENT COUNCIL</p>
          <h1 className="admin-title">학과 정보 관리소</h1>
          <p className="admin-subtitle">우리 학과 학생들을 위한 맞춤형 정보를 등록하세요. 챗봇이 이 내용을 학습합니다.</p>
        </div>
        <button className="ghost-btn" type="button" onClick={handleNavigateHome}>
          메인페이지로 이동
        </button>
      </header>

      <section className="admin-metrics">
        <article className="admin-card admin-card--compact">
          <p className="admin-card__label">등록된 정보</p>
          <strong className="admin-card__value">{knowledgeList.length}</strong>
        </article>
        <article className="admin-card admin-card--compact admin-card--muted">
          <p className="admin-card__label">승인된 정보</p>
          <strong className="admin-card__value">{knowledgeList.filter(k => k.status === 'APPROVED').length}</strong>
          <p className="admin-card__hint">현재 챗봇이 답변 가능한 정보 수</p>
        </article>
        <button className="hero-btn hero-btn--primary" style={{ marginLeft: 'auto' }} onClick={handleCreateClick} disabled={isLoading}>
          + 새 정보 등록하기
        </button>
      </section>

      <section className="admin-panel admin-panel--split">
        {/* Left Column: List */}
        <div className="admin-panel__column">
          <h2 className="admin-panel__title">등록 내역</h2>
          {isLoading ? (
            <p className="admin-status">정보를 불러오는 중...</p>
          ) : (
            <ul className="admin-review-list">
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

        {/* Right Column: Detail or Create Form */}
        <div className="admin-panel__column admin-panel__column--detail">
          {isCreating ? (
            <div className="admin-review-detail">
              <p className="admin-review-detail__eyebrow">새 정보 등록</p>
              <h3 className="admin-review-detail__title">정보 입력</h3>
              
              <form onSubmit={handleSubmit}>
                <div className="mb-3">
                  <label className="admin-form-label">제목 (키워드)</label>
                  <input 
                    type="text" 
                    className="admin-input" 
                    placeholder="예: 졸업논문 제출 기한, 사물함 신청 방법"
                    value={newTitle}
                    onChange={(e) => setNewTitle(e.target.value)}
                    disabled={isLoading}
                  />
                  <p className="admin-form-hint">학생들이 질문할 만한 핵심 키워드를 포함해주세요.</p>
                </div>
                
                <div className="mb-3">
                  <label className="admin-form-label">상세 내용</label>
                  <textarea 
                    className="admin-textarea" 
                    rows={10} 
                    placeholder="상세한 정보를 입력하세요. 챗봇은 이 내용을 바탕으로 답변을 생성합니다."
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
            <div className="admin-review-detail">
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
            <div className="admin-review-detail admin-review-detail--empty">
              <p>왼쪽 목록에서 정보를 선택하거나,<br/>'새 정보 등록하기' 버튼을 눌러주세요.</p>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

export default DepartmentAdminPage
