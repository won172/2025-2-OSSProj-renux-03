import { useFocusTrap } from '../../hooks/useFocusTrap'
import { type ReactNode, useId, useRef } from 'react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  description?: ReactNode
  children?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'default' | 'danger'
  busy?: boolean
  confirmDisabled?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * 대화 삭제·이름 변경처럼 되돌리기 어려운 동작을 확인받는 다이얼로그.
 * 브라우저 confirm()과 달리 무엇이 사라지는지 문장으로 설명할 수 있고 모바일에서도 조작된다.
 */
const ConfirmDialog = ({
  open,
  title,
  description,
  children,
  confirmLabel = '확인',
  cancelLabel = '취소',
  tone = 'default',
  busy = false,
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) => {
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  useFocusTrap(open, dialogRef, onCancel, busy)

  if (!open) return null

  return (
    <div
      className="ch-dialog-backdrop"
      onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onCancel() }}
    >
      <div className="ch-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={dialogRef} tabIndex={-1}>
        <h2 className="ch-dialog__title" id={titleId}>{title}</h2>
        {description && <p className="ch-dialog__desc">{description}</p>}
        {children}
        <div className="ch-dialog__actions">
          <button type="button" className="ch-btn" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`ch-btn ${tone === 'danger' ? 'ch-btn--danger' : 'ch-btn--primary'}`}
            onClick={() => { if (!busy && !confirmDisabled) onConfirm() }}
            disabled={busy || confirmDisabled}
            data-autofocus={children ? undefined : true}
          >
            {busy ? '처리 중...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

export default ConfirmDialog
