import { useState } from 'react'
import { apiFetch } from '../../api/client'

type MessageFeedbackProps = {
  requestId: string
}

type ReasonOption = {
  value: string
  label: string
}

const reasonOptions: ReasonOption[] = [
  { value: 'inaccurate', label: '부정확함' },
  { value: 'outdated', label: '오래된 정보' },
  { value: 'no_source', label: '출처 없음' },
  { value: 'irrelevant', label: '관련 없음' },
  { value: 'other', label: '기타' },
]

const MessageFeedback = ({ requestId }: MessageFeedbackProps) => {
  const [mode, setMode] = useState<'idle' | 'down' | 'submitting' | 'confirmed'>('idle')
  const [selectedReason, setSelectedReason] = useState<string>('')
  const [comment, setComment] = useState('')

  const submitFeedback = async (rating: 1 | -1, reason?: string, feedbackComment?: string) => {
    await apiFetch('/chat/feedback', {
      method: 'POST',
      json: {
        requestId,
        rating,
        reason,
        comment: feedbackComment,
      },
    })
  }

  const handleUp = async () => {
    setMode('confirmed')
    try {
      await submitFeedback(1)
    } catch (error) {
      console.warn('Failed to submit answer feedback', error)
      setMode('idle')
    }
  }

  const handleDownSubmit = async () => {
    setMode('submitting')
    try {
      await submitFeedback(-1, selectedReason, comment.trim() || undefined)
      setMode('confirmed')
    } catch (error) {
      console.warn('Failed to submit answer feedback', error)
      setMode('down')
    }
  }

  if (mode === 'confirmed') {
    return <div className="message-feedback message-feedback--confirmed">감사합니다</div>
  }

  return (
    <div className="message-feedback" aria-label="답변 피드백">
      <div className="message-feedback__actions">
        <button
          type="button"
          className="message-feedback__button"
          onClick={handleUp}
          disabled={mode === 'submitting'}
          aria-label="좋은 답변"
        >
          👍
        </button>
        <button
          type="button"
          className="message-feedback__button"
          onClick={() => setMode('down')}
          disabled={mode === 'submitting'}
          aria-label="아쉬운 답변"
        >
          👎
        </button>
      </div>

      {mode === 'down' || mode === 'submitting' ? (
        <div className="message-feedback__form">
          <div className="message-feedback__reasons" role="radiogroup" aria-label="아쉬운 이유">
            {reasonOptions.map((option) => (
              <label className="message-feedback__reason" key={option.value}>
                <input
                  type="radio"
                  name={`feedback-reason-${requestId}`}
                  value={option.value}
                  checked={selectedReason === option.value}
                  onChange={(event) => setSelectedReason(event.target.value)}
                  disabled={mode === 'submitting'}
                />
                <span>{option.label}</span>
              </label>
            ))}
          </div>
          <textarea
            className="message-feedback__comment"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            maxLength={2000}
            rows={2}
            disabled={mode === 'submitting'}
            aria-label="추가 의견"
            placeholder="추가 의견"
          />
          <button
            type="button"
            className="message-feedback__submit"
            onClick={handleDownSubmit}
            disabled={!selectedReason || mode === 'submitting'}
          >
            {mode === 'submitting' ? '전송 중' : '제출'}
          </button>
        </div>
      ) : null}
    </div>
  )
}

export default MessageFeedback
