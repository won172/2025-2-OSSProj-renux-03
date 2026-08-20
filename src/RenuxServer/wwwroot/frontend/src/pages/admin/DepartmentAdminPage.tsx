import { useCallback, useEffect, useMemo, useState } from 'react'
import { cancelItem, fetchAllItems, submitItem } from '../../admin/adminApi'
import { buildChatPreview, toDepartmentKnowledge } from '../../admin/adminData'
import {
  formatDateTime,
  formatFullDateTime,
  getApiErrorMessage,
  getSourceTypeLabel,
} from '../../admin/format'
import Modal from '../../components/admin/Modal'
import { useAdminConsole } from '../../components/admin/adminConsoleContext'
import {
  EmptyState,
  ErrorNote,
  FilterBar,
  FilterChip,
  LoadingNote,
  MetricCard,
  PageHeader,
  Panel,
  StatusPill,
} from '../../components/admin/ui'
import type { DepartmentKnowledge, KnowledgeStatus } from '../../types/admin'

type ContentType = 'knowledge' | 'event' | 'announcement'
type StatusFilter = 'all' | KnowledgeStatus
type Visibility = 'department' | 'public'

const SOURCE_TYPE_BY_CONTENT: Record<ContentType, string> = {
  knowledge: 'custom_knowledge',
  event: 'event',
  announcement: 'announcement',
}

const STATUS_FILTERS: Array<{ key: StatusFilter; label: string }> = [
  { key: 'all', label: '전체' },
  { key: 'PENDING', label: '대기' },
  { key: 'APPROVED', label: '승인' },
  { key: 'REJECTED', label: '반려' },
]

const statusLabel = (status: KnowledgeStatus) => (
  status === 'APPROVED' ? '승인됨' : status === 'REJECTED' ? '반려됨' : '검수 대기'
)

const statusTone = (status: KnowledgeStatus) => (
  status === 'APPROVED' ? 'success' : status === 'REJECTED' ? 'danger' : 'pending'
)

const emptyForm = {
  title: '',
  content: '',
  startDate: '',
  endDate: '',
  location: '',
  category: '',
}

const DepartmentAdminPage = () => {
  const { showToast, notifyDataChanged, dataVersion, userName, majorName } = useAdminConsole()

  const [items, setItems] = useState<DepartmentKnowledge[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [mode, setMode] = useState<'view' | 'create'>('view')

  const [contentType, setContentType] = useState<ContentType>('knowledge')
  const [visibility, setVisibility] = useState<Visibility>('department')
  const [form, setForm] = useState(emptyForm)
  const [formError, setFormError] = useState<string | null>(null)
  const [showPreview, setShowPreview] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const [cancelTarget, setCancelTarget] = useState<DepartmentKnowledge | null>(null)
  const [busy, setBusy] = useState(false)

  const loadItems = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const data = await fetchAllItems()
      const mapped = (Array.isArray(data) ? data : [])
        .map(toDepartmentKnowledge)
        .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
      setItems(mapped)
    } catch (error) {
      setItems([])
      setLoadError(getApiErrorMessage(error, '등록 내역을 불러오지 못했습니다.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadItems()
  }, [loadItems, dataVersion])

  const filtered = useMemo(
    () => (statusFilter === 'all' ? items : items.filter((item) => item.status === statusFilter)),
    [items, statusFilter],
  )

  useEffect(() => {
    if (mode === 'create') return
    setSelectedId((previous) => (
      previous && filtered.some((item) => item.id === previous) ? previous : filtered[0]?.id ?? null
    ))
  }, [filtered, mode])

  const selected = useMemo(
    () => filtered.find((item) => item.id === selectedId) ?? null,
    [filtered, selectedId],
  )

  const counts = useMemo(() => ({
    total: items.length,
    pending: items.filter((item) => item.status === 'PENDING').length,
    approved: items.filter((item) => item.status === 'APPROVED').length,
    rejected: items.filter((item) => item.status === 'REJECTED').length,
  }), [items])

  const startCreate = (seed?: DepartmentKnowledge) => {
    setMode('create')
    setFormError(null)
    setShowPreview(false)

    if (!seed) {
      setContentType('knowledge')
      setVisibility('department')
      setForm(emptyForm)
      return
    }

    // 반려 항목의 "수정 후 재제출" — 처음부터 다시 쓰지 않도록 원본 payload를 폼에 채운다.
    const raw = seed.raw
    const text = (key: string) => (typeof raw[key] === 'string' ? (raw[key] as string) : '')
    setVisibility(text('visibility') === 'public' ? 'public' : 'department')
    if (seed.sourceType === 'event') {
      setContentType('event')
      setForm({
        title: text('title'),
        content: text('description'),
        startDate: text('start_date'),
        endDate: text('end_date'),
        location: text('location'),
        category: '',
      })
    } else if (seed.sourceType === 'announcement') {
      setContentType('announcement')
      setForm({
        title: text('title'),
        content: text('content'),
        startDate: text('date'),
        endDate: '',
        location: '',
        category: text('category'),
      })
    } else {
      setContentType('knowledge')
      setForm({
        title: text('question'),
        content: text('answer'),
        startDate: '',
        endDate: '',
        location: '',
        category: '',
      })
    }
  }

  const buildPayload = (): Record<string, unknown> | null => {
    const department = majorName || '학과정보'
    if (!form.title.trim() || !form.content.trim()) {
      setFormError('제목과 내용을 입력해주세요.')
      return null
    }

    if (contentType === 'knowledge') {
      return {
        question: form.title.trim(),
        answer: form.content.trim(),
        category: department,
        department,
        visibility,
        requester: userName,
      }
    }

    if (contentType === 'event') {
      if (!form.startDate) {
        setFormError('시작일을 입력해주세요.')
        return null
      }
      return {
        title: form.title.trim(),
        start_date: form.startDate,
        end_date: form.endDate || form.startDate,
        location: form.location.trim(),
        department,
        visibility,
        description: form.content.trim(),
        requester: userName,
      }
    }

    if (!form.startDate) {
      setFormError('게시일을 입력해주세요.')
      return null
    }
    return {
      title: form.title.trim(),
      content: form.content.trim(),
      date: form.startDate,
      category: form.category.trim() || '일반',
      department,
      visibility,
      requester: userName,
    }
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setFormError(null)
    const payload = buildPayload()
    if (!payload) return

    setSubmitting(true)
    try {
      await submitItem(SOURCE_TYPE_BY_CONTENT[contentType], payload)
      showToast('제출했습니다. 검수 승인 후 챗봇에 반영됩니다.', 'success')
      setMode('view')
      setForm(emptyForm)
      setVisibility('department')
      notifyDataChanged()
      await loadItems()
    } catch (error) {
      setFormError(getApiErrorMessage(error, '제출에 실패했습니다. 다시 시도해주세요.'))
    } finally {
      setSubmitting(false)
    }
  }

  const applyCancel = async () => {
    if (!cancelTarget) return
    setBusy(true)
    try {
      await cancelItem(cancelTarget.id)
      setItems((previous) => previous.map((item) => (
        item.id === cancelTarget.id ? { ...item, status: 'REJECTED' as KnowledgeStatus } : item
      )))
      showToast('요청을 취소했습니다.', 'success')
      setCancelTarget(null)
      notifyDataChanged()
    } catch (error) {
      showToast(getApiErrorMessage(error, '요청 취소에 실패했습니다.'), 'error')
    } finally {
      setBusy(false)
    }
  }

  const previewPayload = useMemo(() => {
    const department = majorName || '학과정보'
    if (contentType === 'knowledge') {
      return { question: form.title, answer: form.content, category: department, visibility }
    }
    if (contentType === 'event') {
      return {
        title: form.title,
        start_date: form.startDate,
        end_date: form.endDate || form.startDate,
        location: form.location,
        department,
        visibility,
        description: form.content,
      }
    }
    return {
      title: form.title,
      content: form.content,
      date: form.startDate,
      category: form.category || '일반',
      department,
      visibility,
    }
  }, [contentType, form, majorName, visibility])

  return (
    <>
      <PageHeader
        title="학과 콘솔"
        description={`${majorName || '학과'} 정보를 등록하고 처리 상태를 확인합니다.`}
        actions={
          <>
            <button type="button" className="ac-btn" onClick={() => { void loadItems() }} disabled={loading}>
              {loading ? '새로고침 중...' : '새로고침'}
            </button>
            <button type="button" className="ac-btn ac-btn--primary" onClick={() => startCreate()}>
              + 새 정보 등록
            </button>
          </>
        }
      />

      <div className="ac-metrics">
        <MetricCard label="등록한 정보" value={counts.total} />
        <MetricCard label="검수 대기" value={counts.pending} tone={counts.pending > 0 ? 'pending' : undefined} />
        <MetricCard label="승인됨" value={counts.approved} tone="success" />
        <MetricCard label="반려됨" value={counts.rejected} tone={counts.rejected > 0 ? 'warning' : undefined} />
      </div>

      <Panel padded={false}>
        <FilterBar>
          {STATUS_FILTERS.map((filter) => {
            const count = filter.key === 'all'
              ? counts.total
              : filter.key === 'PENDING' ? counts.pending
                : filter.key === 'APPROVED' ? counts.approved : counts.rejected
            return (
              <FilterChip
                key={filter.key}
                active={statusFilter === filter.key}
                onClick={() => { setStatusFilter(filter.key); setMode('view') }}
              >
                {filter.label} {count}
              </FilterChip>
            )
          })}
        </FilterBar>

        {loadError && <ErrorNote>{loadError}</ErrorNote>}

        <div className="ac-split">
          <div className="ac-list">
            {loading ? (
              <LoadingNote />
            ) : filtered.length === 0 ? (
              <EmptyState>등록된 정보가 없습니다.</EmptyState>
            ) : (
              filtered.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`ac-list-item ${mode === 'view' && selectedId === item.id ? 'ac-list-item--active' : ''}`}
                  onClick={() => { setSelectedId(item.id); setMode('view') }}
                  aria-pressed={mode === 'view' && selectedId === item.id}
                >
                  <span className="ac-list-item__body">
                    <span className="ac-list-item__title">{item.title}</span>
                    <span className="ac-list-item__meta">
                      {getSourceTypeLabel(item.sourceType)} · {formatDateTime(item.createdAt)}
                    </span>
                  </span>
                  <StatusPill tone={statusTone(item.status)}>{statusLabel(item.status)}</StatusPill>
                </button>
              ))
            )}
          </div>

          <div className="ac-detail">
            {mode === 'create' ? (
              <form onSubmit={handleSubmit}>
                <p className="ac-detail__eyebrow">새 정보 등록</p>
                <h2 className="ac-detail__title">정보 입력</h2>

                <div className="ac-field">
                  <label className="ac-label" htmlFor="dept-content-type">등록 유형</label>
                  <select
                    id="dept-content-type"
                    className="ac-select"
                    value={contentType}
                    onChange={(event) => setContentType(event.target.value as ContentType)}
                    disabled={submitting}
                  >
                    <option value="knowledge">❓ 자주 묻는 질문 (FAQ)</option>
                    <option value="event">📅 학과 행사 (Event)</option>
                    <option value="announcement">📢 공지사항 (Notice)</option>
                  </select>
                </div>

                <div className="ac-field">
                  <label className="ac-label" htmlFor="dept-visibility">공개 범위</label>
                  <select
                    id="dept-visibility"
                    className="ac-select"
                    value={visibility}
                    onChange={(event) => setVisibility(event.target.value as Visibility)}
                    disabled={submitting}
                  >
                    <option value="department">{majorName || '소속 학과'} 학생에게만 공개</option>
                    <option value="public">전체 사용자에게 공개</option>
                  </select>
                  <span className="ac-hint">
                    학과 전용 정보는 게스트와 다른 학과 학생의 홈·채팅 검색에 표시되지 않습니다.
                  </span>
                </div>

                <div className="ac-field">
                  <label className="ac-label" htmlFor="dept-title">
                    {contentType === 'knowledge' ? '질문 (Question)' : contentType === 'event' ? '행사명 (Title)' : '제목 (Title)'}
                  </label>
                  <input
                    id="dept-title"
                    type="text"
                    className="ac-input"
                    placeholder={contentType === 'knowledge' ? '예: 졸업논문 제출 기한' : '제목을 입력하세요'}
                    value={form.title}
                    onChange={(event) => setForm((previous) => ({ ...previous, title: event.target.value }))}
                    disabled={submitting}
                  />
                </div>

                {(contentType === 'event' || contentType === 'announcement') && (
                  <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
                    <div className="ac-field" style={{ flex: 1, minWidth: '140px' }}>
                      <label className="ac-label" htmlFor="dept-start-date">
                        {contentType === 'event' ? '시작일' : '게시일'}
                      </label>
                      <input
                        id="dept-start-date"
                        type="date"
                        className="ac-input"
                        value={form.startDate}
                        onChange={(event) => setForm((previous) => ({ ...previous, startDate: event.target.value }))}
                        disabled={submitting}
                      />
                    </div>
                    {contentType === 'event' && (
                      <div className="ac-field" style={{ flex: 1, minWidth: '140px' }}>
                        <label className="ac-label" htmlFor="dept-end-date">종료일 (선택)</label>
                        <input
                          id="dept-end-date"
                          type="date"
                          className="ac-input"
                          value={form.endDate}
                          onChange={(event) => setForm((previous) => ({ ...previous, endDate: event.target.value }))}
                          disabled={submitting}
                        />
                      </div>
                    )}
                  </div>
                )}

                {contentType === 'event' && (
                  <div className="ac-field">
                    <label className="ac-label" htmlFor="dept-location">장소</label>
                    <input
                      id="dept-location"
                      type="text"
                      className="ac-input"
                      placeholder="예: 공학관 101호"
                      value={form.location}
                      onChange={(event) => setForm((previous) => ({ ...previous, location: event.target.value }))}
                      disabled={submitting}
                    />
                  </div>
                )}

                {contentType === 'announcement' && (
                  <div className="ac-field">
                    <label className="ac-label" htmlFor="dept-category">카테고리</label>
                    <input
                      id="dept-category"
                      type="text"
                      className="ac-input"
                      placeholder="예: 학사, 장학, 채용"
                      value={form.category}
                      onChange={(event) => setForm((previous) => ({ ...previous, category: event.target.value }))}
                      disabled={submitting}
                    />
                  </div>
                )}

                <div className="ac-field">
                  <label className="ac-label" htmlFor="dept-content">
                    {contentType === 'knowledge' ? '답변 (Answer)' : '상세 내용'}
                  </label>
                  <textarea
                    id="dept-content"
                    className="ac-textarea"
                    rows={9}
                    placeholder="상세 내용을 입력하세요."
                    value={form.content}
                    onChange={(event) => setForm((previous) => ({ ...previous, content: event.target.value }))}
                    disabled={submitting}
                  />
                </div>

                {formError && <ErrorNote>{formError}</ErrorNote>}

                <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                  <button
                    type="button"
                    className="ac-btn ac-btn--sm"
                    onClick={() => setShowPreview((open) => !open)}
                    aria-expanded={showPreview}
                  >
                    {showPreview ? '미리보기 닫기' : '챗봇 미리보기'}
                  </button>
                </div>

                {showPreview && (
                  <div className="ac-preview">
                    <span className="ac-preview__label">승인되면 챗봇이 이렇게 답합니다</span>
                    <div className="ac-preview__bubble">
                      {buildChatPreview(SOURCE_TYPE_BY_CONTENT[contentType], previewPayload) || '내용을 입력하면 미리보기가 표시됩니다.'}
                    </div>
                  </div>
                )}

                <div className="ac-detail__actions">
                  <button type="button" className="ac-btn" onClick={() => setMode('view')} disabled={submitting}>
                    취소
                  </button>
                  <button type="submit" className="ac-btn ac-btn--primary" disabled={submitting}>
                    {submitting ? '제출 중...' : '제출하기'}
                  </button>
                </div>
              </form>
            ) : !selected ? (
              <EmptyState>왼쪽 목록에서 정보를 선택하거나, '새 정보 등록'을 눌러주세요.</EmptyState>
            ) : (
              <>
                <p className="ac-detail__eyebrow">{getSourceTypeLabel(selected.sourceType)}</p>
                <h2 className="ac-detail__title">{selected.title}</h2>

                <dl className="ac-detail__meta">
                  <div>
                    <dt>상태</dt>
                    <dd><StatusPill tone={statusTone(selected.status)}>{statusLabel(selected.status)}</StatusPill></dd>
                  </div>
                  <div>
                    <dt>등록일</dt>
                    <dd>{formatFullDateTime(selected.createdAt)}</dd>
                  </div>
                </dl>

                {selected.status === 'REJECTED' && (
                  <div className="ac-detail__content" style={{ borderColor: '#fecaca', background: '#fef2f2', marginBottom: '12px' }}>
                    <b>반려 사유:</b> {selected.rejectionReason?.trim() || '사유가 기록되지 않았습니다. 검수 담당자에게 문의해주세요.'}
                  </div>
                )}

                <div className="ac-detail__content">{selected.content || '내용이 없습니다.'}</div>

                <div className="ac-detail__actions">
                  {selected.status === 'REJECTED' && (
                    <button type="button" className="ac-btn ac-btn--primary" onClick={() => startCreate(selected)}>
                      수정 후 재제출
                    </button>
                  )}
                  {selected.status === 'PENDING' && (
                    <button
                      type="button"
                      className="ac-btn ac-btn--danger-ghost"
                      onClick={() => setCancelTarget(selected)}
                    >
                      요청 취소
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </Panel>

      <Modal
        open={cancelTarget !== null}
        title="요청 취소"
        description={<><b>{cancelTarget?.title}</b> 요청을 취소합니다. 취소한 요청은 반려 상태로 남으며, 필요하면 새로 등록할 수 있습니다.</>}
        confirmLabel="요청 취소"
        tone="danger"
        busy={busy}
        onCancel={() => setCancelTarget(null)}
        onConfirm={applyCancel}
      />
    </>
  )
}

export default DepartmentAdminPage
