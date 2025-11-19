import { type FormEvent, type KeyboardEvent, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import type { Department } from '../../types/organization'
import type { ActiveChat } from '../../types/chat'
import type { AuthNameResponse } from '../../types/auth'

type InlineMessage = { id: string; isAsk: boolean; content: string; createdAt: number }

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

  const [inlineMessages, setInlineMessages] = useState<InlineMessage[]>([])
  const [inlineInput, setInlineInput] = useState('')
  const [inlineSending, setInlineSending] = useState(false)
  const [inlineSendError, setInlineSendError] = useState<string | null>(null)

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
        }
      } catch (error) {
        console.error('Failed to load active chats', error)
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

    const selectedDept = departments.find((dept) => dept.id === selectedDepartmentId)
    const orgPayload = {
      id: selectedDepartmentId,
      major: {
        id: selectedDept?.major?.id,
        majorname: selectedDept?.major?.majorname ?? '',
      },
    }

    try {
      setIsCreatingChat(true)
      const chatRoom = await apiFetch<ActiveChat>('/chat/start', {
        method: 'POST',
        json: { org: orgPayload, title: trimmedTitle },
      })
      toggleModal(false)
      navigate(`/chat/${chatRoom.id}`)
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

  const isHeroPrimaryDisabled = isAuthenticated && isNewChatDisabled
  const displayName = isAuthenticated ? userName ?? '로그인 사용자' : '게스트'
  const displayDept = isAuthenticated ? departments[0]?.major?.majorname : null
  const roleLabel = isAuthenticated ? '관리자' : '사용자'
  const visibleChats = activeChats.length > 0 ? activeChats : []

  const inlineFormatTime = (timestamp: number) =>
    new Intl.DateTimeFormat('ko-KR', { hour: 'numeric', minute: '2-digit' }).format(new Date(timestamp))

  const sendInlineMessage = () => {
    const trimmed = inlineInput.trim()
    if (!trimmed) {
      setInlineSendError('메시지를 입력해주세요.')
      return
    }
    setInlineSendError(null)
    const newMsg: InlineMessage = {
      id: typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
      isAsk: true,
      content: trimmed,
      createdAt: Date.now(),
    }
    setInlineMessages((prev) => [...prev, newMsg])
    setInlineInput('')
    setInlineSending(true)
    setInlineSending(false)
  }

  const handleInlineSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    sendInlineMessage()
  }

  const handleInlineKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const isComposing = (event.nativeEvent as any).isComposing
    if (event.key === 'Enter' && !event.shiftKey && !isComposing) {
      event.preventDefault()
      sendInlineMessage()
    }
  }

  return (
    <div className="gpt-home">
      <aside className="gpt-home__sidebar">
        <div className="gpt-home__brand">
          <div className="gpt-home__brand-text">
            <p className="chatbot-hero__badge">Dongguk GPT</p>
            <strong>동똑이</strong>
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
              <li key={chat.id} className="gpt-home__chat-item" onClick={() => navigate(`/chat/${chat.id}`)}>
                <span className="gpt-home__chat-title">{chat.title ?? '제목 없음'}</span>
                <span className="gpt-home__chat-sub">대화 이어가기</span>
              </li>
            ))}
            {visibleChats.length === 0 && (
              <li className="gpt-home__chat-empty">아직 대화가 없습니다. 새 대화를 시작해보세요.</li>
            )}
          </ul>
        </div>

        {/* <div className="gpt-home__section">
          <div className="gpt-home__section-head">
            <h3>내 계정</h3>
            <span className="gpt-home__pill">{isAuthenticated ? '로그인됨' : '게스트'}</span>
          </div>
          <p className="gpt-home__muted">{welcomeMessage}</p>
          <div className="gpt-home__actions">
            {isAuthenticated ? (
              <>
                <button className="ghost-btn" type="button" onClick={handleLogout}>
                  로그아웃
                </button>
                <button className="ghost-btn" type="button" onClick={handleOpenSettings}>
                  설정
                </button>
              </>
            ) : (
              <>
                <button className="ghost-btn" type="button" onClick={handleLogin}>
                  로그인
                </button>
                <button className="ghost-btn" type="button" onClick={handleSignup}>
                  회원가입
                </button>
              </>
            )}
          </div>
        </div> */}
      </aside>

      <div className="gpt-home__content">
        <div className="buddy-topbar">
          <div className="buddy-topbar__brand">
            <div className="buddy-topbar__icon">🎓</div>
            <div>
              {/* <p className="buddy-topbar__eyebrow">DONGGUK BUDDY AI</p> */}
              <p className="buddy-topbar__title">동국대학교 AI 챗봇</p>
            </div>
          </div>
          <div className="buddy-topbar__meta">
            <span className="buddy-topbar__text">{displayName}</span>
            {displayDept && (
              <>
                <span className="buddy-topbar__dot">·</span>
                <span className="buddy-topbar__text buddy-topbar__text--muted">{displayDept}</span>
              </>
            )}
            <span className="buddy-topbar__badge">{roleLabel}</span>
          </div>
          <div className="buddy-topbar__meta">
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
              </div>
            </div>

            <div className="home-chat__thread-wrapper">
              <ul className="chat-bubbles">
                {inlineMessages.map((message) => (
                  <li
                    key={message.id}
                    className={`chat-bubble ${message.isAsk ? 'chat-bubble--user' : 'chat-bubble--bot'}`}
                  >
                    <span className="chat-bubble__text">{message.content}</span>
                    <time className="chat-bubble__time">{inlineFormatTime(message.createdAt)}</time>
                  </li>
                ))}
              </ul>
            </div>

            <form className="home-chat__composer" onSubmit={handleInlineSubmit}>
              <textarea
                className="home-chat__input"
                placeholder="무엇이든 입력하세요"
                value={inlineInput}
                onChange={(event) => setInlineInput(event.target.value)}
                onKeyDown={handleInlineKeyDown}
                rows={3}
                disabled={inlineSending}
              />
              <div className="home-chat__actions">
                {inlineSendError && <span className="home-chat__error">{inlineSendError}</span>}
                <div className="home-chat__buttons">
                  <button className="hero-btn hero-btn--primary" type="submit" disabled={inlineSending}>
                    {inlineSending ? '전송 중...' : '보내기'}
                  </button>
                </div>
              </div>
            </form>
          </section>
        </main>
      </div>
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
