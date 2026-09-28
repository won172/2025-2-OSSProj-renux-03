import type { ChatVerificationStatus } from './chatState'
import type { ChatSource } from '../components/chat/SourceCards'

export interface ChatStreamEvent {
  type?: string
  request_id?: string
  sources?: ChatSource[] | null
  fallback_triggered?: boolean
  fallback_reason?: string | null
  content?: string
  message?: string
  questions?: string[]
  grounded?: boolean | null
  score?: number
  grounding_score?: number | null
  relevance_score?: number | null
  verification_status?: string | null
  suggested_questions?: string[]
}

export const parseChatStreamLine = (rawLine: string): ChatStreamEvent | null => {
  const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
  if (!line.startsWith('data: ')) return null
  try {
    const data: unknown = JSON.parse(line.slice(6))
    return data !== null && typeof data === 'object' && !Array.isArray(data)
      ? data as ChatStreamEvent
      : null
  } catch (error) {
    console.warn('Failed to parse SSE data', error)
    return null
  }
}

export const getGroundingFromEvent = (data: ChatStreamEvent) => {
  const score = data.type === 'grounding' ? data.score : data.grounding_score
  return {
    grounded: typeof data.grounded === 'boolean' ? data.grounded : undefined,
    groundingScore: typeof score === 'number' ? score : undefined,
  }
}

const isVerificationStatus = (value: unknown): value is ChatVerificationStatus =>
  value === 'passed' || value === 'failed' || value === 'unavailable' || value === 'not_required'

export const getCompletionVerification = (data: ChatStreamEvent) => ({
  verificationStatus: isVerificationStatus(data.verification_status) ? data.verification_status : undefined,
  relevanceScore: typeof data.relevance_score === 'number' || data.relevance_score === null
    ? data.relevance_score
    : undefined,
})
