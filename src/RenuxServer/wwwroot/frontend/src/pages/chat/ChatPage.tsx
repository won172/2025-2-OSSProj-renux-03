import { type FormEvent, type KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import { useChatStream } from '../../hooks/useChatStream'
import ChatMarkdown from '../../components/chat/ChatMarkdown'
import CopyButton from '../../components/chat/CopyButton'
import MessageFeedback from '../../components/chat/MessageFeedback'
import RegenerateButton from '../../components/chat/RegenerateButton'
import SuggestedQuestions from '../../components/chat/SuggestedQuestions'
import SourceCards, { type ChatSource } from '../../components/chat/SourceCards'
import type { ActiveChat } from '../../types/chat'

interface ChatPageMessage {
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

const epochTicks = 621355968000000000 // .NET DateTime epoch ticks
const ticksToDate = (ticks: number) => new Date((ticks - epochTicks) / 10000)

const formatMessageTime = (value?: string | number) => {
  if (!value) return ''
  const date = typeof value === 'number' ? ticksToDate(value) : new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('ko-KR', {
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

const getFallbackLabel = (reason?: string | null) => {
  if (reason === 'date_filter_eliminated_all') return '날짜 범위 재확인'
  if (reason === 'score_below_threshold') return '근거 약함'
  if (reason === 'dataset_unavailable') return '일시적 조회 실패'
  return '근거 부족'
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
  const [activeCitation, setActiveCitation] = useState<{ messageId: string; citationNumber: number } | null>(null)
  const { streamMessage } = useChatStream()
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const showRagScores = useMemo(() => {
    if (typeof window === 'undefined') return false
    return window.localStorage.getItem('renux-user-role') === 'UNIVERSITY_COUNCIL'
  }, [])
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
        const messageData = await apiFetch<ChatPageMessage[]>('/chat/load', {
          method: 'POST',
          json: { chatId, lastTime: new Date().toISOString() },
        })
        //const messageData = await apiFetch<ChatPageMessage[]>(`/chat/startload?chatId=${chatId}`, {// 채팅 메시지 불러오기
        //})

        if (Array.isArray(messageData)) {
          setMessages(messageData.reverse())
        }

        // 채팅방 제목 조회: 로그인 사용자는 /chat/active, 게스트는 localStorage에서 찾는다.
        let title: string | null = null
        try {
          const activeChats = await apiFetch<ActiveChat[]>('/chat/active')
          if (Array.isArray(activeChats)) {
            title = activeChats.find((c) => c.id === chatId)?.title ?? null
          }
        } catch {
          // 게스트(401 등)는 게스트 채팅 목록에서 조회
          try {
            const stored = window.localStorage.getItem('renux-guest-chats')
            const guestChats: ActiveChat[] = stored ? JSON.parse(stored) : []
            title = guestChats.find((c) => c.id === chatId)?.title ?? null
          } catch {
            title = null
          }
        }
        setActiveChat({ id: chatId, title, organization: null })
      } catch (fetchError) {
        console.error('Failed to load chat messages', fetchError)
        setError('채팅을 불러오는 중 문제가 발생했습니다.')
      } finally {
        setLoading(false)
      }
    }

    loadInitialData()
  }, [chatId, navigate])

  // 새 메시지/스트리밍 토큰이 쌓일 때마다 자동으로 맨 아래로 스크롤
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  const headerTitle = useMemo(() => {
    if (activeChat?.title) return activeChat.title
    return '채팅방'
  }, [activeChat])

  if (!chatId) {
    return null
  }

  const sendMessage = async (text: string) => {
    if (!chatId) return

    setSendError(null)

    const newMessage: ChatPageMessage = {
      id: typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
      chatId,
      isAsk: true,
      content: text,
      createdTime: new Date().toISOString(),
    }

    // 서버는 ChatMessageDto 구조를 기대하므로(아이디/시간 포함) 즉시 요청 보내기 전에 낙관적으로 목록에 추가합니다.
    setMessages((prev) => [...prev, newMessage])
    
    // 스트리밍을 위한 빈 답변 메시지 미리 추가
    const botMessageId = typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : `bot-${Date.now()}`
    const botPlaceholder: ChatPageMessage = {
      id: botMessageId,
      chatId,
      isAsk: false,
      content: '',
      createdTime: new Date().toISOString(),
      sources: []
    }
    setMessages((prev) => [...prev, botPlaceholder])
    
    setIsSending(true)

    try {
      const { receivedAny } = await streamMessage(
        { id: newMessage.id, chatId, content: text, createdTime: newMessage.createdTime },
        {
          onText: (accumulated) =>
            setMessages((prev) =>
              prev.map((msg) => (msg.id === botMessageId ? { ...msg, content: accumulated } : msg)),
            ),
          onMetadata: (meta) =>
            setMessages((prev) =>
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
            setMessages((prev) =>
              prev.map((msg) => (msg.id === botMessageId ? { ...msg, suggestedQuestions: questions } : msg)),
            ),
          onGrounding: ({ grounded, groundingScore }) =>
            setMessages((prev) =>
              prev.map((msg) => (msg.id === botMessageId ? { ...msg, grounded, groundingScore } : msg)),
            ),
          onRetry: (attempt) => {
            setSendError(`연결이 끊겨 재시도 중입니다. (${attempt}/2)`)
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === botMessageId
                  ? { ...msg, content: '응답 연결을 다시 시도하고 있습니다...', isFallback: true }
                  : msg,
              ),
            )
          },
        },
      )

      // 스트림이 열렸지만 토큰을 하나도 받지 못한 경우 빈 말풍선이 남지 않도록 안내 문구로 대체
      if (!receivedAny) {
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === botMessageId
              ? { ...msg, content: '응답을 받지 못했습니다. 잠시 후 다시 시도해주세요.', isFallback: true }
              : msg,
          ),
        )
      }
    } catch (sendErr) {
      console.error('Failed to send message', sendErr)
      setSendError('메시지를 전송하지 못했습니다. 다시 시도해주세요.')
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === botMessageId
            ? { ...msg, content: '응답 연결이 끊겼습니다. 입력창의 메시지로 다시 시도해주세요.', isFallback: true }
            : msg,
        ),
      )
      setInputValue(text)
    } finally {
      setIsSending(false)
    }
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement> | KeyboardEvent<HTMLTextAreaElement>) => {
    event.preventDefault()

    const trimmed = inputValue.trim()
    if (!trimmed) {
      setSendError('메시지를 입력해주세요.')
      return
    }

    setInputValue('')
    await sendMessage(trimmed)
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
          {/* 환경설정 페이지가 준비되면 버튼을 복원한다 (빈 페이지로의 이동 방지) */}
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
              {messages.map((message, index) => {
                const messageTime = formatMessageTime(message.createdTime)
                const previousUserMessage = !message.isAsk
                  ? [...messages.slice(0, index)].reverse().find((candidate) => candidate.isAsk && candidate.content.trim().length > 0)
                  : null
                return (
                  <li
                    key={message.id}
                    className={`chat-bubble ${message.isAsk ? 'chat-bubble--user' : 'chat-bubble--bot'} ${!message.isAsk && message.isFallback ? 'chat-bubble--fallback' : ''}`}
                  >
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
                    {!message.isAsk && message.content.trim().length > 0 && previousUserMessage && (
                      <RegenerateButton
                        disabled={isSending}
                        onRegenerate={() => sendMessage(previousUserMessage.content)}
                      />
                    )}
                    {!message.isAsk && message.requestId && <MessageFeedback requestId={message.requestId} />}
                    {!message.isAsk && (
                      <SuggestedQuestions
                        questions={message.suggestedQuestions ?? []}
                        disabled={isSending}
                        onSelect={(question) => sendMessage(question)}
                      />
                    )}
                    {messageTime && <time className="chat-bubble__time">{messageTime}</time>}
                  </li>
                )
              })}
            </ul>
          )}
          <div ref={messagesEndRef} />
        </div>

        <form className="chat-page-v2__composer glass-panel" onSubmit={handleSubmit}>
          {sendError && <p className="chat-page-v2__status chat-page-v2__status--error">{sendError}</p>}
          <div className="chat-page-v2__input-wrapper">
            <textarea
              aria-label="채팅 메시지"
              className="chat-page-v2__input"
              placeholder="메시지를 입력하세요 (Enter 전송, Shift+Enter 줄바꿈)"
              value={inputValue}
              onChange={(event) => setInputValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  handleSubmit(event)
                }
              }}
              disabled={isSending}
              rows={1}
            />
            <button type="submit" className="chat-page-v2__send" disabled={isSending} aria-label="메시지 전송">
              {isSending ? '전송 중...' : '전송'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}

export default ChatPage
