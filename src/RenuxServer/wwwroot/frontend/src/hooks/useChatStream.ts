import { useCallback, useEffect, useRef } from 'react'
import { resolveApiUrl, withNgrokHeader } from '../api/client'
import { withGuestTokenHeader } from '../chat/guestToken'
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
}

export interface ChatStreamGrounding {
  grounded: boolean
  groundingScore?: number
}

export interface ChatStreamHandlers {
  /** 토큰이 누적될 때마다 호출(누적된 전체 답변 문자열 전달) */
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

  const stopStream = useCallback(() => {
    controllerRef.current?.abort()
    readerRef.current?.cancel().catch(() => {})
  }, [])

  useEffect(() => {
    return () => {
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

        const processLine = (rawLine: string) => {
          const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
          if (!line.startsWith('data: ')) return
          let data: {
            type?: string
            request_id?: string
            sources?: ChatSource[] | null
            fallback_triggered?: boolean
            fallback_reason?: string | null
            content?: string
            message?: string
            questions?: string[]
            grounded?: boolean
            score?: number
            grounding_score?: number | null
            suggested_questions?: string[]
          }
          try {
            data = JSON.parse(line.substring(6))
          } catch (e) {
            console.warn('Failed to parse SSE data', e)
            return
          }

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
            handlers.onText(accumulatedAnswer)
          } else if (data.type === 'suggestions') {
            handlers.onSuggestions?.(data.questions ?? [])
          } else if (data.type === 'grounding') {
            handlers.onGrounding?.({
              grounded: data.grounded ?? true,
              groundingScore: typeof data.score === 'number' ? data.score : undefined,
            })
          } else if (data.type === 'completion') {
            receivedCompletion = true
            completedRequestId = data.request_id ?? completedRequestId
            completedGrounded = typeof data.grounded === 'boolean'
              ? data.grounded
              : completedGrounded
            handlers.onMetadata?.({
              sources: data.sources,
              requestId: data.request_id,
              isFallback: Boolean(data.fallback_reason),
              fallbackReason: data.fallback_reason,
            })
            handlers.onSuggestions?.(data.suggested_questions ?? [])
            if (typeof data.grounded === 'boolean') {
              handlers.onGrounding?.({
                grounded: data.grounded,
                groundingScore: typeof data.grounding_score === 'number'
                  ? data.grounding_score
                  : undefined,
              })
            }
          } else if (data.type === 'done') {
            receivedDone = true
          } else if (data.type === 'error') {
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
          if (!receivedCompletion || !receivedDone) {
            throw new Error('Chat stream ended before its completion contract.')
          }
        } finally {
          readerRef.current = null
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
        readerRef.current = null
        if (controllerRef.current === controller) {
          controllerRef.current = null
        }
      }
    },
    [],
  )

  return { streamMessage, stopStream }
}
