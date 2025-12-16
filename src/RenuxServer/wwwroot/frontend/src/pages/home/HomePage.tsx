import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import rehypeExternalLinks from 'rehype-external-links'
import remarkGfm from 'remark-gfm'
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
  if (normalized.includes('관리자')) {
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
  const skipLoadOnSelectRef = useRef(false)
  const isLoadingMoreRef = useRef(false)

  // Mobile sidebar state
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  // Removed useEffect for auto-scrolling to bottom on chatMessages change
  // to prevent scrolling to bottom when loading older messages.
  
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
          
          const rawRole = data.roleName || data.role
          if (rawRole) {
            const resolvedRole = mapRoleNameToUserRole(rawRole)
            setUserRole(resolvedRole)
            if (typeof window !== 'undefined') {
              window.localStorage.setItem('renux-user-role', resolvedRole)
            }
          }
          if (data.majorName) {
            setDepartmentName(data.majorName)
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
    const fetchActiveChats = async () => {
      if (isAuthenticated) {
        try {
          const data = await apiFetch<ActiveChat[]>('/chat/active', { method: 'GET' })
          if (Array.isArray(data)) {
            setActiveChats(data)
            if (!selectedChatId && data.length > 0) {
              // 로그인 시에는 기존 선택 로직 유지 또는 필요 시 변경
            }
          }
        } catch (error) {
          console.error('Failed to load active chats', error)
          setActiveChats([])
        }
      } else {
        // 게스트인 경우 로컬 스토리지에서 불러오지 않음 (저장 안 함)
        setActiveChats([])
      }
    }
    fetchActiveChats()
  }, [isAuthenticated])

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
      if (!isAuthenticated) {
        saveGuestChat(chatRoom)
      }

      setActiveChats((prev) => [chatRoom, ...prev])
      
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
    if (!selectedChatId || isLoadingMoreRef.current || !hasMoreMessages || chatMessages.length === 0) return

    const firstMessageTime = chatMessages[0].createdTime
    const container = document.querySelector('.home-chat__thread-wrapper') as HTMLDivElement
    const prevScrollHeight = container?.scrollHeight ?? 0

    try {
      setIsLoadingMore(true)
      isLoadingMoreRef.current = true
      
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
      // 약간의 지연을 두어 상태 업데이트가 완료된 후 플래그를 해제 (안전장치)
      setTimeout(() => {
        isLoadingMoreRef.current = false
      }, 100)
    }
  }

  useEffect(() => {
    if (!selectedChatId) return
    if (skipLoadOnSelectRef.current) {
      skipLoadOnSelectRef.current = false
      return
    }
    loadMessages(selectedChatId)
  }, [selectedChatId])

  const handleChatSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    
    const trimmed = chatInput.trim()
    if (!trimmed) {
      setChatError('메시지를 입력해주세요.')
      return
    }

    let currentChatId = selectedChatId

    // 채팅방이 없으면 자동 생성
    if (!currentChatId) {
      if (departments.length === 0) {
        setChatError('채팅을 시작할 수 있는 학과 정보가 없습니다.')
        return
      }

      try {
        setChatSending(true)
        const defaultOrg = departments[0]
        const title = trimmed.length > 20 ? trimmed.substring(0, 20) + '...' : trimmed
        
        const chatRoom = await apiFetch<ActiveChat>('/chat/start', {
          method: 'POST',
          json: { org: defaultOrg, title },
        })

        if (!isAuthenticated) {
          // saveGuestChat(chatRoom)
        }

        currentChatId = chatRoom.id
        setActiveChats((prev) => [chatRoom, ...prev])
        
        // 중요: useEffect에 의한 loadMessages가 실행되지 않도록 플래그 설정
        skipLoadOnSelectRef.current = true
        setSelectedChatId(chatRoom.id)
        setSelectedChatTitle(chatRoom.title ?? title)

        // 방금 생성된 방의 환영 메시지를 수동으로 가져옴
        const initialData = await apiFetch<ChatPageMessage[]>('/chat/load', {
          method: 'POST',
          json: { chatId: chatRoom.id, lastTime: new Date().toISOString() },
        })
        
        // 환영 메시지 설정 (있다면)
        if (Array.isArray(initialData)) {
           setChatMessages(initialData.reverse())
        } else {
           setChatMessages([])
        }

      } catch (error) {
        console.error('Failed to auto-create chat room', error)
        setChatError('채팅방을 생성하지 못했습니다.')
        setChatSending(false)
        return
      }
    }

    // 여기부터는 currentChatId가 반드시 존재함
    const newMsg: ChatPageMessage = {
      id: typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
      chatId: currentChatId,
      isAsk: true,
      content: trimmed,
      createdTime: new Date().toISOString(),
    }

    // 기존 메시지 목록 뒤에 내 메시지 추가
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
        setTimeout(scrollToBottom, 100)
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

  const isHeroPrimaryDisabled = isNewChatDisabled
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

  const loadGuestChats = (): ActiveChat[] => {
    if (typeof window === 'undefined') return []
    try {
      const stored = window.localStorage.getItem('renux-guest-chats')
      return stored ? JSON.parse(stored) : []
    } catch (e) {
      console.error('Failed to load guest chats', e)
      return []
    }
  }

  const saveGuestChat = (chat: ActiveChat) => {
    if (typeof window === 'undefined') return
    try {
      const current = loadGuestChats()
      // 중복 방지
      if (current.find((c) => c.id === chat.id)) return
      const updated = [chat, ...current]
      window.localStorage.setItem('renux-guest-chats', JSON.stringify(updated))
    } catch (e) {
      console.error('Failed to save guest chat', e)
    }
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
                <div className="home-guide">
                  <h3>동똑이 사용 가이드</h3>
                  <ol>
                    <li>
                      <strong>새 대화 시작하기</strong>
                      <p>좌측 사이드바의 <em>+ 새 대화</em> 버튼을 클릭하여 채팅방을 생성하세요.</p>
                    </li>
                    <li>
                      <strong>질문하기</strong>
                      <p>학사 일정, 장학금, 규정 등 궁금한 내용을 자유롭게 질문하세요.</p>
                    </li>
                    <li>
                      <strong>로그인 혜택</strong>
                      <p>로그인하면 대화 내역이 저장되고, 소속 학과에 맞는 맞춤형 답변을 받을 수 있습니다.</p>
                    </li>
                  </ol>
                </div>
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
                        <ReactMarkdown
                          className="chat-bubble__text"
                          remarkPlugins={[remarkGfm]}
                          rehypePlugins={[[rehypeExternalLinks, { target: '_blank', rel: ['noopener', 'noreferrer'] }]]}
                          components={{
                            a: ({ node, ...props }) => (
                              <a
                                {...props}
                                target="_blank"
                                rel="noopener noreferrer"
                                style={{ color: '#0d6efd', textDecoration: 'underline', pointerEvents: 'auto', cursor: 'pointer' }}
                            onClick={(e) => e.stopPropagation()}
                              />
                            ),
                          }}
                        >
                          {message.content}
                        </ReactMarkdown>
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
                  placeholder={selectedChatId ? '무엇이든 물어보세요' : '무엇이든 물어보세요 (새 대화가 자동으로 시작됩니다)'}
                  value={chatInput}
                  onChange={(event) => setChatInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault()
                      handleChatSubmit(event as any)
                    }
                  }}
                  rows={3}
                  disabled={chatSending || (departmentsLoading && !selectedChatId)}
                />
                <button
                  className="hero-btn hero-btn--primary home-chat__send-btn"
                  type="submit"
                  disabled={chatSending || (departmentsLoading && !selectedChatId)}
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