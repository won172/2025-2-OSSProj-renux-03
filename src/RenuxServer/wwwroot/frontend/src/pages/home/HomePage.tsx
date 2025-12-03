import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import donggukLogo from '../../assets/images/dongguk-logo.png'
import dongddokiLogo from '../../assets/images/dongddoki-logo.png'
import type { Department } from '../../types/organization'
import type { ActiveChat } from '../../types/chat'
import type { AuthNameResponse, UserRole } from '../../types/auth'

type ChatPageMessage = {
  id: string
  chatId: string
  isAsk: boolean
  content: string
  createdTime: string | number
}

const mapRoleNameToUserRole = (roleName?: string | null): UserRole => {
  if (!roleName) return 'STUDENT'
  const normalized = roleName.trim().toLowerCase()
  if (normalized.includes('총학생회') || normalized.includes('교직원') || normalized.includes('관리자')) {
    return 'UNIVERSITY_COUNCIL'
  }
  if (normalized.includes('학생회')) {
    return 'DEPARTMENT_COUNCIL'
  }
  return 'STUDENT'
}

const HomePage = () => {
  const navigate = useNavigate()
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [userName, setUserName] = useState<string | null>(null)
  const [departments, setDepartments] = useState<Department[]>([])
  const [departmentsLoading, setDepartmentsLoading] = useState(true)
  const [activeChats, setActiveChats] = useState<ActiveChat[]>([])
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [selectedDepartmentId, setSelectedDepartmentId] = useState('')
  const [chatRoomTitle, setChatRoomTitle] = useState('')
  const [isCreatingChat, setIsCreatingChat] = useState(false)
  const [createChatError, setCreateChatError] = useState<string | null>(null)
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null)
  const [selectedChatTitle, setSelectedChatTitle] = useState<string | null>(null)
  const [chatMessages, setChatMessages] = useState<ChatPageMessage[]>([])
  const [chatLoading, setChatLoading] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [hasMoreMessages, setHasMoreMessages] = useState(true)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [chatInput, setChatInput] = useState('')
  const [chatSending, setChatSending] = useState(false)
  const [userRole, setUserRole] = useState<UserRole>(() => {
    if (typeof window === 'undefined') return 'STUDENT'
    const stored = window.localStorage.getItem('renux-user-role')
    if (stored === 'DEPARTMENT_COUNCIL' || stored === 'UNIVERSITY_COUNCIL') return stored
    return 'STUDENT'
  })
  const [departmentName, setDepartmentName] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Mobile sidebar state
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    if (!isLoadingMore) {
      scrollToBottom()
    }
  }, [chatMessages, isLoadingMore])
  
  // Close sidebar when switching chats on mobile
  useEffect(() => {
    setIsSidebarOpen(false)
  }, [selectedChatId])

  const isNewChatDisabled = useMemo(() => {
    if (departmentsLoading) return true
    return departments.length === 0
  }, [departments, departmentsLoading])

  useEffect(() => {
    const loadDepartments = async () => {
      setDepartmentsLoading(true)
      try {
        const data = await apiFetch<Department[]>('/req/orgs', { method: 'GET' })
        if (Array.isArray(data)) {
          setDepartments(data)
        } else {
          setDepartments([])
        }
      } catch (error) {
        console.error('Failed to load departments; switching to demo data', error)
        setDepartments([])
      } finally {
        setDepartmentsLoading(false)
      }
    }
    loadDepartments()
  }, [])

  useEffect(() => {
    const checkLoginStatus = async () => {
      try {
        const data = await apiFetch<AuthNameResponse>('/auth/name', { method: 'GET' })
        if (data?.name) {
          setIsAuthenticated(true)
          setUserName(data.name)
          if (data.role) {
            const resolvedRole = mapRoleNameToUserRole(data.role)
            setUserRole(resolvedRole)
            if (typeof window !== 'undefined') {
              window.localStorage.setItem('renux-user-role', resolvedRole)
            }
          }
          if (data.departmentName) {
            setDepartmentName(data.departmentName)
          }
        }
      } catch (error) {
        console.log('User is not logged in', error)
        setIsAuthenticated(false)
        setUserName(null)
      }
    }
    checkLoginStatus()
  }, [])

  useEffect(() => {
    if (!isAuthenticated) return

    const fetchActiveChats = async () => {
      try {
        const data = await apiFetch<ActiveChat[]>('/chat/active', { method: 'GET' })
        if (Array.isArray(data)) {
          setActiveChats(data)
          if (!selectedChatId && data.length > 0) {
            setSelectedChatId(data[0].id)
            setSelectedChatTitle(data[0].title ?? '채팅방')
          }
        }
      } catch (error) {
        console.error('Failed to load active chats', error)
        setActiveChats([])
      }
    }
    fetchActiveChats()
  }, [isAuthenticated, selectedChatId])

  useEffect(() => {
    document.body.classList.toggle('modal-open', isModalOpen)
    return () => {
      document.body.classList.remove('modal-open')
    }
  }, [isModalOpen])

  const toggleModal = (open: boolean) => {
    setCreateChatError(null)
    setSelectedDepartmentId('')
    setChatRoomTitle('')
    setIsModalOpen(open)
  }

  const handleNewChatClick = () => {
    if (!isAuthenticated) {
      navigate('/auth/in')
      return
    }
    toggleModal(true)
  }

  const handleModalClose = () => {
    toggleModal(false)
  }

  const handleCreateChat = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setCreateChatError(null)

    if (!selectedDepartmentId) {
      setCreateChatError('학과를 먼저 선택해주세요.')
      return
    }

    const trimmedTitle = chatRoomTitle.trim()
    if (!trimmedTitle) {
      setCreateChatError('채팅방 제목을 입력해주세요.')
      return
    }

    const selectedOrg = departments.find((dept) => dept.id === selectedDepartmentId)
    if (!selectedOrg || !selectedOrg.major?.id) {
      setCreateChatError('선택한 학과 정보를 불러오지 못했습니다.')
      return
    }

    try {
      setIsCreatingChat(true)
      const chatRoom = await apiFetch<ActiveChat>('/chat/start', {
        method: 'POST',
        json: { org: selectedOrg, title: trimmedTitle },
      })
      toggleModal(false)
      setSelectedChatId(chatRoom.id)
      setSelectedChatTitle(chatRoom.title ?? trimmedTitle)
      setChatMessages([])
      setChatError(null)
    } catch (error) {
      console.error('Failed to create chat room', error)
      setCreateChatError('채팅방을 생성하지 못했습니다. 잠시 후 다시 시도해주세요.')
    } finally {
      setIsCreatingChat(false)
    }
  }

  const handleLogin = () => {
    navigate('/auth/in')
  }

  const handleSignup = () => navigate('/auth/up')
  const handleLogout = async () => {
    try {
      await apiFetch('/auth/signout', { method: 'GET' })
	window.location.reload()
    } catch (error) {
      console.error('Failed to logout', error)
      alert('로그아웃에 실패했습니다. 다시 시도해주세요.')
    }
  }

  const handleOpenUniversityAdmin = () => {
    navigate('/admin/university')
  }

  const handleOpenDepartmentAdmin = () => {
    navigate('/admin/department')
  }

  const handleSelectChat = (chat: ActiveChat) => {
    setSelectedChatId(chat.id)
    setSelectedChatTitle(chat.title ?? '채팅방')
    setChatMessages([])
    setChatError(null)
  }

  const loadMessages = async (chatIdToLoad: string) => {
    try {
      setChatLoading(true)
      setChatError(null)
      setHasMoreMessages(true)
      const data = await apiFetch<ChatPageMessage[]>('/chat/load', {
        method: 'POST',
        json: { chatId: chatIdToLoad, lastTime: new Date().toISOString() },
      })
      if (Array.isArray(data)) {
        setChatMessages(data.reverse())
        if (data.length < 20) setHasMoreMessages(false)
        setTimeout(scrollToBottom, 100) 
      } else {
        setChatMessages([])
        setHasMoreMessages(false)
      }
    } catch (err) {
      console.error('Failed to load messages', err)
      setChatError('채팅 메시지를 불러오지 못했습니다.')
      setChatMessages([])
    } finally {
      setChatLoading(false)
    }
  }

  const loadMoreMessages = async () => {
    if (!selectedChatId || isLoadingMore || !hasMoreMessages || chatMessages.length === 0) return

    const firstMessageTime = chatMessages[0].createdTime
    const container = document.querySelector('.home-chat__thread-wrapper') as HTMLDivElement
    const prevScrollHeight = container?.scrollHeight ?? 0

    try {
      setIsLoadingMore(true)
      const data = await apiFetch<ChatPageMessage[]>('/chat/load', {
        method: 'POST',
        json: { chatId: selectedChatId, lastTime: firstMessageTime },
      })
      if (Array.isArray(data) && data.length > 0) {
        const newMessages = data.reverse()
        setChatMessages((prev) => [...newMessages, ...prev])
        
        setTimeout(() => {
            if (container) {
                container.scrollTop = container.scrollHeight - prevScrollHeight
            }
        }, 0)

        if (data.length < 20) setHasMoreMessages(false)
      } else {
        setHasMoreMessages(false)
      }
    } catch (err) {
      console.error('Failed to load older messages', err)
    } finally {
      setIsLoadingMore(false)
    }
  }

  useEffect(() => {
    if (!selectedChatId || !isAuthenticated) return
    loadMessages(selectedChatId)
  }, [selectedChatId, isAuthenticated])

  const handleChatSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selectedChatId || !isAuthenticated) {
      setChatError('채팅방을 선택하거나 로그인 후 이용해주세요.')
      return
    }
    const trimmed = chatInput.trim()
    if (!trimmed) {
      setChatError('메시지를 입력해주세요.')
      return
    }

    const newMsg: ChatPageMessage = {
      id: typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
      chatId: selectedChatId,
      isAsk: true,
      content: trimmed,
      createdTime: new Date().toISOString(),
    }

    setChatMessages((prev) => [...prev, newMsg])
    setChatInput('')
    setChatSending(true)
    setChatError(null)
    
    setTimeout(scrollToBottom, 0)

    try {
      const reply = await apiFetch<ChatPageMessage>('/chat/msg', {
        method: 'POST',
        json: {
          id: newMsg.id,
          chatId: newMsg.chatId,
          isAsk: newMsg.isAsk,
          content: newMsg.content,
          createdTime: newMsg.createdTime,
        },
      })
      
      if (reply) {
        setChatMessages((prev) => [...prev, reply])
        setTimeout(scrollToBottom, 0)
      }
    } catch (err) {
      console.error('Failed to send message', err)
      setChatError('메시지를 전송하지 못했습니다.')
      setChatMessages((prev) => prev.filter((msg) => msg.id !== newMsg.id))
      setChatInput(trimmed)
    } finally {
      setChatSending(false)
    }
  }

  const isHeroPrimaryDisabled = isAuthenticated && isNewChatDisabled
  const displayName = isAuthenticated ? userName ?? '로그인 사용자' : '게스트'
  const fallbackDept = departments[0]?.major?.majorname ?? (userRole === 'UNIVERSITY_COUNCIL' ? '총학생회' : null) // 우선 디폴트로 총학생회.
  const displayDept = departmentName ?? fallbackDept 
  const roleLabelMap: Record<UserRole, string> = {
    STUDENT: '일반학생',
    DEPARTMENT_COUNCIL: '학생회',
    UNIVERSITY_COUNCIL: '총학생회',
  }
  const roleLabel = roleLabelMap[userRole] // '일반학생'
  const showDeptAdminButton = isAuthenticated && userRole === 'DEPARTMENT_COUNCIL' // '학생회'
  const showUnivAdminButton = isAuthenticated && userRole === 'UNIVERSITY_COUNCIL' // '총학생회'
  const visibleChats = activeChats.length > 0 ? activeChats : [] 

  const formatMessageTime = (value?: string | number) => {
    if (!value) return ''
    const date = typeof value === 'number' ? new Date(value) : new Date(value)
    if (Number.isNaN(date.getTime())) return ''
    return new Intl.DateTimeFormat('ko-KR', { hour: 'numeric', minute: '2-digit' }).format(date)
  }

  return (
    <div className="gpt-home">
      {/* Mobile Backdrop */}
      <div 
        className={`mobile-backdrop ${isSidebarOpen ? 'open' : ''}`} 
        onClick={() => setIsSidebarOpen(false)}
      />

      <aside className={`gpt-home__sidebar ${isSidebarOpen ? 'mobile-open' : ''}`}>
        <div className="gpt-home__brand">
          <div className="home-logo-row">
            <img src={donggukLogo} alt="Dongguk University" className="home-logo home-logo--univ" />
          </div>

        </div>

        <button type="button" className="gpt-home__new" onClick={handleNewChatClick} disabled={isHeroPrimaryDisabled}>
          + 새 대화
        </button>

        <div className="gpt-home__section">
          <div className="gpt-home__section-head">
            <h3>최근 대화</h3>
          </div>
          <ul className="gpt-home__chat-list">
            {visibleChats.map((chat) => (
              <li key={chat.id} className="gpt-home__chat-item" onClick={() => handleSelectChat(chat)}>
                <span className="gpt-home__chat-title">{chat.title ?? '제목 없음'}</span>
                <span className="gpt-home__chat-sub">대화 이어가기</span>
              </li>
            ))}
            {visibleChats.length === 0 && (
              <li className="gpt-home__chat-empty">아직 대화가 없습니다. 새 대화를 시작해보세요.</li>
            )}
          </ul>
        </div>

        {/* Mobile Sidebar Footer (Account Actions) */}
        <div className="gpt-home__sidebar-footer mobile-only">
            <div className="gpt-home__section">
                <div className="gpt-home__section-head">
                    <h3>내 계정</h3>
                    {isAuthenticated && <span className="gpt-home__pill">{roleLabel}</span>}
                </div>
                <div className="gpt-home__actions">
                    {isAuthenticated ? (
                        <>
                            {showUnivAdminButton && (
                                <button className="ghost-btn small ghost-btn--accent" type="button" onClick={handleOpenUniversityAdmin}>
                                    관리자
                                </button>
                            )}
                            {showDeptAdminButton && (
                                <button className="ghost-btn small" type="button" onClick={handleOpenDepartmentAdmin}>
                                    학과 관리자
                                </button>
                            )}
                            <button className="ghost-btn small" type="button" onClick={handleLogout}>
                                로그아웃
                            </button>
                        </>
                    ) : (
                        <>
                            <button className="ghost-btn small" type="button" onClick={handleLogin}>
                                로그인
                            </button>
                            <button className="ghost-btn small" type="button" onClick={handleSignup}>
                                회원가입
                            </button>
                        </>
                    )}
                </div>
            </div>
        </div>
      </aside>

      <div className="buddy-topbar">
          <div className="buddy-topbar__brand">
            <button 
              type="button" 
              className="mobile-menu-btn"
              onClick={() => setIsSidebarOpen(true)}
            >
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="3" y1="12" x2="21" y2="12"></line>
                <line x1="3" y1="6" x2="21" y2="6"></line>
                <line x1="3" y1="18" x2="21" y2="18"></line>
              </svg>
            </button>
            <div className="buddy-topbar__icon buddy-topbar__icon--image">
              <img src={dongddokiLogo} alt="동똑이 로고" className="buddy-topbar__logo" />
            </div>
            <div>
              {/* <p className="buddy-topbar__eyebrow">DONGGUK BUDDY AI</p> */}
              <p className="buddy-topbar__title">동국대학교 동똑이</p>
            </div>
          </div>
          <div className="buddy-topbar__meta">
            {isAuthenticated ? (
              <>
                <span className="buddy-topbar__text">{displayName}</span>
                {displayDept && (
                  <>
                    <span className="buddy-topbar__dot">·</span>
                    <span className="buddy-topbar__text buddy-topbar__text--muted">{displayDept}</span>
                  </>
                )}
                <span className="buddy-topbar__badge">{roleLabel}</span>
              </>
            ) : (
              <span className="buddy-topbar__text buddy-topbar__text--muted">로그인하면 개인화 정보가 표시됩니다</span>
            )}
          </div>
          <div className="buddy-topbar__meta buddy-topbar__meta--actions">
            {showUnivAdminButton && (
              <button className="ghost-btn small ghost-btn--accent" type="button" onClick={handleOpenUniversityAdmin}>
                관리자
              </button>
            )}
            {showDeptAdminButton && (
              <button className="ghost-btn small" type="button" onClick={handleOpenDepartmentAdmin}>
                학과 관리자
              </button>
            )}
            {isAuthenticated ? (
              <button className="ghost-btn small" type="button" onClick={handleLogout}>
                로그아웃
              </button>
            ) : (
              <>
                <button className="ghost-btn small" type="button" onClick={handleLogin}>
                  로그인
                </button>
                <button className="ghost-btn small" type="button" onClick={handleSignup}>
                  회원가입
                </button>
              </>
            )}
          </div>
        </div>

        <main className="gpt-home__main gpt-home__main--chat">
          <section className="home-chat glass-panel home-chat--fullheight">
            <div className="home-chat__header">
              <div>
                <p className="chatbot-hero__badge">동국대학교 재학생 맞춤형 정보 제공 챗봇</p>
                {selectedChatTitle && <h2 className="home-chat__title">{selectedChatTitle}</h2>}
              </div>
            </div>

            <div
              className="home-chat__thread-wrapper"
              onScroll={(e) => {
                const target = e.currentTarget
                if (target.scrollTop === 0 && hasMoreMessages && !isLoadingMore) {
                  loadMoreMessages()
                }
              }}
            >
              {chatLoading ? (
                <p className="home-chat__status">채팅을 불러오는 중...</p>
              ) : chatError ? (
                <p className="home-chat__status home-chat__status--error">{chatError}</p>
              ) : !selectedChatId ? (
                <p className="home-chat__status">채팅방을 선택하거나 새로 만드세요.</p>
              ) : chatMessages.length === 0 ? (
                <div className="home-chat__empty">아직 메시지가 없습니다. 첫 메시지를 보내보세요.</div>
              ) : (
                <ul className="chat-bubbles">
                  {isLoadingMore && <li className="home-chat__status"><small>이전 대화 불러오는 중...</small></li>}
                  {chatMessages.map((message) => {
                    const messageTime = formatMessageTime(message.createdTime)
                    return (
                      <li
                        key={message.id}
                        className={`chat-bubble ${message.isAsk ? 'chat-bubble--user' : 'chat-bubble--bot'}`}
                      >
                        <span className="chat-bubble__text">{message.content}</span>
                        {messageTime && <time className="chat-bubble__time">{messageTime}</time>}
                      </li>
                    )
                  })}
                  {chatSending && (
                    <li className="chat-bubble chat-bubble--bot">
                      <div className="typing-indicator">
                        <div className="typing-dot"></div>
                        <div className="typing-dot"></div>
                        <div className="typing-dot"></div>
                      </div>
                    </li>
                  )}
                  <div ref={messagesEndRef} />
                </ul>
              )}
            </div>

            <form className="home-chat__composer" onSubmit={handleChatSubmit}>
              <div className="home-chat__input-wrapper">
                <textarea
                  className="home-chat__input"
                  placeholder={isAuthenticated ? '무엇이든 입력하세요' : '로그인 후 이용 가능합니다'}
                  value={chatInput}
                  onChange={(event) => setChatInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault()
                      handleChatSubmit(event as any)
                    }
                  }}
                  rows={3}
                  disabled={chatSending || !isAuthenticated || !selectedChatId}
                />
                <button
                  className="hero-btn hero-btn--primary home-chat__send-btn"
                  type="submit"
                  disabled={chatSending || !isAuthenticated || !selectedChatId}
                >
                  {chatSending ? '전송' : '보내기'}
                </button>
              </div>
              {chatError && <span className="home-chat__error">{chatError}</span>}
            </form>
          </section>
        </main>
      {isModalOpen && (
        <div className="modal fade show" style={{ display: 'block' }} role="dialog">
          <div className="modal-dialog">
            <div className="modal-content">
              <div className="modal-header">
                <h5 className="modal-title">새 채팅 만들기</h5>
                <button type="button" className="btn-close" aria-label="Close" onClick={handleModalClose} />
              </div>
              <form onSubmit={handleCreateChat}>
                <div className="modal-body">
                  {createChatError && <div className="alert alert-danger">{createChatError}</div>}
                  <div className="mb-3">
                    <label className="form-label" htmlFor="department-select">
                      학과 선택
                    </label>
                    <select
                      id="department-select"
                      className="form-select"
                      value={selectedDepartmentId}
                      onChange={(event) => setSelectedDepartmentId(event.target.value)}
                      disabled={departmentsLoading || isCreatingChat}
                    >
                      <option value="">학과를 선택하세요</option>
                      {departments.map((department) => (
                        <option key={department.id} value={department.id}>
                          {department.major?.majorname ?? '학과명 없음'}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="mb-3">
                    <label className="form-label" htmlFor="chat-title-input">
                      채팅방 제목
                    </label>
                    <input
                      id="chat-title-input"
                      type="text"
                      className="form-control"
                      value={chatRoomTitle}
                      onChange={(event) => setChatRoomTitle(event.target.value)}
                      disabled={isCreatingChat}
                      placeholder="예: 장학금 상담"
                    />
                  </div>
                </div>
                <div className="modal-footer">
                  <button type="button" className="btn btn-secondary" onClick={handleModalClose}>
                    닫기
                  </button>
                  <button type="submit" className="btn btn-primary" disabled={isCreatingChat || isNewChatDisabled}>
                    {isCreatingChat ? '생성 중...' : '생성'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
      {isModalOpen && <div className="modal-backdrop fade show" />}
    </div>
  )
}

export default HomePage