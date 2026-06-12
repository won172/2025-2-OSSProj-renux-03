import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import type { DepartmentKnowledge } from '../../types/admin'

interface UserInfoResponse {
  name?: string;
  Name?: string;
  majorName?: string;
  MajorName?: string;
}

interface AdminItemResponse {
  id: number | string;
  data: string;
  source_type: string;
  status: string;
  created_at: string;
}

interface KnowledgePayload {
  question: string;
  answer: string;
  category: string;
  requester?: string;
}

interface EventPayload {
  title: string;
  start_date: string;
  end_date: string;
  location: string;
  department: string;
  description: string;
  requester?: string;
}

interface AnnouncementPayload {
  title: string;
  content: string;
  date: string;
  category: string;
  department: string;
  requester?: string;
}

const DepartmentAdminPage = () => {
  const navigate = useNavigate()
  const [knowledgeList, setKnowledgeList] = useState<DepartmentKnowledge[]>([])
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
  const [userDepartment, setUserDepartment] = useState('')
  const [userName, setUserName] = useState('')
  // alert() 대신 사용하는 인라인 알림 (성공/오류)
  const [notice, setNotice] = useState<string | null>(null)
  const [errorNotice, setErrorNotice] = useState<string | null>(null)

  // Fetch user info to get department
  useEffect(() => {
    const fetchUserInfo = async () => {
      try {
        const userInfo = await apiFetch<UserInfoResponse>('/auth/name');
        if (userInfo) {
            // Handle both camelCase and PascalCase
            const major = userInfo.majorName || userInfo.MajorName;
            const name = userInfo.name || userInfo.Name;
            
            if (major) setUserDepartment(major);
            if (name) setUserName(name);
        }
      } catch (e) {
        console.error("Failed to fetch user info", e);
        setUserName(`Error: ${e instanceof Error ? e.message : 'Unknown'}`);
      }
    };
    fetchUserInfo();
  }, []);

  // Fetch items on load
  // 새로고침 버튼에서도 재사용할 수 있도록 useEffect 밖으로 분리
  const fetchItems = useCallback(async () => {
      setIsLoading(true);
      try {
        // Use the new endpoint that returns all items sorted by date
        const itemsData = await apiFetch<AdminItemResponse[]>('/admin/items');
        
        if (Array.isArray(itemsData)) {
          const mappedList: DepartmentKnowledge[] = itemsData.map((item) => {
             let title = '제목 없음';
             let content = '';
             let parsedData: Record<string, string | undefined> = {};
             
             try {
                parsedData = JSON.parse(item.data);
             } catch(e) { console.error('JSON parse error', e); }

             if (item.source_type === 'custom_knowledge') {
                 title = parsedData.question || '질문 없음';
                 content = parsedData.answer || '';
             } else if (item.source_type === 'event') {
                 title = `[행사] ${parsedData.title || ''}`;
                 content = `일시: ${parsedData.start_date} ~ ${parsedData.end_date}\n장소: ${parsedData.location}\n\n${parsedData.description}`;
             } else if (item.source_type === 'announcement') {
                 title = `[공지] ${parsedData.title || ''}`;
                 content = `게시일: ${parsedData.date}\n분류: ${parsedData.category}\n\n${parsedData.content}`;
             }
             
             // Map backend status to frontend status
             let status: 'PENDING' | 'APPROVED' | 'REJECTED' = 'PENDING';
             if (item.status === 'approved' || item.status === 'approved_manually') status = 'APPROVED';
             else if (item.status === 'rejected') status = 'REJECTED';
             else status = 'PENDING';

             return {
               id: item.id.toString(),
               title: title,
               content: content,
               status: status,
               createdAt: item.created_at
             };
          });
          setKnowledgeList(mappedList);
        }
      } catch (e) {
        console.error("Failed to load items", e);
      } finally {
        setIsLoading(false);
      }
  }, []);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

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
    setNotice(null)
    setErrorNotice(null)
    if (!newTitle.trim() || !newContent.trim()) {
      setErrorNotice('제목과 내용을 입력해주세요.')
      return
    }

    const deptToUse = userDepartment || '학과정보';

    let payloadData: KnowledgePayload | EventPayload | AnnouncementPayload;
    let sourceType = '';

    if (contentType === 'knowledge') {
      payloadData = {
        question: newTitle,
        answer: newContent,
        category: deptToUse,
        requester: userName
      }
      sourceType = 'custom_knowledge'
    } else if (contentType === 'event') {
      if (!startDate) {
        setErrorNotice('시작일을 입력해주세요.')
        return
      }
      payloadData = {
        title: newTitle,
        start_date: startDate,
        end_date: endDate || startDate,
        location: location,
        department: deptToUse,
        description: newContent,
        requester: userName
      }
      sourceType = 'event'
    } else {
      // contentType === 'announcement'
      if (!startDate) {
        setErrorNotice('게시일을 입력해주세요.')
        return
      }
      payloadData = {
        title: newTitle,
        content: newContent,
        date: startDate,
        category: category || '일반',
        department: deptToUse,
        requester: userName
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
        // Optimistically add to list (re-fetch will update status properly if needed)
        const newKnowledgeItem: DepartmentKnowledge = {
          id: submitResponse.id.toString(),
          title: `[${contentType === 'knowledge' ? '정보' : contentType === 'event' ? '행사' : '공지'}] ${newTitle}`,
          content: newContent,
          status: 'PENDING',
          createdAt: new Date().toISOString(),
        };
        setKnowledgeList((prev) => [newKnowledgeItem, ...prev])
        setNotice('성공적으로 제출되었습니다. 검수 승인 후 챗봇에 반영됩니다.')
      } else {
        setErrorNotice('제출에 실패했습니다. 응답 형식이 올바르지 않습니다.');
      }
      
      setIsCreating(false)
      setNewTitle('')
      setNewContent('')
      
    } catch (e) {
      console.error('Failed to submit', e)
      setErrorNotice('제출에 실패했습니다. 다시 시도해주세요.')
    } finally {
      setIsLoading(false)
    }
  }

  const handleDelete = async (id: string) => {
    if (!confirm('요청을 취소하시겠습니까? (반려 처리됩니다)')) return
    setNotice(null)
    setErrorNotice(null)

    try {
      setIsLoading(true)
      
      if (!id.startsWith('k-')) {
         await apiFetch(`/admin/reject/${id}`, { method: 'POST' })
      }

      // Update status instead of removing
      setKnowledgeList((prev) => prev.map((item) => 
        item.id === id ? { ...item, status: 'REJECTED' } : item
      ))
      
      setNotice('요청이 취소되었습니다.')
    } catch (e) {
      console.error('Failed to cancel request', e)
      setErrorNotice('요청 취소에 실패했습니다. 다시 시도해주세요.')
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
      case 'REJECTED': return 'danger'
      case 'PENDING': return 'pending'
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
              <p className="admin-subtitle compact" style={{ fontSize: '0.8rem', opacity: 0.8 }}>
                접속: {userName || userDepartment || '로딩 중...'}
              </p>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button className="ghost-btn" type="button" onClick={() => fetchItems()} disabled={isLoading}>
                {isLoading ? '새로고침 중...' : '새로고침'}
              </button>
              <button className="hero-btn hero-btn--primary" type="button" onClick={handleNavigateHome}>
                메인페이지로 이동
              </button>
            </div>
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
          <section className="admin-panel glass-panel full-height" style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: '500px' }}>
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
          <section className="admin-panel glass-panel full-height" style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: '500px' }}>
            <div className="admin-panel__column full-height admin-panel__column--detail" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                <div className="admin-panel-content-scroll admin-review-detail-scroll" style={{ flex: 1, overflowY: 'auto' }}>
                  {isCreating ? (
                    <div className="admin-review-detail" style={{ border: 'none', background: 'transparent', padding: 0 }}>
                      <p className="admin-review-detail__eyebrow">새 정보 등록</p>
                      <h3 className="admin-review-detail__title">정보 입력</h3>
                      
                      <form onSubmit={handleSubmit}>
                        <div className="admin-form-field">
                          <label className="admin-form-label">등록 유형</label>
                          <select 
                            className="admin-input" 
                            value={contentType} 
                            onChange={(e) => setContentType(e.target.value as 'knowledge' | 'event' | 'announcement')}
                            disabled={isLoading}
                          >
                            <option value="knowledge">❓ 자주 묻는 질문 (FAQ)</option>
                            <option value="event">📅 학과 행사 (Event)</option>
                            <option value="announcement">📢 공지사항 (Notice)</option>
                          </select>
                        </div>

                        <div className="admin-form-field">
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
                          <div className="admin-form-field" style={{ display: 'flex', gap: '10px' }}>
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
                          <div className="admin-form-field">
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
                          <div className="admin-form-field">
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
                        
                        <div className="admin-form-field">
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

                        {notice && (
                          <div style={{ marginTop: '12px', color: '#15803d', background: '#f0fdf4', padding: '10px 14px', borderRadius: '8px' }}>
                            {notice}
                          </div>
                        )}
                        {errorNotice && (
                          <div className="admin-alert admin-alert--danger" style={{ marginTop: '12px' }}>
                            {errorNotice}
                          </div>
                        )}

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
                        {/* 승인/반려 완료 항목에는 취소 버튼을 노출하지 않는다 — 승인된 항목을 실수로 반려 처리하는 사고 방지 */}
                        {selectedItem.status === 'PENDING' && (
                          <button className="ghost-btn small ghost-btn--danger" onClick={() => handleDelete(selectedItem.id)} disabled={isLoading}>
                            {isLoading ? '처리 중...' : '요청 취소'}
                          </button>
                        )}
                      </div>

                      {notice && (
                        <div style={{ marginTop: '12px', color: '#15803d', background: '#f0fdf4', padding: '10px 14px', borderRadius: '8px' }}>
                          {notice}
                        </div>
                      )}
                      {errorNotice && (
                        <div className="admin-alert admin-alert--danger" style={{ marginTop: '12px' }}>
                          {errorNotice}
                        </div>
                      )}
                      
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