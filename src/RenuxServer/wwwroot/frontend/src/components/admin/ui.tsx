import { useId, useRef, type ReactNode } from 'react'
import type { StatusTone } from '../../admin/format'

/** 화면 제목 + 설명 + 우측 액션. 모든 관리자 페이지의 첫 줄. */
export const PageHeader = ({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) => (
  <header className="ac-page-header">
    <div>
      <h1 className="ac-page-header__title">{title}</h1>
      {description && <p className="ac-page-header__desc">{description}</p>}
    </div>
    {actions && <div className="ac-page-header__actions">{actions}</div>}
  </header>
)

export const StatusPill = ({ tone, children }: { tone: StatusTone; children: ReactNode }) => (
  <span className={`ac-pill ac-pill--${tone}`}>{children}</span>
)

/** 지표 카드. to를 주면 클릭 가능한 카드가 된다. */
export const MetricCard = ({
  label,
  value,
  hint,
  tone,
  onClick,
  emphasis = false,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: StatusTone
  onClick?: () => void
  emphasis?: boolean
}) => {
  const className = [
    'ac-metric',
    emphasis ? 'ac-metric--emphasis' : '',
    tone ? `ac-metric--${tone}` : '',
    onClick ? 'ac-metric--clickable' : '',
  ].filter(Boolean).join(' ')

  const content = (
    <>
      <span className="ac-metric__label">{label}</span>
      <strong className="ac-metric__value">{value}</strong>
      {hint && <span className="ac-metric__hint">{hint}</span>}
    </>
  )

  if (onClick) {
    return <button type="button" className={className} onClick={onClick}>{content}</button>
  }
  return <article className={className}>{content}</article>
}

export const Panel = ({
  title,
  description,
  actions,
  children,
  padded = true,
}: {
  title?: string
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  padded?: boolean
}) => (
  <section className="ac-panel">
    {(title || actions) && (
      <header className="ac-panel__header">
        <div>
          {title && <h2 className="ac-panel__title">{title}</h2>}
          {description && <p className="ac-panel__desc">{description}</p>}
        </div>
        {actions && <div className="ac-panel__actions">{actions}</div>}
      </header>
    )}
    <div className={padded ? 'ac-panel__body' : 'ac-panel__body ac-panel__body--flush'}>{children}</div>
  </section>
)

export const EmptyState = ({ children }: { children: ReactNode }) => (
  <p className="ac-empty">{children}</p>
)

export const ErrorNote = ({ children }: { children: ReactNode }) => (
  <p className="ac-error" role="alert">{children}</p>
)

export const LoadingNote = ({ children = '불러오는 중입니다...' }: { children?: ReactNode }) => (
  <p className="ac-loading" role="status">{children}</p>
)

/** 필터 바 — 검색·셀렉트·칩을 한 줄에 담는 컨테이너 */
export const FilterBar = ({ children }: { children: ReactNode }) => (
  <div className="ac-filterbar">{children}</div>
)

export const FilterChip = ({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) => (
  <button
    type="button"
    className={`ac-chip ${active ? 'ac-chip--active' : ''}`}
    onClick={onClick}
    aria-pressed={active}
  >
    {children}
  </button>
)

/** 넓은 표가 페이지 전체를 가로 스크롤시키지 않도록 감싸는 래퍼 */
export const TableScroll = ({ children, minWidth, mobileCards = false }: { children: ReactNode; minWidth?: number; mobileCards?: boolean }) => (
  <div className={`ac-table-scroll ${mobileCards ? 'ac-table-cards' : ''}`}>
    <div style={minWidth ? { minWidth: `${minWidth}px` } : undefined}>{children}</div>
  </div>
)

/** 행마다 열리는 보조 메뉴 — 파괴적 동작을 표면에서 한 겹 숨긴다. */
export const RowMenu = ({
  open,
  onToggle,
  children,
  label = '더 보기',
}: {
  open: boolean
  onToggle: () => void
  children: ReactNode
  label?: string
}) => {
  const id = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  return <div className="ac-rowmenu" onKeyDown={(event) => {
    if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); onToggle(); trigger.current?.focus() }
  }}>
    <button
      ref={trigger}
      type="button"
      className="ac-btn ac-btn--sm ac-btn--ghost"
      onClick={onToggle}
      aria-expanded={open}
      aria-controls={id}
      aria-label={label}
    >
      ⋯
    </button>
    {open && <div id={id} className="ac-rowmenu__panel" role="group" aria-label={label}>{children}</div>}
  </div>
}
