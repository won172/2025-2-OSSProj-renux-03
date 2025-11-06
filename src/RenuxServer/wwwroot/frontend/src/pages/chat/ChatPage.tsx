import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import type { ActiveChat } from '../../types/chat'

interface ChatPageMessage {
  id: string
  chatId: string
  isAsk: boolean
  content: string
  createdTime: number
}

const epochTicks = 621355968000000000 // .NET DateTime epoch ticks
const getCurrentTicks = () => epochTicks + Date.now() * 10000

const ticksToDate = (ticks: number) => new Date((ticks - epochTicks) / 10000)

const formatMessageTime = (ticks?: number) => {
  if (!ticks) return ''
  const date = ticksToDate(ticks)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('ko-KR', {
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

const ChatPage = () => {
  const navigate = useNavigate()
  const { chatId } = useParams<{ chatId: string }>()
  const [messages, setMessages] = useState<ChatPageMessage[]>([])
  const [activeChat, setActiveChat] = useState<ActiveChat | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [inputValue, setInputValue] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  // 채팅방 ID가 없으면 홈으로 리다이렉트
  useEffect(() => {//
    if (!chatId) {
      navigate('/')
      return
    }
    // 채팅방 메시지 불러오기
    const loadInitialData = async () => {
      try {
        setLoading(true)// 로딩 시작
        const messageData = await apiFetch<ChatPageMessage[]>(`/chat/startload?chatId=${chatId}`, {// 채팅 메시지 불러오기
        })

        if (Array.isArray(messageData)) {
          setMessages(messageData.reverse())
        }

        setActiveChat({ id: chatId, title: null, organization: null })
      } catch (fetchError) {
        console.error('Failed to load chat messages', fetchError)
        setError('채팅을 불러오는 중 문제가 발생했습니다.')
      } finally {
        setLoading(false)
      }
    }

    loadInitialData()
  }, [chatId, navigate])

  const headerTitle = useMemo(() => {
    if (activeChat?.title) return activeChat.title
    return '채팅방'
  }, [activeChat])

  if (!chatId) {
    return null
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!chatId) return

    const trimmed = inputValue.trim()
    if (!trimmed) {
      setSendError('메시지를 입력해주세요.')
      return
    }

    setSendError(null)

    const newMessage: ChatPageMessage = {
      id: typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
      chatId,
      isAsk: true,
      content: trimmed,
      createdTime: getCurrentTicks(),
    }

    // 서버는 ChatMessageDto 구조를 기대하므로(아이디/시간 포함) 즉시 요청 보내기 전에 낙관적으로 목록에 추가합니다.
    setMessages((prev) => [...prev, newMessage])
    setInputValue('')
    setIsSending(true)

    try {
      const { id, chatId, content, createdTime } = newMessage
      await apiFetch('/chat/msg', {
        method: 'POST',
        json: { id, 
          chatId, 
          isAsk: true, 
          content, 
          createdTime }, // ChatMessageDto와 동일한 필드 셋을 전달해야 서버에서 바로 매핑됩니다.
      })


      // 새 답변까지 반영하려면 최신 메시지를 다시 불러옵니다. (추후에는 전용 최신 로딩 API로 최적화 가능)
      const refreshed = await apiFetch<ChatPageMessage[]>(`/chat/startload?chatId=${chatId}`, {
        method: 'POST',
      })


      if (Array.isArray(refreshed)) {
        setMessages(refreshed.reverse())
      }
    } catch (sendErr) {
      console.error('Failed to send message', sendErr)
      setSendError('메시지를 전송하지 못했습니다. 다시 시도해주세요.')
      // 요청이 실패하면 낙관적으로 추가했던 메시지를 제거하고 입력값을 복구합니다.
      setMessages((prev) => prev.filter((msg) => msg.id !== newMessage.id))
      setInputValue(trimmed)
    } finally {
      setIsSending(false)
    }
  }

  return (
    <div className="chat-page-v2 bg-gradient-subtle">
      <aside className="chat-page-v2__sidebar glass-panel">
        <button
          type="button"
          className="chat-page-v2__back buddy-secondary-btn"
          onClick={() => navigate('/')}
        >
          홈으로 돌아가기
        </button>
        <div className="chat-page-v2__summary">
          <p className="chat-page-v2__summary-label">현재 대화</p>
          <h2 className="chat-page-v2__summary-title">{headerTitle}</h2>
          <p className="chat-page-v2__summary-text">
            학과별 상담 히스토리를 확인하고 필요한 내용을 다시 문의하세요.
          </p>
        </div>
      </aside>

      <section className="chat-page-v2__main">
        <header className="chat-page-v2__header glass-panel">
          <div>
            <p className="chat-page-v2__subtitle">Dongguk Buddy AI</p>
            <h1 className="chat-page-v2__title">{headerTitle}</h1>
          </div>
          <button type="button" className="buddy-secondary-btn" onClick={() => navigate('/settings')}>
            환경설정
          </button>
        </header>

        <div className="chat-page-v2__messages glass-panel">
          {loading ? (
            <p className="chat-page-v2__status">채팅을 불러오는 중입니다...</p>
          ) : error ? (
            <p className="chat-page-v2__status chat-page-v2__status--error">{error}</p>
          ) : messages.length === 0 ? (
            <div className="chat-page-v2__empty">
              <h2>첫 대화를 시작해보세요</h2>
              <p>첫 질문을 남기면 AI가 바로 답변을 준비합니다.</p>
            </div>
          ) : (
            <ul className="chat-bubbles">
              {messages.map((message) => {
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
            </ul>
          )}
        </div>

        <form className="chat-page-v2__composer glass-panel" onSubmit={handleSubmit}>
          <input
            className="chat-page-v2__input"
            type="text"
            placeholder="메시지를 입력하세요"
            value={inputValue}
            onChange={(event) => setInputValue(event.target.value)}
            disabled={isSending}
          />
          <button type="submit" className="chat-page-v2__send" disabled={isSending}>
            {isSending ? '전송 중...' : '전송'}
          </button>
        </form>

        {sendError && <p className="chat-page-v2__status chat-page-v2__status--error">{sendError}</p>}
      </section>
    </div>
  )
}

export default ChatPage
