import { REMINDER_LABEL, REMINDER_STEPS, normalizeSteps, type ReminderStep, type SavedReminder } from './savedEventsApi'

/** 앱 안 알림 설정(스위치 + 7일 전·1일 전·당일). 시점을 모두 끄면 알림도 꺼진다. */
const ReminderCard = ({ reminder, busy, onChange, idPrefix = 'cal-reminder' }: {
  reminder: SavedReminder
  busy: boolean
  onChange: (reminder: SavedReminder) => void
  idPrefix?: string
}) => {
  const { enabled, daysBefore } = reminder
  const titleId = `${idPrefix}-title`
  const toggleStep = (step: ReminderStep) => {
    const next = daysBefore.includes(step) ? daysBefore.filter((value) => value !== step) : normalizeSteps([...daysBefore, step])
    onChange({ enabled: next.length > 0, daysBefore: next })
  }
  return (
    <section className="cal-reminder" aria-labelledby={titleId}>
      <div className="cal-reminder__head">
        <div>
          <h3 id={titleId}>알림 받기</h3>
          <p className="cal-muted">앱 안 알림으로 보내요</p>
        </div>
        <button
          type="button"
          role="switch"
          className="cal-switch"
          aria-checked={enabled}
          aria-labelledby={titleId}
          disabled={busy}
          onClick={() => onChange(enabled ? { enabled: false, daysBefore } : { enabled: true, daysBefore: daysBefore.length ? daysBefore : [7, 1] })}
        ><span aria-hidden="true" /></button>
      </div>
      {enabled && (
        <div className="cal-reminder__steps" role="group" aria-label="알림 시점">
          {REMINDER_STEPS.map((step) => (
            <button key={step} type="button" className="cal-pill" aria-pressed={daysBefore.includes(step)} disabled={busy} onClick={() => toggleStep(step)}>
              {REMINDER_LABEL[step]}
            </button>
          ))}
        </div>
      )}
    </section>
  )
}

export default ReminderCard
