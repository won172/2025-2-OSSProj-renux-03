import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import { useChatStream } from '../../hooks/useChatStream'
import donggukLogo from '../../assets/images/dongguk-logo.png'
import dongddokiLogo from '../../assets/images/dongddoki-logo.png'
import ChatMarkdown from '../../components/chat/ChatMarkdown'
import CopyButton from '../../components/chat/CopyButton'
import MessageFeedback from '../../components/chat/MessageFeedback'
import RegenerateButton from '../../components/chat/RegenerateButton'
import SuggestedQuestions from '../../components/chat/SuggestedQuestions'
import SourceCards, { type ChatSource } from '../../components/chat/SourceCards'
import type { Department } from '../../types/organization'
import type { ActiveChat } from '../../types/chat'
import type { AuthNameResponse, UserRole } from '../../types/auth'

type ChatPageMessage = {
  id: string
  chatId: string
  isAsk: boolean
  content: string
  createdTime: string | number
  sources?: ChatSource[] | null
  requestId?: string
  isFallback?: boolean
  fallbackReason?: string | null
  suggestedQuestions?: string[]
  grounded?: boolean
  groundingScore?: number
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

const getFallbackLabel = (reason?: string | null) => {
  if (reason === 'date_filter_eliminated_all') return '날짜 범위 재확인'
  if (reason === 'score_below_threshold') return '근거 약함'
  if (reason === 'dataset_unavailable') return '일시적 조회 실패'
  return '근거 부족'
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
  const [activeCitation, setActiveCitation] = useState<{ messageId: string; citationNumber: number } | null>(null)
  const [userRole, setUserRole] = useState<UserRole>(() => {
    if (typeof window === 'undefined') return 'STUDENT'
    const stored = window.localStorage.getItem('renux-user-role')
    if (stored === 'DEPARTMENT_COUNCIL' || stored === 'UNIVERSITY_COUNCIL') return stored
    return 'STUDENT'
  })
  const [departmentName, setDepartmentName] = useState<string | null>(null)
  const [entryYear, setEntryYear] = useState<string | null>(null)
  const chatInputRef = useRef<HTMLTextAreaElement | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const skipLoadOnSelectRef = useRef(false)
  const isLoadingMoreRef = useRef(false)
  const { streamMessage } = useChatStream()

  // Mobile sidebar state
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

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
          if (data.entryYear) {
            setEntryYear(String(data.entryYear))
          }
        }
      } catch (error) {
        console.log('User is not logged in', error)
        setIsAuthenticated(false)
        setUserName(null)
        setDepartmentName(null)
        setEntryYear(null)
        setUserRole('STUDENT')
        window.localStorage.removeItem('renux-user-role')
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

  const handleSignup = () => {
    navigate('/auth/up')
  }

  const handleLogout = async () => {
    try {
      await apiFetch('/auth/signout', { method: 'POST' })
      // 이전 세션의 역할이 다음 사용자에게 노출되지 않도록 캐시 제거
      window.localStorage.removeItem('renux-user-role')
      window.location.reload()
    } catch (error) {
      console.error('Failed to logout', error)
      // alert() 대신 인라인 표시 — 다른 에러 처리 패턴과 통일
      setChatError('로그아웃에 실패했습니다. 다시 시도해주세요.')
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

  const loadMessages = useCallback(async (chatIdToLoad: string) => {
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
  }, [scrollToBottom])

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
  }, [loadMessages, selectedChatId])

  const sendChatMessage = async (text: string, chatId: string | number) => {
    const resolvedChatId = String(chatId)
    const newMsg: ChatPageMessage = {
      id: typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
      chatId: resolvedChatId,
      isAsk: true,
      content: text,
      createdTime: new Date().toISOString(),
    }

    // 스트리밍 토큰을 채워 넣을 빈 봇 말풍선을 미리 추가(내용이 비면 타이핑 인디케이터로 렌더)
    const botMessageId = typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : `bot-${Date.now()}`
    const botPlaceholder: ChatPageMessage = {
      id: botMessageId,
      chatId: resolvedChatId,
      isAsk: false,
      content: '',
      createdTime: new Date().toISOString(),
      sources: [],
    }

    // 내 메시지 + 빈 봇 말풍선을 함께 추가
    setChatMessages((prev) => [...prev, newMsg, botPlaceholder])
    setChatSending(true)
    setChatError(null)

    setTimeout(scrollToBottom, 0)

    try {
      const { receivedAny } = await streamMessage(
        { id: newMsg.id, chatId: resolvedChatId, content: text, createdTime: newMsg.createdTime },
        {
          onText: (accumulated) => {
            setChatMessages((prev) =>
              prev.map((msg) => (msg.id === botMessageId ? { ...msg, content: accumulated } : msg)),
            )
            setTimeout(scrollToBottom, 0)
          },
          onMetadata: (meta) =>
            setChatMessages((prev) =>
              prev.map((msg) =>
                msg.id === botMessageId
                  ? {
                      ...msg,
                      sources: meta.sources,
                      requestId: meta.requestId,
                      isFallback: meta.isFallback,
                      fallbackReason: meta.fallbackReason,
                    }
                  : msg,
              ),
            ),
          onSuggestions: (questions) =>
            setChatMessages((prev) =>
              prev.map((msg) => (msg.id === botMessageId ? { ...msg, suggestedQuestions: questions } : msg)),
            ),
          onGrounding: ({ grounded, groundingScore }) =>
            setChatMessages((prev) =>
              prev.map((msg) => (msg.id === botMessageId ? { ...msg, grounded, groundingScore } : msg)),
            ),
          onRetry: (attempt) => {
            setChatError(`연결이 끊겨 재시도 중입니다. (${attempt}/2)`)
            setChatMessages((prev) =>
              prev.map((msg) =>
                msg.id === botMessageId
                  ? { ...msg, content: '응답 연결을 다시 시도하고 있습니다...', isFallback: true }
                  : msg,
              ),
            )
            setTimeout(scrollToBottom, 0)
          },
        },
      )

      // 토큰을 하나도 받지 못하면 빈 말풍선 대신 안내 문구로 대체
      if (!receivedAny) {
        setChatMessages((prev) =>
          prev.map((msg) =>
            msg.id === botMessageId
              ? { ...msg, content: '응답을 받지 못했습니다. 잠시 후 다시 시도해주세요.', isFallback: true }
              : msg,
          ),
        )
      }
      setTimeout(scrollToBottom, 100)
    } catch (err) {
      console.error('Failed to send message', err)
      setChatError('메시지를 전송하지 못했습니다.')
      setChatMessages((prev) =>
        prev.map((msg) =>
          msg.id === botMessageId
            ? { ...msg, content: '응답 연결이 끊겼습니다. 입력창의 메시지로 다시 시도해주세요.', isFallback: true }
            : msg,
        ),
      )
      setChatInput(text)
    } finally {
      setChatSending(false)
    }
  }

  const handleChatSubmit = async (event: FormEvent<HTMLFormElement> | KeyboardEvent<HTMLTextAreaElement>) => {
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
          // 게스트도 새로고침 후 사이드바에서 채팅방을 다시 찾을 수 있도록 저장
          // (수동 생성 경로 handleCreateChat과 동일한 동작으로 통일)
          saveGuestChat(chatRoom)
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

    setChatInput('')
    await sendChatMessage(trimmed, currentChatId)
  }

  const isHeroPrimaryDisabled = isNewChatDisabled
  const displayName = isAuthenticated ? userName ?? '로그인 사용자' : '게스트'
  const displayDept = isAuthenticated
    ? departmentName ?? (userRole === 'UNIVERSITY_COUNCIL' ? '총학생회' : '동국대학교')
    : '동국대학교'
  const roleLabelMap: Record<UserRole, string> = {
    STUDENT: '일반학생',
    DEPARTMENT_COUNCIL: '학생회',
    UNIVERSITY_COUNCIL: '총학생회',
  }
  const roleLabel = roleLabelMap[userRole] // '일반학생'
  const showDeptAdminButton = isAuthenticated && userRole === 'DEPARTMENT_COUNCIL' // '학생회'
  const showUnivAdminButton = isAuthenticated && userRole === 'UNIVERSITY_COUNCIL' // '총학생회'
  const showRagScores = isAuthenticated && userRole === 'UNIVERSITY_COUNCIL'
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

  const starterQuestions = useMemo(() => {
    const dept = isAuthenticated ? (departmentName ?? '').trim() : ''
    const year = isAuthenticated ? (entryYear ?? '').trim() : ''
    const questions: string[] = []

    if (dept && year) {
      questions.push(`${year}학번 ${dept} 졸업기준 알려줘`)
    }
    if (dept) {
      questions.push(`${dept} 전공필수 과목 알려줘`)
      questions.push(`${dept} 사무실 연락처 알려줘`)
    }

    if (isAuthenticated && userRole === 'DEPARTMENT_COUNCIL') {
      questions.push('최근 학과 관련 공지 보여줘')
      questions.push('이번 달 학사일정 알려줘')
    } else if (isAuthenticated && userRole === 'UNIVERSITY_COUNCIL') {
      questions.push('오늘 올라온 공지 요약해줘')
      questions.push('최근 장학 공지 보여줘')
    } else {
      questions.push('최근 장학 공지 보여줘')
      questions.push('이번 달 학사일정 알려줘')
      questions.push('오늘 학식 뭐 나와?')
    }

    return Array.from(new Set(questions)).slice(0, 6)
  }, [departmentName, entryYear, isAuthenticated, userRole])

  const handleStarterQuestionSelect = (question: string) => {
    setChatInput(question)
    window.requestAnimationFrame(() => {
      chatInputRef.current?.focus()
    })
  }

  return (
    <div className="gpt-home">
      {/* Mobile Backdrop */}
      <div 
        className={`mobile-backdrop ${isSidebarOpen ? 'open' : ''}`} 
        onClick={() => setIsSidebarOpen(false)}
        aria-hidden={!isSidebarOpen}
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

        <div className="gpt-home__section gpt-home__chat-section">
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
                            <button className="ghost-btn small ghost-btn--accent" type="button" onClick={handleSignup}>
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
              aria-label="채팅 목록 열기"
              aria-expanded={isSidebarOpen}
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
              <span className="buddy-topbar__text buddy-topbar__text--muted">로그인 또는 회원가입 후 이용할 수 있습니다</span>
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
                <button className="ghost-btn small ghost-btn--accent" type="button" onClick={handleSignup}>
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
                // 정확히 0이 아닌 근접 임계값 — 관성 스크롤로 0을 스치지 못해도 로드되도록
                if (target.scrollTop <= 16 && hasMoreMessages && !isLoadingMore) {
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
                  <div className="home-guide__starters">
                    <div className="home-guide__context">
                      <span>{displayDept ?? '동국대학교'}</span>
                      <strong>{isAuthenticated ? `${roleLabel} 맞춤 질문` : '바로 물어볼 질문'}</strong>
                    </div>
                    <div className="suggested-questions" aria-label="추천 질문">
                      <div className="suggested-questions__heading">추천 질문</div>
                      <div className="suggested-questions__list">
                        {starterQuestions.map((question) => (
                          <button
                            key={question}
                            type="button"
                            className="suggested-questions__chip"
                            disabled={chatSending}
                            onClick={() => handleStarterQuestionSelect(question)}
                          >
                            {question}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
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
                      <strong>로그인 기능</strong>
                      <p>회원가입 후 로그인하면 대화 내역이 저장되고, 소속 학과에 맞는 맞춤형 답변을 받을 수 있습니다.</p>
                    </li>
                  </ol>
                </div>
              ) : chatMessages.length === 0 ? (
                <div className="home-chat__empty">아직 메시지가 없습니다. 첫 메시지를 보내보세요.</div>
              ) : (
                <ul className="chat-bubbles">
                  {isLoadingMore && <li className="home-chat__status"><small>이전 대화 불러오는 중...</small></li>}

                  {chatMessages.map((message, index) => {
                    const messageTime = formatMessageTime(message.createdTime)
                    // 스트리밍 대기 중인 빈 봇 말풍선은 타이핑 인디케이터로 렌더
                    const isStreamingPlaceholder = !message.isAsk && !message.content
                    const previousUserMessage = !message.isAsk
                      ? [...chatMessages.slice(0, index)].reverse().find((candidate) => candidate.isAsk && candidate.content.trim().length > 0)
                      : null
                    return (
                      <li
                        key={message.id}
                        className={`chat-bubble ${message.isAsk ? 'chat-bubble--user' : 'chat-bubble--bot'} ${!message.isAsk && message.isFallback ? 'chat-bubble--fallback' : ''}`}
                      >
                        {isStreamingPlaceholder ? (
                          <div className="typing-indicator">
                            <div className="typing-dot"></div>
                            <div className="typing-dot"></div>
                            <div className="typing-dot"></div>
                          </div>
                        ) : (
                          <>
                            {!message.isAsk && message.isFallback && <span className="chat-fallback-badge">{getFallbackLabel(message.fallbackReason)}</span>}
                            <ChatMarkdown
                              content={message.content}
                              onCitationClick={(citationNumber) => setActiveCitation({ messageId: message.id, citationNumber })}
                            />
                            {!message.isAsk && message.grounded === false && (
                              <span
                                className="chat-fallback-badge"
                                title={typeof message.groundingScore === 'number' ? `근거 일치도 약 ${Math.round(message.groundingScore * 100)}%` : undefined}
                              >
                                ⚠️ 제공된 자료로 충분히 확인되지 않은 내용이 포함될 수 있어요.
                              </span>
                            )}
                            {!message.isAsk && (
                              <SourceCards
                                sources={message.sources}
                                showScores={showRagScores}
                                isFallback={message.isFallback}
                                activeCitationNumber={activeCitation?.messageId === message.id ? activeCitation.citationNumber : null}
                              />
                            )}
                            {!message.isAsk && message.content.trim().length > 0 && <CopyButton text={message.content} />}
                            {!message.isAsk && message.content.trim().length > 0 && previousUserMessage && selectedChatId && (
                              <RegenerateButton
                                disabled={chatSending}
                                onRegenerate={() => sendChatMessage(previousUserMessage.content, selectedChatId)}
                              />
                            )}
                            {!message.isAsk && message.requestId && <MessageFeedback requestId={message.requestId} />}
                            {!message.isAsk && (
                              <SuggestedQuestions
                                questions={message.suggestedQuestions ?? []}
                                disabled={chatSending}
                                onSelect={(question) => {
                                  if (selectedChatId) {
                                    sendChatMessage(question, selectedChatId)
                                  }
                                }}
                              />
                            )}
                            {messageTime && <time className="chat-bubble__time">{messageTime}</time>}
                          </>
                        )}
                      </li>
                    )
                  })}
                  <div ref={messagesEndRef} />
                </ul>
              )}
            </div>

            <form className="home-chat__composer" onSubmit={handleChatSubmit}>
              <div className="home-chat__input-wrapper">
                <textarea
                  ref={chatInputRef}
                  aria-label="채팅 메시지"
                  className="home-chat__input"
                  placeholder={selectedChatId ? '무엇이든 물어보세요' : '무엇이든 물어보세요 (새 대화가 자동으로 시작됩니다)'}
                  value={chatInput}
                  onChange={(event) => setChatInput(event.target.value)}
                  onKeyDown={(event) => {
                    // isComposing: 한글 조합 중 Enter가 글자 확정+전송으로 이중 동작하는 것 방지
                    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                      event.preventDefault()
                      handleChatSubmit(event)
                    }
                  }}
                  rows={3}
                  disabled={chatSending || (departmentsLoading && !selectedChatId)}
                />
                <button
                  className="hero-btn hero-btn--primary home-chat__send-btn"
                  type="submit"
                  disabled={chatSending || (departmentsLoading && !selectedChatId)}
                  aria-label="메시지 보내기"
                >
                  {chatSending ? '전송 중...' : '보내기'}
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
