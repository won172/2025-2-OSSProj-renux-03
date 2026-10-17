import { useState, type FormEvent } from 'react'
import { addDays, diffDays } from './calendarDates'
import ReminderCard from './ReminderCard'
import { DEFAULT_REMINDER, MAX_PERSONAL_TITLE, validatePersonalInput, type PersonalEventInput, type SavedEvent, type SavedReminder } from './savedEventsApi'

/** 개인 일정 추가·수정 폼. 날짜는 기기의 날짜 선택기를 쓴다. 저장 버튼은 좁은 화면에서 아래에 붙는다. */
const PersonalEventForm = ({ initial, defaultDay, busy, onSubmit, onCancel }: {
  initial?: SavedEvent
  defaultDay: string
  busy: boolean
  onSubmit: (input: PersonalEventInput) => void
  onCancel: () => void
}) => {
  const [title, setTitle] = useState(initial?.title ?? '')
  const [start, setStart] = useState(initial?.startDate ?? initial?.endDate ?? defaultDay)
  const [multiDay, setMultiDay] = useState(Boolean(initial?.startDate))
  const [end, setEnd] = useState(initial?.endDate ?? defaultDay)
  const [reminder, setReminder] = useState<SavedReminder>(initial?.reminder ?? DEFAULT_REMINDER)
  const [error, setError] = useState<string | null>(null)

  const input = (): PersonalEventInput => ({
    title,
    startDate: multiDay ? start : null,
    endDate: multiDay ? end : start,
    reminder,
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const value = input()
    const problem = validatePersonalInput(value)
    setError(problem)
    if (!problem) onSubmit(value)
  }

  const changeStart = (value: string) => {
    setStart(value)
    // 시작일을 종료일 뒤로 옮기면 종료일을 같이 옮겨 기간을 유지한다.
    if (multiDay && value && end && value > end) setEnd(addDays(value, Math.max(0, diffDays(start, end))))
  }

  return (
    <form className="cal-form" onSubmit={submit} noValidate aria-labelledby="cal-form-title">
      <h2 id="cal-form-title" className="cal-detail__title">{initial ? '내 일정 수정' : '내 일정 추가'}</h2>
      <label className="cal-field">
        <span>제목</span>
        <input value={title} maxLength={MAX_PERSONAL_TITLE} required placeholder="예: 동아리 회의, 과제 제출"
          onChange={(event) => setTitle(event.target.value)} autoComplete="off" />
      </label>
      <label className="cal-field">
        <span>{multiDay ? '시작일' : '날짜'}</span>
        <input type="date" value={start} required onChange={(event) => changeStart(event.target.value)} />
      </label>
      <label className="cal-check">
        <input type="checkbox" checked={multiDay} onChange={(event) => {
          setMultiDay(event.target.checked)
          if (event.target.checked && end <= start) setEnd(addDays(start, 1))
        }} />
        <span>여러 날 일정</span>
      </label>
      {multiDay && (
        <label className="cal-field">
          <span>종료일</span>
          <input type="date" value={end} min={start} required onChange={(event) => setEnd(event.target.value)} />
        </label>
      )}
      <ReminderCard reminder={reminder} busy={busy} onChange={setReminder} idPrefix="cal-form-reminder" />
      {error && <p className="cal-form__error" role="alert">{error}</p>}
      <div className="cal-detail__actions">
        <button type="button" className="cal-btn cal-detail__ask" onClick={onCancel} disabled={busy}>취소</button>
        <button type="submit" className="cal-btn cal-btn--primary cal-save" disabled={busy} aria-busy={busy}>
          {busy ? '저장하는 중...' : '저장'}
        </button>
      </div>
    </form>
  )
}

export default PersonalEventForm
