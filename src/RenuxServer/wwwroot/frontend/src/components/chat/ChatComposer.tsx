import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
  useEffect,
} from 'react'

export const CHAT_INPUT_MAX_LENGTH = 2000

interface ChatComposerProps {
  inputRef: RefObject<HTMLTextAreaElement | null>
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  onStop: () => void
  sending: boolean
  disabled: boolean
  placeholder: string
  error?: string | null
}

/**
 * 채팅 입력창. 기존 계약(2000자 제한·카운터·Enter 전송/Shift+Enter 줄바꿈·IME 조합 처리·
 * 자동 높이 조절·전송 중 중단 버튼)을 유지한다.
 */
const ChatComposer = ({
  inputRef,
  value,
  onChange,
  onSubmit,
  onStop,
  sending,
  disabled,
  placeholder,
  error,
}: ChatComposerProps) => {
  const resize = () => {
    window.requestAnimationFrame(() => {
      const textarea = inputRef.current
      if (!textarea) return
      textarea.style.height = 'auto'
      textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`
    })
  }

  // 값이 바깥에서 바뀌는 경우(추천 질문 선택 등)에도 높이를 맞춘다.
  useEffect(resize, [value, inputRef])

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    onChange(event.target.value.slice(0, CHAT_INPUT_MAX_LENGTH))
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    onSubmit()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // isComposing: 한글 조합 중 Enter가 글자 확정과 전송으로 이중 동작하는 것을 막는다.
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      onSubmit()
    }
  }

  return (
    <div className="ch-composer">
      <form className="ch-composer__inner" onSubmit={handleSubmit} aria-busy={sending}>
        <div className="ch-composer__box">
          <textarea
            ref={inputRef}
            className="ch-composer__input"
            aria-label="채팅 메시지"
            aria-describedby="chat-input-contract"
            aria-invalid={error ? true : undefined}
            placeholder={placeholder}
            value={value}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            rows={1}
            maxLength={CHAT_INPUT_MAX_LENGTH}
            disabled={disabled}
          />

          {sending ? (
            <button
              type="button"
              className="ch-composer__send ch-composer__send--stop"
              onClick={onStop}
              aria-label="답변 생성 중단"
            >
              중단
            </button>
          ) : (
            <button
              type="submit"
              className="ch-composer__send"
              disabled={disabled || value.trim().length === 0}
              aria-label="메시지 보내기"
            >
              보내기
            </button>
          )}
        </div>

        <div className="ch-composer__meta" id="chat-input-contract">
          <span>Enter로 전송 · Shift+Enter로 줄바꿈</span>
          <span aria-label={`${CHAT_INPUT_MAX_LENGTH}자 중 ${value.length}자 입력`}>
            {value.length}/{CHAT_INPUT_MAX_LENGTH}
          </span>
        </div>

        {error && <span className="ch-composer__error" role="alert">{error}</span>}
      </form>
    </div>
  )
}

export default ChatComposer
