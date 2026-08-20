import { useEffect, useRef, useState } from 'react'
import { apiFetch } from '../../api/client'
import { withGuestTokenHeader } from '../../chat/guestToken'
import ChatMarkdown from './ChatMarkdown'
import SourceCards from './SourceCards'
import SuggestedQuestions from './SuggestedQuestions'
import type { ChatViewMessage } from '../../chat/chatState'
import { canShareAnswer, shareAnswer } from '../../native/nativeFeatures'

const FALLBACK_LABELS: Record<string, string> = {
  date_filter_eliminated_all: '날짜 범위 재확인',
  score_below_threshold: '근거 약함',
  dataset_unavailable: '일시적 조회 실패',
}

const getFallbackLabel = (reason?: string | null) => {
  if (!reason) return '근거 부족'
  return FALLBACK_LABELS[reason] ?? '근거 부족'
}

const FEEDBACK_REASONS = [
  { value: 'inaccurate', label: '부정확함' },
  { value: 'outdated', label: '오래된 정보' },
  { value: 'no_source', label: '출처 없음' },
  { value: 'irrelevant', label: '관련 없음' },
  { value: 'other', label: '기타' },
]

const formatTime = (value?: string | number) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('ko-KR', { hour: 'numeric', minute: '2-digit' }).format(date)
}

interface ChatMessageItemProps {
  message: ChatViewMessage
  isStopped: boolean
  /** 마지막 봇 메시지에만 추천 후속질문을 붙여 스레드가 칩으로 길어지지 않게 한다. */
  isLastAssistant: boolean
  canRegenerate: boolean
  showScores: boolean
  busy: boolean
  guestToken?: string
  activeCitationNumber: number | null
  onCitationClick: (citationNumber: number) => void
  onRegenerate: () => void
  onSelectSuggestion: (question: string) => void
}

const ChatMessageItem = ({
  message,
  isStopped,
  isLastAssistant,
  canRegenerate,
  showScores,
  busy,
  guestToken,
  activeCitationNumber,
  onCitationClick,
  onRegenerate,
  onSelectSuggestion,
}: ChatMessageItemProps) => {
  const [copied, setCopied] = useState(false)
  const [sharing, setSharing] = useState(false)
  const [feedbackMode, setFeedbackMode] = useState<'idle' | 'form' | 'sending' | 'done'>('idle')
  const [feedbackReason, setFeedbackReason] = useState('')
  const [feedbackComment, setFeedbackComment] = useState('')
  const copyTimer = useRef<number | null>(null)

  useEffect(() => () => {
    if (copyTimer.current !== null) window.clearTimeout(copyTimer.current)
  }, [])

  const messageTime = formatTime(message.createdTime)

  if (message.isAsk) {
    return (
      <li className="ch-msg ch-msg--user">
        <div className="ch-msg__bubble">{message.content}</div>
        {messageTime && (
          <div className="ch-actions" style={{ opacity: 1 }}>
            <span className="ch-actions__time">{messageTime}</span>
          </div>
        )}
      </li>
    )
  }

  // 내용이 아직 비어 있는 봇 말풍선은 스트리밍 대기 상태다.
  if (!message.content) {
    return (
      <li className="ch-msg">
        <div className="ch-msg__doc">
          <div className="ch-typing" role="status" aria-live="polite">
            <span className="ch-visually-hidden">동똑이가 답변을 작성 중입니다.</span>
            <span aria-hidden="true" /><span aria-hidden="true" /><span aria-hidden="true" />
          </div>
        </div>
      </li>
    )
  }

  const hasContent = message.content.trim().length > 0

  const submitFeedback = async (rating: 1 | -1) => {
    if (!message.requestId) return
    await apiFetch('/chat/feedback', {
      method: 'POST',
      headers: withGuestTokenHeader({}, guestToken),
      json: {
        requestId: message.requestId,
        rating,
        reason: rating === -1 ? feedbackReason : undefined,
        comment: rating === -1 ? (feedbackComment.trim() || undefined) : undefined,
      },
    })
  }

  const handleUp = async () => {
    setFeedbackMode('done')
    try {
      await submitFeedback(1)
    } catch (error) {
      console.warn('Failed to submit answer feedback', error)
      setFeedbackMode('idle')
    }
  }

  const handleDownSubmit = async () => {
    setFeedbackMode('sending')
    try {
      await submitFeedback(-1)
      setFeedbackMode('done')
    } catch (error) {
      console.warn('Failed to submit answer feedback', error)
      setFeedbackMode('form')
    }
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      if (copyTimer.current !== null) window.clearTimeout(copyTimer.current)
      copyTimer.current = window.setTimeout(() => {
        setCopied(false)
        copyTimer.current = null
      }, 1500)
    } catch (error) {
      console.warn('Failed to copy message text', error)
    }
  }

  const handleShare = async () => {
    setSharing(true)
    try {
      await shareAnswer(message.content)
    } catch (error) {
      // iOS 공유 시트를 사용자가 닫은 경우를 포함해 답변 화면은 그대로 유지한다.
      console.warn('Failed to share message text', error)
    } finally {
      setSharing(false)
    }
  }

  return (
    <li className={`ch-msg ${message.isFallback ? 'ch-msg--fallback' : ''} ${isStopped ? 'ch-msg--stopped' : ''}`}>
      <div className="ch-msg__doc">
        {(isStopped || message.isFallback) && (
          <div className="ch-msg__notes">
            {isStopped && <span className="ch-note ch-note--muted" role="status">생성을 중단한 임시 답변</span>}
            {message.isFallback && (
              <span className="ch-note ch-note--warn">{getFallbackLabel(message.fallbackReason)}</span>
            )}
          </div>
        )}

        <ChatMarkdown content={message.content} onCitationClick={onCitationClick} />

        {!isStopped && message.grounded === false && (
          <div className="ch-msg__notes" style={{ marginTop: '8px', marginBottom: 0 }}>
            <span
              className="ch-note ch-note--warn"
              title={typeof message.groundingScore === 'number'
                ? `근거 일치도 약 ${Math.round(message.groundingScore * 100)}%`
                : undefined}
            >
              ⚠️ 제공된 자료로 충분히 확인되지 않은 내용이 포함될 수 있어요
            </span>
          </div>
        )}

        {!isStopped && (
          <SourceCards
            sources={message.sources}
            showScores={showScores}
            isFallback={message.isFallback}
            activeCitationNumber={activeCitationNumber}
          />
        )}

        {!isStopped && hasContent && (
          <div className="ch-actions">
            <button
              type="button"
              className={`ch-action ${copied ? 'ch-action--done' : ''}`}
              onClick={handleCopy}
              aria-label={copied ? '답변 복사됨' : '답변 복사'}
            >
              {copied ? '✓ 복사됨' : '📋 복사'}
            </button>

            {canShareAnswer() && (
              <button
                type="button"
                className="ch-action"
                onClick={handleShare}
                disabled={sharing}
                aria-label="답변 공유"
              >
                {sharing ? '공유 중' : '↗ 공유'}
              </button>
            )}

            {canRegenerate && (
              <button
                type="button"
                className="ch-action"
                onClick={onRegenerate}
                disabled={busy}
                aria-label="답변 다시 생성"
              >
                ↻ 다시 생성
              </button>
            )}

            {message.requestId && feedbackMode === 'done' && (
              <span className="ch-action ch-action--done">평가 감사합니다</span>
            )}

            {message.requestId && feedbackMode !== 'done' && (
              <>
                <button
                  type="button"
                  className="ch-action"
                  onClick={handleUp}
                  disabled={busy || feedbackMode === 'sending'}
                  aria-label="좋은 답변"
                >
                  👍
                </button>
                <button
                  type="button"
                  className={`ch-action ${feedbackMode === 'form' || feedbackMode === 'sending' ? 'ch-action--on' : ''}`}
                  onClick={() => setFeedbackMode(feedbackMode === 'idle' ? 'form' : 'idle')}
                  disabled={busy || feedbackMode === 'sending'}
                  aria-label="아쉬운 답변"
                  aria-expanded={feedbackMode === 'form' || feedbackMode === 'sending'}
                >
                  👎
                </button>
              </>
            )}

            {messageTime && <span className="ch-actions__time" title="답변 시각">{messageTime}</span>}
          </div>
        )}

        {(feedbackMode === 'form' || feedbackMode === 'sending') && message.requestId && (
          <div className="message-feedback__form" style={{ marginTop: '10px' }}>
            <div className="message-feedback__reasons" role="radiogroup" aria-label="아쉬운 이유">
              {FEEDBACK_REASONS.map((option) => (
                <label className="message-feedback__reason" key={option.value}>
                  <input
                    type="radio"
                    name={`feedback-reason-${message.requestId}`}
                    value={option.value}
                    checked={feedbackReason === option.value}
                    onChange={(event) => setFeedbackReason(event.target.value)}
                    disabled={feedbackMode === 'sending'}
                  />
                  <span>{option.label}</span>
                </label>
              ))}
            </div>
            <textarea
              className="message-feedback__comment"
              value={feedbackComment}
              onChange={(event) => setFeedbackComment(event.target.value)}
              maxLength={2000}
              rows={2}
              disabled={feedbackMode === 'sending'}
              aria-label="추가 의견"
              placeholder="어떤 점이 아쉬웠는지 알려주세요 (선택)"
            />
            <button
              type="button"
              className="ch-btn ch-btn--sm ch-btn--primary"
              onClick={handleDownSubmit}
              disabled={!feedbackReason || feedbackMode === 'sending'}
            >
              {feedbackMode === 'sending' ? '전송 중' : '제출'}
            </button>
          </div>
        )}

        {!isStopped && isLastAssistant && (
          <SuggestedQuestions
            questions={message.suggestedQuestions ?? []}
            requestId={message.requestId}
            disabled={busy}
            guestToken={guestToken}
            onSelect={onSelectSuggestion}
          />
        )}
      </div>
    </li>
  )
}

export default ChatMessageItem
