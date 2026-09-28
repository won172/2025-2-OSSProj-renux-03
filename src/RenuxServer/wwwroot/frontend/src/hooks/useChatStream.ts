import { useCallback, useEffect, useRef } from 'react'
import { resolveApiUrl, withNgrokHeader } from '../api/client'
import { withGuestTokenHeader } from '../chat/guestToken'
import { getCompletionRetrieval, getCompletionVerification, getGroundingFromEvent, parseChatStreamLine } from '../chat/streamEvents'
import type { ChatRetrievalMode, ChatVerificationStatus } from '../chat/chatState'
import type { ChatSource } from '../components/chat/SourceCards'

export interface ChatStreamPayload {
  id: string
  chatId: string
  content: string
  createdTime: string | number
  guestToken?: string
}

export interface ChatStreamMetadata {
  sources?: ChatSource[] | null
  requestId?: string
  isFallback?: boolean
  fallbackReason?: string | null
  verificationStatus?: ChatVerificationStatus
  relevanceScore?: number | null
  retrievalMode?: ChatRetrievalMode
  degradedDatasets?: string[]
}

export interface ChatStreamGrounding {
  grounded: boolean
  groundingScore?: number
}

export interface ChatStreamHandlers {
  /** 프레임마다 합쳐진 전체 답변 문자열 전달; 종료 시 남은 텍스트는 즉시 전달 */
  onText: (accumulated: string) => void
  /** 검색 메타데이터(출처/폴백) 수신 시 호출 */
  onMetadata?: (meta: ChatStreamMetadata) => void
  /** 추천 후속질문 수신 시 호출 */
  onSuggestions?: (questions: string[]) => void
  /** 답변 근거성 경고 수신 시 호출 */
  onGrounding?: (grounding: ChatStreamGrounding) => void
}

export interface ChatStreamResult {
  answer: string
  receivedAny: boolean
  requestId?: string
  grounded?: boolean
}

/**
 * /chat/stream(SSE) 송신·파싱을 canonical 채팅 셸용 훅 한 곳에 모은다.
 * 네트워크 청크가 줄 중간에서 잘려도 토큰이 유실되지 않도록 버퍼로 이월하며,
 * 언마운트 시 진행 중인 reader를 취소해 누수를 막는다.
 */
export const useChatStream = () => {
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const pendingDeliveryRef = useRef<{ flush: () => void; discard: () => void } | null>(null)

  const stopStream = useCallback(() => {
    pendingDeliveryRef.current?.flush()
    controllerRef.current?.abort()
    readerRef.current?.cancel().catch(() => {})
  }, [])

  useEffect(() => {
    return () => {
      pendingDeliveryRef.current?.discard()
      pendingDeliveryRef.current = null
      controllerRef.current?.abort()
      readerRef.current?.cancel().catch(() => {})
      controllerRef.current = null
      readerRef.current = null
    }
  }, [])

  const streamMessage = useCallback(
    async (payload: ChatStreamPayload, handlers: ChatStreamHandlers): Promise<ChatStreamResult> => {
      const url = resolveApiUrl('/chat/stream')
      const controller = new AbortController()
      pendingDeliveryRef.current?.flush()
      controllerRef.current?.abort()
      controllerRef.current = controller

      const openStream = async () => {
        const response = await fetch(url, {
          method: 'POST',
          headers: withNgrokHeader(
            url,
            withGuestTokenHeader({
              'Content-Type': 'application/json',
              Accept: 'text/event-stream',
            }, payload.guestToken),
          ),
          body: JSON.stringify({
            id: payload.id,
            chatId: payload.chatId,
            isAsk: true,
            content: payload.content,
            createdTime: payload.createdTime,
          }),
          credentials: 'include',
          signal: controller.signal,
        })

        if (!response.ok) throw new Error(`Streaming failed (status: ${response.status})`)
        return response
      }

      const readStream = async (response: Response) => {
        const reader = response.body?.getReader()
        if (!reader) throw new Error('No reader available')
        readerRef.current = reader

        const decoder = new TextDecoder()
        let accumulatedAnswer = ''
        let receivedCompletion = false
        let receivedDone = false
        let completedRequestId: string | undefined
        let completedGrounded: boolean | undefined
        let lastDeliveredAnswer = ''
        let frame: number | null = null
        let timer: ReturnType<typeof setTimeout> | null = null
        let discarded = false

        const cancelScheduledDelivery = () => {
          if (frame !== null) globalThis.cancelAnimationFrame(frame)
          if (timer !== null) globalThis.clearTimeout(timer)
          frame = null
          timer = null
        }
        const flushPending = () => {
          cancelScheduledDelivery()
          if (discarded) return
          if (accumulatedAnswer !== lastDeliveredAnswer) {
            lastDeliveredAnswer = accumulatedAnswer
            handlers.onText(accumulatedAnswer)
          }
        }
        const delivery = {
          flush: flushPending,
          discard: () => {
            discarded = true
            cancelScheduledDelivery()
          },
        }
        pendingDeliveryRef.current = delivery

        const scheduleDelivery = () => {
          if (frame !== null || timer !== null) return
          if (typeof globalThis.requestAnimationFrame === 'function') {
            frame = globalThis.requestAnimationFrame(flushPending)
          }
          // Background tabs can pause rAF; the timer also covers runtimes without it.
          timer = globalThis.setTimeout(flushPending, 40)
        }

        const processLine = (rawLine: string) => {
          const data = parseChatStreamLine(rawLine)
          if (!data) return

          if (data.type === 'metadata') {
            completedRequestId = data.request_id ?? completedRequestId
            handlers.onMetadata?.({
              sources: data.sources,
              requestId: data.request_id,
              isFallback: data.fallback_triggered,
              fallbackReason: data.fallback_reason,
            })
          } else if (data.type === 'text') {
            accumulatedAnswer += data.content ?? ''
            if (accumulatedAnswer !== lastDeliveredAnswer) scheduleDelivery()
          } else if (data.type === 'suggestions') {
            handlers.onSuggestions?.(data.questions ?? [])
          } else if (data.type === 'grounding') {
            const grounding = getGroundingFromEvent(data)
            if (grounding.grounded !== undefined) {
              completedGrounded = grounding.grounded
              handlers.onGrounding?.({ ...grounding, grounded: grounding.grounded })
            }
          } else if (data.type === 'completion') {
            flushPending()
            receivedCompletion = true
            completedRequestId = data.request_id ?? completedRequestId
            const verification = getCompletionVerification(data)
            const grounding = getGroundingFromEvent(data)
            handlers.onMetadata?.({
              sources: data.sources,
              requestId: data.request_id,
              isFallback: Boolean(data.fallback_reason),
              fallbackReason: data.fallback_reason,
              ...verification,
              ...getCompletionRetrieval(data),
            })
            handlers.onSuggestions?.(data.suggested_questions ?? [])
            if (verification.verificationStatus === 'failed') {
              completedGrounded = false
              handlers.onGrounding?.({ ...grounding, grounded: false })
            } else if (grounding.grounded !== undefined) {
              completedGrounded = grounding.grounded
              handlers.onGrounding?.({ ...grounding, grounded: grounding.grounded })
            }
          } else if (data.type === 'done') {
            flushPending()
            receivedDone = true
          } else if (data.type === 'error') {
            flushPending()
            throw new Error(data.message ?? 'Streaming error')
          }
        }

        try {
          let buffer = ''
          while (true) {
            const { done, value } = await reader.read()
            if (done) break

            buffer += decoder.decode(value, { stream: true })
            const lines = buffer.split('\n')
            // 마지막 조각은 아직 완성되지 않았을 수 있으므로 다음 청크로 이월한다.
            buffer = lines.pop() ?? ''
            for (const line of lines) {
              processLine(line)
            }
          }
          // 스트림 종료 후 버퍼에 남은 완성 라인을 처리한다.
          if (buffer.length > 0) {
            processLine(buffer)
          }
          if (controller.signal.aborted) {
            throw new DOMException('The chat stream was stopped.', 'AbortError')
          }
          if (!receivedCompletion || !receivedDone) {
            throw new Error('Chat stream ended before its completion contract.')
          }
        } finally {
          flushPending()
          if (pendingDeliveryRef.current === delivery) pendingDeliveryRef.current = null
          if (readerRef.current === reader) readerRef.current = null
        }

        return {
          answer: accumulatedAnswer,
          receivedAny: accumulatedAnswer.trim().length > 0,
          requestId: completedRequestId,
          grounded: completedGrounded,
        }
      }

      try {
        const response = await openStream()
        const result = await readStream(response)
        if (controller.signal.aborted) {
          throw new DOMException('The chat stream was stopped.', 'AbortError')
        }
        return result
      } finally {
        if (controllerRef.current === controller) {
          controllerRef.current = null
        }
      }
    },
    [],
  )

  return { streamMessage, stopStream }
}
