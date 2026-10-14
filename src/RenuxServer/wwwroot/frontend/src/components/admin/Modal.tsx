import { useFocusTrap } from '../../hooks/useFocusTrap'
import { type ReactNode, useId, useRef } from 'react'

interface ModalProps {
  open: boolean
  title: string
  description?: ReactNode
  children?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  /** 파괴적 작업이면 확인 버튼을 위험 색으로 표시한다. */
  tone?: 'default' | 'danger'
  busy?: boolean
  confirmDisabled?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * confirm()/prompt()를 대체하는 모달.
 * 브라우저 기본 다이얼로그와 달리 설명·입력·위험도 표시를 담을 수 있고,
 * 모바일에서도 조작 가능하다.
 */
const Modal = ({
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
}: ModalProps) => {
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  useFocusTrap(open, dialogRef, onCancel, busy)

  if (!open) return null

  return (
    <div className="ac-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onCancel() }}>
      <div className="ac-modal" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={dialogRef} tabIndex={-1}>
        <h2 className="ac-modal__title" id={titleId}>{title}</h2>
        {description && <div className="ac-modal__description">{description}</div>}
        {children && <div className="ac-modal__body">{children}</div>}
        <div className="ac-modal__actions">
          <button type="button" className="ac-btn" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`ac-btn ${tone === 'danger' ? 'ac-btn--danger' : 'ac-btn--primary'}`}
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

export default Modal
