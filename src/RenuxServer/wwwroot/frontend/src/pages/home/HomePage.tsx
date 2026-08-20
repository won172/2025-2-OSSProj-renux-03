import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import { mapRoleNameToUserRole } from '../../auth/roleMapping'
import {
  buildChatTitle,
  toChatListEntry,
  toGuestChatListEntry,
  type ChatListEntry,
} from '../../chat/chatList'
import {
  claimGuestChats,
  deleteChat,
  fetchActiveChats,
  fetchDeadlines,
  fetchDepartments,
  fetchFollowups,
  fetchHomeBriefing,
  fetchNotificationPreferences,
  fetchNotifications,
  loadChatMessages,
  deleteNotification,
  markAllNotificationsRead,
  markNotificationRead,
  renameChat,
  startChat,
  syncNotifications,
} from '../../chat/chatApi'
import {
  finalizeStoppedAssistant,
  isAbortError,
  isStoppedAssistant,
  normalizeAssistantRuns,
  prepareRegeneration,
  readGuestChatRecords,
  removeGuestChat,
  renameGuestChat,
  resolveGuestChatRoute,
  toChatPath,
  updateGuestChatMessages,
  upsertGuestChat,
  writeGuestChatRecords,
  type ChatViewMessage,
  type GuestChatRecord,
} from '../../chat/chatState'
import ChatComposer, { CHAT_INPUT_MAX_LENGTH } from '../../components/chat/ChatComposer'
import ChatHeader from '../../components/chat/ChatHeader'
import ChatMessageItem from '../../components/chat/ChatMessageItem'
import ChatSidebar from '../../components/chat/ChatSidebar'
import ChatToasts, { type ChatToast, type ChatToastTone } from '../../components/chat/ChatToasts'
import ConfirmDialog from '../../components/chat/ConfirmDialog'
import HomeBriefing from '../../components/chat/HomeBriefing'
import { useChatStream } from '../../hooks/useChatStream'
import { useInstallPrompt } from '../../hooks/useInstallPrompt'
import type { HomeBriefing as HomeBriefingData } from '../../types/briefing'
import type { ActiveChat } from '../../types/chat'
import type { AuthNameResponse, UserRole } from '../../types/auth'
import type { Department } from '../../types/organization'
import type { DeadlineItem, UserNotification } from '../../types/notification'

type AuthStatus = 'checking' | 'authenticated' | 'guest'

// 드로어 미디어 쿼리와 chat-shell.css의 경계값을 함께 유지한다.
const MOBILE_LAYOUT_QUERY = '(max-width: 900px)'
const GUIDE_DISMISSED_KEY = 'renux-guide-dismissed'
/** 맨 아래에서 이 거리 안이면 '따라가기' 상태로 본다. */
const FOLLOW_THRESHOLD_PX = 120
/**
 * 알림 자동 동기화 주기.
 * 후보 목록은 서버에서 20분 캐시되고 공지는 하루 4회만 갱신되므로,
 * 더 자주 물어도 새 정보가 나오지 않고 요청만 늘어난다.
 */
const NOTIFICATION_SYNC_INTERVAL_MS = 10 * 60 * 1000

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

const getFocusableElements = (container: HTMLElement | null) => {
  if (!container) return []
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    .filter((element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true')
}

const keepFocusInside = (event: globalThis.KeyboardEvent, container: HTMLElement | null) => {
  if (event.key !== 'Tab') return
  const focusable = getFocusableElements(container)
  if (focusable.length === 0) {
    event.preventDefault()
    container?.focus()
    return
  }

  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

const HomePage = () => {
  const navigate = useNavigate()
  const { chatId: routeChatId } = useParams<{ chatId: string }>()

  const [authStatus, setAuthStatus] = useState<AuthStatus>('checking')
  const [userName, setUserName] = useState<string | null>(null)
  const [departmentName, setDepartmentName] = useState<string | null>(null)
  const [userRole, setUserRole] = useState<UserRole>('STUDENT')

  const [departments, setDepartments] = useState<Department[]>([])
  const [departmentsLoading, setDepartmentsLoading] = useState(true)
  const [activeChats, setActiveChats] = useState<ActiveChat[]>([])
  const [guestRecords, setGuestRecords] = useState<GuestChatRecord[]>([])

  const [selectedChatId, setSelectedChatId] = useState<string | null>(null)
  const [selectedChatTitle, setSelectedChatTitle] = useState<string | null>(null)
  const [chatMessages, setChatMessages] = useState<ChatViewMessage[]>([])
  const [chatLoading, setChatLoading] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const [unknownGuestChatId, setUnknownGuestChatId] = useState<string | null>(null)
  const [hasMoreMessages, setHasMoreMessages] = useState(true)
  const [isLoadingMore, setIsLoadingMore] = useState(false)

  const [chatInput, setChatInput] = useState('')
  const [chatSending, setChatSending] = useState(false)
  const [activeCitation, setActiveCitation] = useState<{ messageId: string; citationNumber: number } | null>(null)

  const [briefing, setBriefing] = useState<HomeBriefingData | null>(null)
  const [briefingLoading, setBriefingLoading] = useState(true)
  const [notifications, setNotifications] = useState<UserNotification[]>([])
  const [deadlines, setDeadlines] = useState<DeadlineItem[]>([])
  const [hasEnabledTopics, setHasEnabledTopics] = useState(true)
  const [lastSyncedAt, setLastSyncedAt] = useState<Date | null>(null)
  const [syncingNotifications, setSyncingNotifications] = useState(false)

  const [toasts, setToasts] = useState<ChatToast[]>([])
  const [deleteTarget, setDeleteTarget] = useState<ChatListEntry | null>(null)
  const [renameTarget, setRenameTarget] = useState<ChatListEntry | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [dialogBusy, setDialogBusy] = useState(false)
  const [claimPrompt, setClaimPrompt] = useState<{ chatIds: string[]; guestToken: string } | null>(null)

  const [showGuide, setShowGuide] = useState(() => {
    if (typeof window === 'undefined') return false
    try {
      return window.localStorage.getItem(GUIDE_DISMISSED_KEY) !== '1'
    } catch {
      return true
    }
  })

  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [isMobileLayout, setIsMobileLayout] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia(MOBILE_LAYOUT_QUERY).matches,
  )
  const [showJumpButton, setShowJumpButton] = useState(false)

  const chatInputRef = useRef<HTMLTextAreaElement | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const sidebarRef = useRef<HTMLElement | null>(null)
  const mobileMenuButtonRef = useRef<HTMLButtonElement | null>(null)
  const skipLoadOnSelectRef = useRef<string | null>(null)
  const isLoadingMoreRef = useRef(false)
  /** 사용자가 위로 올려 읽는 중이면 새 토큰이 와도 끌어내리지 않는다. */
  const shouldFollowRef = useRef(true)
  const toastSeq = useRef(0)
  /** 폴링 콜백이 낡은 authStatus를 붙잡지 않도록 최신 값을 ref로 둔다. */
  const authStatusRef = useRef<AuthStatus>('checking')

  const { streamMessage, stopStream } = useChatStream()
  const { canInstall, install, dismiss: dismissInstall } = useInstallPrompt()
  const isAuthenticated = authStatus === 'authenticated'

  useEffect(() => {
    authStatusRef.current = authStatus
  }, [authStatus])

  const showToast = useCallback((text: string, tone: ChatToastTone = 'success') => {
    toastSeq.current += 1
    const id = `chat-toast-${toastSeq.current}`
    setToasts((previous) => [...previous, { id, text, tone }])
  }, [])

  const dismissToast = useCallback((id: string) => {
    setToasts((previous) => previous.filter((toast) => toast.id !== id))
  }, [])

  // ------------------------------------------------------------ 스크롤 제어

  /**
   * 컨테이너의 scrollTop을 직접 설정한다.
   *
   * scrollIntoView는 앵커가 이미 보이면 움직이지 않고 중첩 스크롤 영역에서 엉뚱한
   * 컨테이너를 움직인다. smooth 동작도 쓰지 않는다 — 사용자 설정이나 환경에 따라
   * 조용히 무시되면 '맨 아래로'가 아무 일도 하지 않는 죽은 버튼이 되기 때문이다.
   */
  const scrollToBottom = useCallback(() => {
    const container = scrollRef.current
    shouldFollowRef.current = true
    setShowJumpButton(false)
    if (!container) return
    container.scrollTop = container.scrollHeight
  }, [])

  /** 스트리밍 중에는 사용자가 아래쪽을 보고 있을 때만 따라간다. */
  const followIfAtBottom = useCallback(() => {
    const container = scrollRef.current
    if (!container) return
    if (shouldFollowRef.current) {
      container.scrollTop = container.scrollHeight
    } else {
      setShowJumpButton(true)
    }
  }, [])

  const handleScroll = () => {
    const container = scrollRef.current
    if (!container) return

    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
    const atBottom = distanceFromBottom <= FOLLOW_THRESHOLD_PX
    shouldFollowRef.current = atBottom
    // 위로 올려 읽는 중이면 돌아갈 길을 남겨 둔다(스트리밍이 아닐 때도).
    setShowJumpButton(!atBottom && container.scrollHeight > container.clientHeight)

    // 정확히 0이 아닌 근접 임계값 — 관성 스크롤로 0을 스치지 못해도 로드되도록.
    if (container.scrollTop <= 16 && hasMoreMessages && !isLoadingMore) {
      void loadMoreMessages()
    }
  }

  // ------------------------------------------------------------ 초기 로드

  useEffect(() => {
    const mediaQuery = window.matchMedia(MOBILE_LAYOUT_QUERY)
    const updateLayout = () => setIsMobileLayout(mediaQuery.matches)
    updateLayout()
    mediaQuery.addEventListener('change', updateLayout)
    return () => mediaQuery.removeEventListener('change', updateLayout)
  }, [])

  useEffect(() => {
    if (!isMobileLayout) setIsSidebarOpen(false)
  }, [isMobileLayout])

  /**
   * 닫을 때 포커스를 열기 버튼으로 돌려줄지 여부.
   * 대화를 골라서 닫는 경우에는 입력창으로 가야 하므로 돌려주지 않는다.
   */
  const restoreFocusOnCloseRef = useRef(true)
  const wasSidebarOpenRef = useRef(false)

  const closeSidebar = useCallback((restoreFocus = true) => {
    restoreFocusOnCloseRef.current = restoreFocus
    setIsSidebarOpen(false)
  }, [])

  const openSidebar = () => setIsSidebarOpen(true)

  /**
   * 드로어 열림/닫힘에 따라 포커스를 옮긴다.
   *
   * 핸들러 안에서 requestAnimationFrame으로 처리하면 React가 aside의 inert를 갱신하기
   * 전에 focus()가 호출될 수 있다. inert 요소의 자손은 포커스를 받지 못하고, 반대로
   * 닫힐 때는 inert가 걸리면서 포커스가 body로 튕겨 나간다. 커밋 이후 실행되는
   * effect에서 처리해야 두 방향 모두 확실하다.
   */
  useEffect(() => {
    if (!isMobileLayout) {
      wasSidebarOpenRef.current = isSidebarOpen
      return
    }

    if (isSidebarOpen) {
      getFocusableElements(sidebarRef.current)[0]?.focus()
    } else if (wasSidebarOpenRef.current) {
      // 포커스가 닫힌(inert) 드로어 안에 남아 body로 튕기지 않도록 항상 옮겨 준다.
      if (restoreFocusOnCloseRef.current) mobileMenuButtonRef.current?.focus()
      else chatInputRef.current?.focus()
    }

    wasSidebarOpenRef.current = isSidebarOpen
  }, [isMobileLayout, isSidebarOpen])

  useEffect(() => {
    if (!isMobileLayout || !isSidebarOpen) return
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        closeSidebar()
        return
      }
      keepFocusInside(event, sidebarRef.current)
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [closeSidebar, isMobileLayout, isSidebarOpen])

  useEffect(() => () => stopStream(), [routeChatId, stopStream])

  useEffect(() => {
    const load = async () => {
      setDepartmentsLoading(true)
      try {
        const data = await fetchDepartments()
        setDepartments(Array.isArray(data) ? data : [])
      } catch (error) {
        console.error('Failed to load departments', error)
        setDepartments([])
      } finally {
        setDepartmentsLoading(false)
      }
    }
    void load()
  }, [])

  useEffect(() => {
    const checkLoginStatus = async () => {
      try {
        const data = await apiFetch<AuthNameResponse>('/auth/name', { method: 'GET' })
        if (data?.name) {
          setAuthStatus('authenticated')
          setUserName(data.name)

          const rawRole = data.roleName || data.role
          if (rawRole) {
            const resolvedRole = mapRoleNameToUserRole(rawRole)
            setUserRole(resolvedRole)
            try {
              window.localStorage.setItem('renux-user-role', resolvedRole)
            } catch {
              // 저장소가 차단되어도 서버에서 확인한 역할은 그대로 사용한다.
            }
          }
          if (data.majorName) setDepartmentName(data.majorName)
          return
        }
        setAuthStatus('guest')
      } catch {
        setAuthStatus('guest')
        setUserName(null)
        setDepartmentName(null)
        setUserRole('STUDENT')
        try {
          window.localStorage.removeItem('renux-user-role')
        } catch {
          // 저장소 접근 실패는 게스트 판정에 영향을 주지 않는다.
        }
      }
    }
    void checkLoginStatus()
  }, [])

  useEffect(() => {
    if (authStatus === 'checking') return

    const load = async () => {
      if (authStatus === 'authenticated') {
        try {
          const data = await fetchActiveChats()
          setActiveChats(Array.isArray(data) ? data : [])
        } catch (error) {
          console.error('Failed to load active chats', error)
          setActiveChats([])
        }
        return
      }
      setGuestRecords(readGuestChatRecords(window.localStorage))
    }
    void load()
  }, [authStatus])

  // 홈 브리핑은 로그인 여부와 무관하게 보여준다(게스트도 오늘 정보를 볼 수 있어야 한다).
  useEffect(() => {
    const load = async () => {
      setBriefingLoading(true)
      try {
        const data = await fetchHomeBriefing()
        setBriefing(data)
      } catch (error) {
        console.warn('Failed to load home briefing', error)
        setBriefing(null)
      } finally {
        setBriefingLoading(false)
      }
    }
    void load()
  }, [])

  /**
   * 마감 알림을 서버와 맞춘다.
   * 설정 페이지에 들어가야만 갱신되던 문제를 없애기 위해 로그인 직후·주기적으로,
   * 그리고 벨을 열 때 호출한다. 실패해도 화면을 막지 않고 기존 목록을 유지한다.
   */
  const refreshNotifications = useCallback(async (options: { sync?: boolean } = {}) => {
    if (authStatusRef.current !== 'authenticated') return
    if (options.sync) setSyncingNotifications(true)

    try {
      if (options.sync) {
        try {
          await syncNotifications()
        } catch {
          // 동기화 실패는 조회까지 막지 않는다.
        }
      }

      const [notificationResult, deadlineResult, preferenceResult] = await Promise.allSettled([
        fetchNotifications(),
        fetchDeadlines(),
        fetchNotificationPreferences(),
      ])

      if (notificationResult.status === 'fulfilled' && Array.isArray(notificationResult.value)) {
        setNotifications(notificationResult.value)
      }
      if (deadlineResult.status === 'fulfilled' && Array.isArray(deadlineResult.value)) {
        setDeadlines(deadlineResult.value)
      }
      if (preferenceResult.status === 'fulfilled') {
        // 관심 주제가 하나도 켜져 있지 않으면 벨의 빈 상태 문구가 달라져야 한다.
        setHasEnabledTopics((preferenceResult.value.preferences ?? []).some((preference) => preference.enabled))
      }
      setLastSyncedAt(new Date())
    } finally {
      if (options.sync) setSyncingNotifications(false)
    }
  }, [])

  useEffect(() => {
    if (authStatus !== 'authenticated') return

    void refreshNotifications({ sync: true })
    const timer = window.setInterval(() => { void refreshNotifications({ sync: true }) }, NOTIFICATION_SYNC_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [authStatus, refreshNotifications])

  // ------------------------------------------------------------ 대화 로드

  const loadMessages = useCallback(async (chatIdToLoad: string) => {
    try {
      setChatLoading(true)
      setChatError(null)
      setHasMoreMessages(true)
      const data = await loadChatMessages(chatIdToLoad, new Date().toISOString())
      if (Array.isArray(data)) {
        setChatMessages(normalizeAssistantRuns([...data].reverse()))
        if (data.length < 20) setHasMoreMessages(false)
        window.setTimeout(() => scrollToBottom(), 60)
      } else {
        setChatMessages([])
        setHasMoreMessages(false)
      }
    } catch (error) {
      console.error('Failed to load messages', error)
      setChatError('채팅 메시지를 불러오지 못했습니다.')
      setChatMessages([])
    } finally {
      setChatLoading(false)
    }
  }, [scrollToBottom])

  const loadMoreMessages = async () => {
    if (!isAuthenticated || !selectedChatId || isLoadingMoreRef.current || !hasMoreMessages || chatMessages.length === 0) {
      return
    }

    const firstMessageTime = chatMessages[0].createdTime
    const container = scrollRef.current
    const previousScrollHeight = container?.scrollHeight ?? 0

    try {
      setIsLoadingMore(true)
      isLoadingMoreRef.current = true

      const data = await loadChatMessages(
        selectedChatId,
        typeof firstMessageTime === 'string' ? firstMessageTime : new Date(firstMessageTime).toISOString(),
      )
      if (Array.isArray(data) && data.length > 0) {
        setChatMessages((previous) => normalizeAssistantRuns([...[...data].reverse(), ...previous]))
        // 읽던 위치를 유지한다 — 위에 내용이 붙은 만큼 스크롤을 내려 보정.
        window.setTimeout(() => {
          if (container) container.scrollTop = container.scrollHeight - previousScrollHeight
        }, 0)
        if (data.length < 20) setHasMoreMessages(false)
      } else {
        setHasMoreMessages(false)
      }
    } catch (error) {
      console.error('Failed to load older messages', error)
    } finally {
      setIsLoadingMore(false)
      window.setTimeout(() => { isLoadingMoreRef.current = false }, 100)
    }
  }

  useEffect(() => {
    if (authStatus === 'checking') return

    if (!routeChatId) {
      setSelectedChatId(null)
      setSelectedChatTitle(null)
      setChatMessages([])
      setChatError(null)
      setUnknownGuestChatId(null)
      setHasMoreMessages(false)
      return
    }

    if (skipLoadOnSelectRef.current === routeChatId) {
      skipLoadOnSelectRef.current = null
      setUnknownGuestChatId(null)
      return
    }

    if (authStatus === 'guest') {
      const records = readGuestChatRecords(window.localStorage)
      const route = resolveGuestChatRoute(routeChatId, records)
      setGuestRecords(records)
      setHasMoreMessages(false)
      setChatLoading(false)
      setChatError(null)

      if (route.kind === 'unknown') {
        setSelectedChatId(null)
        setSelectedChatTitle(null)
        setChatMessages([])
        setUnknownGuestChatId(route.chatId)
        return
      }

      if (route.kind === 'known') {
        setSelectedChatId(route.chat.id)
        setSelectedChatTitle(route.chat.title ?? '대화')
        setChatMessages(normalizeAssistantRuns(route.chat.messages ?? []))
        setUnknownGuestChatId(null)
        window.setTimeout(() => scrollToBottom(), 0)
      }
      return
    }

    setSelectedChatId(routeChatId)
    // 제목은 목록 캐시에서 먼저 채운다 — '채팅방'이 잠깐 보였다 바뀌는 깜빡임을 없앤다.
    setSelectedChatTitle(activeChats.find((chat) => chat.id === routeChatId)?.title ?? null)
    setUnknownGuestChatId(null)
    void loadMessages(routeChatId)
    // activeChats는 제목 초기값 용도라 의존성에서 제외한다(목록 갱신마다 재로드하지 않도록).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authStatus, loadMessages, routeChatId, scrollToBottom])

  useEffect(() => {
    if (!selectedChatId) return
    const selected = activeChats.find((chat) => chat.id === selectedChatId)
      ?? guestRecords.find((record) => record.id === selectedChatId)
    if (selected?.title) setSelectedChatTitle(selected.title)
  }, [activeChats, guestRecords, selectedChatId])

  useEffect(() => {
    setIsSidebarOpen(false)
  }, [selectedChatId])

  // 게스트 대화는 메시지가 바뀔 때마다 이 기기 저장소에 반영한다.
  useEffect(() => {
    if (authStatus !== 'guest' || !selectedChatId || unknownGuestChatId || chatLoading || chatSending) return

    const updated = updateGuestChatMessages(
      readGuestChatRecords(window.localStorage),
      selectedChatId,
      chatMessages,
    )
    writeGuestChatRecords(window.localStorage, updated)
    setGuestRecords(updated)
  }, [authStatus, chatLoading, chatMessages, chatSending, selectedChatId, unknownGuestChatId])

  // ------------------------------------------------------------ 전송

  const storeGuestChat = useCallback((chat: ActiveChat) => {
    const updated = upsertGuestChat(readGuestChatRecords(window.localStorage), chat)
    writeGuestChatRecords(window.localStorage, updated)
    setGuestRecords(updated)
  }, [])

  const selectedGuestToken = authStatus === 'guest'
    ? guestRecords.find((record) => record.id === selectedChatId)?.guestToken
    : undefined
  const reusableGuestToken = selectedGuestToken
    ?? (authStatus === 'guest' ? guestRecords.find((record) => record.guestToken)?.guestToken : undefined)

  const streamIntoAssistant = async (
    question: ChatViewMessage,
    assistantId: string,
    restoreInputOnError: boolean,
    guestToken = selectedGuestToken,
  ) => {
    setChatSending(true)
    setChatError(null)
    scrollToBottom()

    try {
      const { receivedAny, requestId, grounded } = await streamMessage(
        {
          id: question.id,
          chatId: question.chatId,
          content: question.content,
          createdTime: typeof question.createdTime === 'string'
            ? question.createdTime
            : new Date().toISOString(),
          guestToken: authStatus === 'guest' ? guestToken : undefined,
        },
        {
          onText: (accumulated) => {
            setChatMessages((previous) =>
              previous.map((message) => (message.id === assistantId ? { ...message, content: accumulated } : message)),
            )
            followIfAtBottom()
          },
          onMetadata: (meta) =>
            setChatMessages((previous) =>
              previous.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      sources: meta.sources,
                      requestId: meta.requestId,
                      isFallback: meta.isFallback,
                      fallbackReason: meta.fallbackReason,
                    }
                  : message,
              ),
            ),
          onSuggestions: (questions) =>
            setChatMessages((previous) =>
              previous.map((message) =>
                message.id === assistantId ? { ...message, suggestedQuestions: questions } : message,
              ),
            ),
          onGrounding: ({ grounded, groundingScore }) =>
            setChatMessages((previous) =>
              previous.map((message) =>
                message.id === assistantId ? { ...message, grounded, groundingScore } : message,
              ),
            ),
        },
      )

      if (!receivedAny) {
        setChatMessages((previous) =>
          previous.map((message) =>
            message.id === assistantId
              ? { ...message, content: '응답을 받지 못했습니다. 다시 생성해주세요.', isFallback: true }
              : message,
          ),
        )
      }
      followIfAtBottom()

      // 본 답변의 completion/done을 받은 뒤 별도 요청으로 추천을 만든다.
      // await하지 않으므로 입력창 로딩 상태와 답변 완료 시점에는 영향을 주지 않는다.
      if (requestId && grounded === true) {
        void fetchFollowups(
          requestId,
          authStatus === 'guest' ? guestToken : undefined,
        ).then(({ questions }) => {
          setChatMessages((previous) => previous.map((message) => (
            message.id === assistantId && message.requestId === requestId
              ? { ...message, suggestedQuestions: questions }
              : message
          )))
        }).catch((error) => {
          // 추천 실패는 이미 완료된 답변을 오류 상태로 바꾸지 않는다.
          console.warn('Failed to load follow-up suggestions', error)
        })
      }
    } catch (error) {
      if (isAbortError(error)) {
        setChatError(null)
        setChatMessages((previous) => finalizeStoppedAssistant(previous, assistantId))
      } else {
        console.error('Failed to send message', error)
        setChatError('메시지를 전송하지 못했습니다.')
        setChatMessages((previous) =>
          previous.map((message) =>
            message.id === assistantId
              ? { ...message, content: '응답 연결이 끊겼습니다. 다시 생성 버튼으로 시도해주세요.', isFallback: true }
              : message,
          ),
        )
        if (restoreInputOnError) setChatInput(question.content)
      }
    } finally {
      setChatSending(false)
    }
  }

  const newId = (prefix: string) =>
    typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : `${prefix}-${Date.now()}-${Math.random()}`

  const sendChatMessage = async (
    text: string,
    chatId: string | number,
    guestToken = selectedGuestToken,
  ) => {
    const resolvedChatId = String(chatId)
    const question: ChatViewMessage = {
      id: newId('q'),
      chatId: resolvedChatId,
      isAsk: true,
      content: text,
      createdTime: new Date().toISOString(),
    }

    // 스트리밍 토큰을 채울 빈 봇 말풍선을 미리 넣어 타이핑 인디케이터로 보이게 한다.
    const assistantId = newId('a')
    const placeholder: ChatViewMessage = {
      id: assistantId,
      chatId: resolvedChatId,
      isAsk: false,
      content: '',
      createdTime: new Date().toISOString(),
      sources: [],
    }

    setChatMessages((previous) => [...previous, question, placeholder])
    await streamIntoAssistant(question, assistantId, true, guestToken)
  }

  const regenerateChatMessage = async (assistantId: string) => {
    if (chatSending) return
    const prepared = prepareRegeneration(chatMessages, assistantId)
    if (!prepared) {
      setChatError('다시 생성할 질문을 찾지 못했습니다.')
      return
    }
    setChatMessages(prepared.messages)
    await streamIntoAssistant(prepared.question, prepared.assistant.id, false)
  }

  /** 서버의 채팅방 조직 FK를 채우기 위해 내 학과를 우선하고, 없으면 첫 조직을 사용한다. */
  const resolveChatDepartment = useCallback((): Department | null => {
    if (departments.length === 0) return null
    if (departmentName) {
      const mine = departments.find(
        (department) => department.major?.majorname?.trim() === departmentName.trim(),
      )
      if (mine) return mine
    }
    return departments[0]
  }, [departmentName, departments])

  const submitQuestion = async (rawText: string) => {
    const trimmed = rawText.trim()
    if (!trimmed) {
      setChatError('메시지를 입력해주세요.')
      return
    }
    if (trimmed.length > CHAT_INPUT_MAX_LENGTH) {
      setChatError(`메시지는 ${CHAT_INPUT_MAX_LENGTH.toLocaleString()}자까지 입력할 수 있습니다.`)
      return
    }

    let currentChatId = selectedChatId
    let currentGuestToken = selectedGuestToken

    // 방이 없으면 첫 질문으로 바로 만든다 — 별도 모달 없이 대화가 시작되게.
    if (!currentChatId) {
      const org = resolveChatDepartment()
      if (!org) {
        setChatError('채팅을 시작할 수 있는 학과 정보가 없습니다.')
        return
      }

      try {
        setChatSending(true)
        const title = buildChatTitle(trimmed)
        const chatRoom = await startChat(org, title, reusableGuestToken)

        if (authStatus === 'guest') {
          storeGuestChat(chatRoom)
          currentGuestToken = chatRoom.guestToken
        } else {
          setActiveChats((previous) => [chatRoom, ...previous])
        }

        currentChatId = chatRoom.id
        // URL effect가 방금 준비한 메시지 상태를 덮어쓰지 않게 한 번만 건너뛴다.
        skipLoadOnSelectRef.current = chatRoom.id
        setSelectedChatId(chatRoom.id)
        setSelectedChatTitle(chatRoom.title ?? title)
        setUnknownGuestChatId(null)
        navigate(toChatPath(chatRoom.id))

        // 방금 만든 방의 환영 메시지를 가져온다.
        const initial = await loadChatMessages(chatRoom.id, new Date().toISOString())
        setChatMessages(Array.isArray(initial) ? normalizeAssistantRuns([...initial].reverse()) : [])
      } catch (error) {
        console.error('Failed to create chat room', error)
        setChatError('채팅방을 생성하지 못했습니다.')
        setChatSending(false)
        return
      }
    }

    setChatInput('')
    await sendChatMessage(trimmed, currentChatId, currentGuestToken)
  }

  // ------------------------------------------------------------ 대화 관리

  const applyDelete = async () => {
    if (!deleteTarget) return
    setDialogBusy(true)
    try {
      if (isAuthenticated) {
        await deleteChat(deleteTarget.id)
        setActiveChats((previous) => previous.filter((chat) => chat.id !== deleteTarget.id))
      } else {
        const updated = removeGuestChat(readGuestChatRecords(window.localStorage), deleteTarget.id)
        writeGuestChatRecords(window.localStorage, updated)
        setGuestRecords(updated)
      }

      showToast(`“${deleteTarget.title}” 대화를 삭제했습니다.`)
      const wasCurrent = selectedChatId === deleteTarget.id
      setDeleteTarget(null)
      if (wasCurrent) navigate('/')
    } catch (error) {
      console.error('Failed to delete chat', error)
      showToast('대화를 삭제하지 못했습니다. 잠시 후 다시 시도해주세요.', 'error')
    } finally {
      setDialogBusy(false)
    }
  }

  const applyRename = async () => {
    if (!renameTarget) return
    const title = renameValue.trim()
    if (!title) return

    setDialogBusy(true)
    try {
      if (isAuthenticated) {
        await renameChat(renameTarget.id, title)
        setActiveChats((previous) =>
          previous.map((chat) => (chat.id === renameTarget.id ? { ...chat, title } : chat)),
        )
      } else {
        const updated = renameGuestChat(readGuestChatRecords(window.localStorage), renameTarget.id, title)
        writeGuestChatRecords(window.localStorage, updated)
        setGuestRecords(updated)
      }

      if (selectedChatId === renameTarget.id) setSelectedChatTitle(title)
      showToast('대화 이름을 바꿨습니다.')
      setRenameTarget(null)
    } catch (error) {
      console.error('Failed to rename chat', error)
      showToast('이름을 바꾸지 못했습니다. 잠시 후 다시 시도해주세요.', 'error')
    } finally {
      setDialogBusy(false)
    }
  }

  // 로그인 직후, 이 기기에 남은 게스트 대화를 계정으로 옮길지 물어본다.
  useEffect(() => {
    if (authStatus !== 'authenticated') return
    const records = readGuestChatRecords(window.localStorage)
    const claimable = records.filter((record) => record.guestToken)
    if (claimable.length === 0) return

    setClaimPrompt({
      chatIds: claimable.map((record) => record.id),
      guestToken: claimable[0].guestToken!,
    })
  }, [authStatus])

  const applyClaim = async () => {
    if (!claimPrompt) return
    setDialogBusy(true)
    try {
      const result = await claimGuestChats(claimPrompt.chatIds, claimPrompt.guestToken)
      // 이관된 대화는 이제 서버에 있으므로 이 기기 사본은 지운다(중복 노출 방지).
      writeGuestChatRecords(window.localStorage, [])
      setGuestRecords([])
      const refreshed = await fetchActiveChats()
      setActiveChats(Array.isArray(refreshed) ? refreshed : [])
      showToast(`이전 대화 ${result.claimed}개를 계정으로 옮겼습니다.`)
      setClaimPrompt(null)
    } catch (error) {
      console.error('Failed to claim guest chats', error)
      showToast('이전 대화를 옮기지 못했습니다.', 'error')
      setClaimPrompt(null)
    } finally {
      setDialogBusy(false)
    }
  }

  const dismissClaim = () => {
    // 다시 묻지 않도록 이 기기 사본을 비운다 — 매 로그인마다 같은 질문을 반복하지 않게.
    writeGuestChatRecords(window.localStorage, [])
    setGuestRecords([])
    setClaimPrompt(null)
  }

  // ------------------------------------------------------------ 계정

  const handleLogout = async () => {
    try {
      await apiFetch('/auth/signout', { method: 'POST' })
      try {
        window.localStorage.removeItem('renux-user-role')
      } catch {
        // 저장소 접근 실패는 로그아웃 자체를 막지 않는다.
      }
      // 전체 리로드 대신 상태만 초기화한다.
      stopStream()
      setAuthStatus('guest')
      setUserName(null)
      setDepartmentName(null)
      setUserRole('STUDENT')
      setActiveChats([])
      setNotifications([])
      setDeadlines([])
      setLastSyncedAt(null)
      setHasEnabledTopics(true)
      setChatMessages([])
      setSelectedChatId(null)
      setSelectedChatTitle(null)
      navigate('/')
      showToast('로그아웃했습니다.')
    } catch (error) {
      console.error('Failed to logout', error)
      showToast('로그아웃에 실패했습니다. 다시 시도해주세요.', 'error')
    }
  }

  /**
   * 알림 읽음 처리.
   * 같은 마감의 리마인드 여러 건을 한 번에 받는다(화면에서 한 줄로 접혀 있으므로).
   * 낙관적으로 먼저 반영하고, 실패하면 되돌린다 — 서버 왕복을 기다리면 클릭이 굼떠 보인다.
   */
  const handleMarkNotificationRead = async (notificationIds: string[]) => {
    if (notificationIds.length === 0) return
    const targets = new Set(notificationIds)
    const previousState = notifications

    setNotifications((previous) => previous.map((item) => (
      targets.has(item.id) ? { ...item, isRead: true } : item
    )))

    const results = await Promise.allSettled(notificationIds.map(markNotificationRead))
    if (results.some((result) => result.status === 'rejected')) {
      setNotifications(previousState)
      showToast('알림을 읽음으로 표시하지 못했습니다.', 'error')
    }
  }

  const handleMarkAllNotificationsRead = async () => {
    const previousState = notifications
    setNotifications((previous) => previous.map((item) => ({ ...item, isRead: true })))

    try {
      await markAllNotificationsRead()
    } catch (error) {
      console.warn('Failed to mark all notifications as read', error)
      setNotifications(previousState)
      showToast('알림을 모두 읽음으로 표시하지 못했습니다.', 'error')
    }
  }

  const handleDeleteNotifications = async (notificationIds: string[]) => {
    if (notificationIds.length === 0) return
    const targets = new Set(notificationIds)
    const previousState = notifications

    setNotifications((previous) => previous.filter((item) => !targets.has(item.id)))

    const results = await Promise.allSettled(notificationIds.map(deleteNotification))
    if (results.some((result) => result.status === 'rejected')) {
      setNotifications(previousState)
      showToast('알림을 삭제하지 못했습니다.', 'error')
    }
  }

  const dismissGuide = () => {
    setShowGuide(false)
    try {
      window.localStorage.setItem(GUIDE_DISMISSED_KEY, '1')
    } catch {
      // 저장에 실패하면 다음 방문에 다시 보일 뿐이라 무시한다.
    }
  }

  // ------------------------------------------------------------ 파생 값

  const chatEntries = useMemo<ChatListEntry[]>(
    () => (isAuthenticated ? activeChats.map(toChatListEntry) : guestRecords.map(toGuestChatListEntry)),
    [activeChats, guestRecords, isAuthenticated],
  )

  const starterQuestions = useMemo(() => {
    const dept = isAuthenticated ? (departmentName ?? '').trim() : ''
    const questions: string[] = []

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
      questions.push('졸업요건 알려줘')
    }

    return Array.from(new Set(questions)).slice(0, 6)
  }, [departmentName, isAuthenticated, userRole])

  const lastAssistantId = useMemo(() => {
    for (let index = chatMessages.length - 1; index >= 0; index -= 1) {
      if (!chatMessages[index].isAsk) return chatMessages[index].id
    }
    return null
  }, [chatMessages])

  const showRagScores = isAuthenticated && userRole === 'UNIVERSITY_COUNCIL'
  const headerTitle = selectedChatId ? (selectedChatTitle ?? '대화') : '동국대학교 동똑이'
  const greeting = isAuthenticated && userName ? `${userName}님, 오늘의 동국대` : '오늘의 동국대'
  const greetingSub = isAuthenticated
    ? `${departmentName ?? '동국대학교'} 기준으로 오늘 알아두면 좋은 정보를 모았어요.`
    : '로그인하면 학과에 맞는 답변과 마감 알림까지 받을 수 있어요.'

  const composerDisabled = chatSending
    || Boolean(unknownGuestChatId)
    || (departmentsLoading && !selectedChatId)

  const selectedEntry = chatEntries.find((entry) => entry.id === selectedChatId) ?? null

  return (
    <div className={`ch-shell ${isSidebarOpen ? 'ch-shell--nav-open' : ''}`}>
      <ChatSidebar
        sidebarRef={sidebarRef}
        entries={chatEntries}
        selectedChatId={selectedChatId}
        isMobileLayout={isMobileLayout}
        isOpen={isSidebarOpen}
        busy={chatSending}
        isAuthenticated={isAuthenticated}
        onNewChat={() => {
          if (isMobileLayout) closeSidebar(false)
          navigate('/')
          setChatInput('')
          window.requestAnimationFrame(() => chatInputRef.current?.focus())
        }}
        onSelectChat={(chatId) => {
          if (isMobileLayout) closeSidebar(false)
          navigate(toChatPath(chatId))
        }}
        onRenameChat={(entry) => { setRenameTarget(entry); setRenameValue(entry.title) }}
        onDeleteChat={(entry) => setDeleteTarget(entry)}
        onLogin={() => navigate('/auth/in')}
        onSignup={() => navigate('/auth/up')}
      />

      <div className="ch-main">
        <ChatHeader
          title={headerTitle}
          onRenameTitle={selectedEntry ? () => { setRenameTarget(selectedEntry); setRenameValue(selectedEntry.title) } : undefined}
          menuButtonRef={mobileMenuButtonRef}
          isAuthenticated={isAuthenticated}
          userName={userName}
          departmentName={departmentName}
          role={userRole}
          notifications={notifications}
          hasEnabledTopics={hasEnabledTopics}
          lastSyncedAt={lastSyncedAt}
          syncingNotifications={syncingNotifications}
          onMarkNotificationRead={(ids) => { void handleMarkNotificationRead(ids) }}
          onMarkAllNotificationsRead={() => { void handleMarkAllNotificationsRead() }}
          onDeleteNotifications={(ids) => { void handleDeleteNotifications(ids) }}
          onRefreshNotifications={() => { void refreshNotifications() }}
          onAskFromNotification={(question) => { void submitQuestion(question) }}
          onOpenSidebar={openSidebar}
          isSidebarOpen={isSidebarOpen}
          onLogin={() => navigate('/auth/in')}
          onSignup={() => navigate('/auth/up')}
          onLogout={handleLogout}
        />

        <div
          className="ch-scroll"
          ref={scrollRef}
          onScroll={handleScroll}
          role="region"
          aria-label="대화 내용"
          aria-busy={chatLoading || chatSending}
        >
          <div className="ch-center">
            {chatLoading ? (
              <p className="ch-status" role="status">대화를 불러오는 중...</p>
            ) : unknownGuestChatId ? (
              <div className="ch-notice">
                <h2>이 기기에 저장된 대화가 아닙니다</h2>
                <p>게스트 대화는 대화를 시작한 기기의 브라우저에만 저장됩니다. 로그인하면 어느 기기에서나 이어볼 수 있어요.</p>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button type="button" className="ch-btn" onClick={() => navigate('/auth/in')}>로그인</button>
                  <button type="button" className="ch-btn ch-btn--primary" onClick={() => navigate('/')}>
                    새 대화 시작
                  </button>
                </div>
              </div>
            ) : !selectedChatId ? (
              <HomeBriefing
                greeting={greeting}
                subtitle={greetingSub}
                briefing={briefing}
                briefingLoading={briefingLoading}
                deadlines={deadlines}
                isAuthenticated={isAuthenticated}
                starterQuestions={starterQuestions}
                busy={chatSending || departmentsLoading}
                onAsk={(question) => { void submitQuestion(question) }}
                showGuide={showGuide}
                onDismissGuide={dismissGuide}
                canInstall={canInstall}
                onInstall={() => { void install() }}
                onDismissInstall={dismissInstall}
              />
            ) : chatMessages.length === 0 ? (
              <p className="ch-status">아직 메시지가 없습니다. 첫 질문을 보내보세요.</p>
            ) : (
              <ul className="ch-thread" role="log" aria-live="polite" aria-busy={chatSending} aria-label="채팅 메시지">
                {isLoadingMore && (
                  <li className="ch-status" role="status"><small>이전 대화 불러오는 중...</small></li>
                )}

                {chatMessages.map((message, index) => {
                  const previousUserMessage = !message.isAsk
                    ? [...chatMessages.slice(0, index)].reverse().find(
                        (candidate) => candidate.isAsk && candidate.content.trim().length > 0,
                      )
                    : null

                  return (
                    <ChatMessageItem
                      key={message.id}
                      message={message}
                      isStopped={isStoppedAssistant(message)}
                      isLastAssistant={message.id === lastAssistantId}
                      canRegenerate={Boolean(previousUserMessage) && Boolean(selectedChatId)}
                      showScores={showRagScores}
                      busy={chatSending}
                      guestToken={selectedGuestToken}
                      activeCitationNumber={
                        activeCitation?.messageId === message.id ? activeCitation.citationNumber : null
                      }
                      onCitationClick={(citationNumber) =>
                        setActiveCitation({ messageId: message.id, citationNumber })
                      }
                      onRegenerate={() => { void regenerateChatMessage(message.id) }}
                      onSelectSuggestion={(question) => { void submitQuestion(question) }}
                    />
                  )
                })}
                <li className="ch-thread__end" aria-hidden="true" />
              </ul>
            )}
          </div>

          {showJumpButton && selectedChatId && (
            <button
              type="button"
              className={`ch-jump ${chatSending ? 'ch-jump--new' : ''}`}
              onClick={() => scrollToBottom()}
            >
              ↓ {chatSending ? '새 답변 보기' : '맨 아래로'}
            </button>
          )}
        </div>

        <ChatComposer
          inputRef={chatInputRef}
          value={chatInput}
          onChange={setChatInput}
          onSubmit={() => { void submitQuestion(chatInput) }}
          onStop={stopStream}
          sending={chatSending}
          disabled={composerDisabled}
          placeholder={
            // 좁은 화면에서는 긴 안내가 두 줄로 접혀 입력창을 밀어내므로 짧게 쓴다.
            // (자동 시작 안내는 홈의 사용 가이드가 이미 담당한다.)
            selectedChatId || isMobileLayout
              ? '무엇이든 물어보세요'
              : '무엇이든 물어보세요 — 입력하면 바로 대화가 시작됩니다'
          }
          error={chatError}
        />
      </div>

      {isSidebarOpen && (
        <button type="button" className="ch-scrim" aria-label="대화 목록 닫기" onClick={() => closeSidebar()} />
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="대화 삭제"
        description={
          <>
            <b>{deleteTarget?.title}</b> 대화와 주고받은 메시지를 모두 삭제합니다. 삭제한 대화는 복구할 수 없습니다.
          </>
        }
        confirmLabel="삭제"
        tone="danger"
        busy={dialogBusy}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => { void applyDelete() }}
      />

      <ConfirmDialog
        open={renameTarget !== null}
        title="대화 이름 변경"
        description="목록에서 알아보기 쉬운 이름으로 바꿀 수 있습니다."
        confirmLabel="저장"
        busy={dialogBusy}
        confirmDisabled={renameValue.trim().length === 0}
        onCancel={() => setRenameTarget(null)}
        onConfirm={applyRename}
      >
        <input
          type="text"
          value={renameValue}
          onChange={(event) => setRenameValue(event.target.value)}
          maxLength={80}
          aria-label="대화 이름"
          placeholder="예: 졸업요건 상담"
        />
      </ConfirmDialog>

      <ConfirmDialog
        open={claimPrompt !== null}
        title="이전 대화를 계정으로 옮길까요?"
        description={
          <>
            로그인 전 이 기기에서 나눈 대화 <b>{claimPrompt?.chatIds.length ?? 0}개</b>가 남아 있습니다.
            계정으로 옮기면 다른 기기에서도 이어볼 수 있습니다. 옮기지 않으면 이 기기 사본은 정리됩니다.
          </>
        }
        confirmLabel="옮기기"
        cancelLabel="옮기지 않기"
        busy={dialogBusy}
        onCancel={dismissClaim}
        onConfirm={applyClaim}
      />

      <ChatToasts toasts={toasts} onDismiss={dismissToast} />
    </div>
  )
}

export default HomePage
