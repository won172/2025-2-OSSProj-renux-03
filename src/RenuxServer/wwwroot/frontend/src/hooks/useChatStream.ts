import { useCallback, useEffect, useRef } from 'react'
import { resolveApiUrl, withNgrokHeader } from '../api/client'
import type { ChatSource } from '../components/chat/SourceCards'

export interface ChatStreamPayload {
  id: string
  chatId: string
  content: string
  createdTime: string | number
}

export interface ChatStreamMetadata {
  sources?: ChatSource[] | null
  isFallback?: boolean
  fallbackReason?: string | null
}

export interface ChatStreamHandlers {
  /** 토큰이 누적될 때마다 호출(누적된 전체 답변 문자열 전달) */
  onText: (accumulated: string) => void
  /** 검색 메타데이터(출처/폴백) 수신 시 호출 */
  onMetadata?: (meta: ChatStreamMetadata) => void
  /** 첫 토큰 수신 전 연결 실패가 발생해 같은 payload로 재연결할 때 호출 */
  onRetry?: (attempt: number, delayMs: number) => void
}

export interface ChatStreamResult {
  answer: string
  receivedAny: boolean
}

/**
 * /chat/stream(SSE) 송신·파싱을 한 곳에 모은 훅.
 * HomePage·ChatPage가 동일한 스트리밍 구현을 공유하도록 한다.
 * 네트워크 청크가 줄 중간에서 잘려도 토큰이 유실되지 않도록 버퍼로 이월하며,
 * 언마운트 시 진행 중인 reader를 취소해 누수를 막는다.
 */
export const useChatStream = () => {
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null)

  useEffect(() => {
    return () => {
      readerRef.current?.cancel().catch(() => {})
      readerRef.current = null
    }
  }, [])

  const streamMessage = useCallback(
    async (payload: ChatStreamPayload, handlers: ChatStreamHandlers): Promise<ChatStreamResult> => {
      const url = resolveApiUrl('/chat/stream')
      const maxRetries = 2
      let attempt = 0

      const delay = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))

      const openStream = async () => {
        const response = await fetch(url, {
          method: 'POST',
          headers: withNgrokHeader(url, {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
          }),
          body: JSON.stringify({
            id: payload.id,
            chatId: payload.chatId,
            isAsk: true,
            content: payload.content,
            createdTime: payload.createdTime,
          }),
          credentials: 'include',
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

        const processLine = (rawLine: string) => {
          const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
          if (!line.startsWith('data: ')) return
          let data: {
            type?: string
            sources?: ChatSource[] | null
            fallback_triggered?: boolean
            fallback_reason?: string | null
            content?: string
            message?: string
          }
          try {
            data = JSON.parse(line.substring(6))
          } catch (e) {
            console.warn('Failed to parse SSE data', e)
            return
          }

          if (data.type === 'metadata') {
            handlers.onMetadata?.({
              sources: data.sources,
              isFallback: data.fallback_triggered,
              fallbackReason: data.fallback_reason,
            })
          } else if (data.type === 'text') {
            accumulatedAnswer += data.content ?? ''
            handlers.onText(accumulatedAnswer)
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
        } finally {
          readerRef.current = null
        }

        return { answer: accumulatedAnswer, receivedAny: accumulatedAnswer.trim().length > 0 }
      }

      while (true) {
        try {
          const response = await openStream()
          const result = await readStream(response)
          if (result.receivedAny || attempt >= maxRetries) return result
          throw new Error('Streaming ended before any answer token was received.')
        } catch (error) {
          readerRef.current = null
          if (attempt >= maxRetries) throw error
          attempt += 1
          const delayMs = Math.min(800 * 2 ** (attempt - 1), 3000)
          handlers.onRetry?.(attempt, delayMs)
          await delay(delayMs)
        }
      }
    },
    [],
  )

  return { streamMessage }
}
