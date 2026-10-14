import { Link } from 'react-router-dom'
import { supportsContentEditing } from '../../admin/staffReview'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchAllItems, setItemDisabled, updateItem } from '../../admin/adminApi'
import { buildChatPreview, getEditableFields, toPendingAnswerReview } from '../../admin/adminData'
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
  RowMenu,
  StatusPill,
  TableScroll,
} from '../../components/admin/ui'
import type { PendingAnswerReview } from '../../types/admin'

type VisibilityFilter = 'active' | 'disabled' | 'all'

const SOURCE_FILTERS = [
  { key: 'all', label: '전체 유형' },
  { key: 'custom_knowledge', label: 'FAQ' },
  { key: 'event', label: '행사' },
  { key: 'announcement', label: '공지' },
  { key: 'staff_refresh', label: '교직원 갱신' },
]

/**
 * 승인된 지식의 사후 관리 화면.
 * 지금까지는 한번 승인하면 잘못된 내용도 손댈 방법이 없어 챗봇이 계속 그 답을 했다.
 */
const ContentPage = () => {
  const { showToast, notifyDataChanged, dataVersion } = useAdminConsole()

  const [items, setItems] = useState<PendingAnswerReview[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [sourceFilter, setSourceFilter] = useState('all')
  const [departmentFilter, setDepartmentFilter] = useState('all')
  const [visibility, setVisibility] = useState<VisibilityFilter>('active')
  const [search, setSearch] = useState('')
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)

  const [editTarget, setEditTarget] = useState<PendingAnswerReview | null>(null)
  const [editDraft, setEditDraft] = useState<Record<string, string>>({})
  const [disableTarget, setDisableTarget] = useState<{ item: PendingAnswerReview; next: boolean } | null>(null)
  const [busy, setBusy] = useState(false)

  const loadItems = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchAllItems()
      const approved = (Array.isArray(data) ? data : [])
        .map(toPendingAnswerReview)
        .filter((item) => item.status === 'approved' || item.status === 'approved_manually')
        .sort((a, b) => new Date(b.submittedAt).getTime() - new Date(a.submittedAt).getTime())
      setItems(approved)
    } catch (fetchError) {
      setItems([])
      setError(getApiErrorMessage(fetchError, '승인된 콘텐츠를 불러오지 못했습니다.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadItems()
  }, [loadItems, dataVersion])

  useEffect(() => {
    if (!openMenuId) return
    const close = () => setOpenMenuId(null)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [openMenuId])

  const departments = useMemo(
    () => Array.from(new Set(items.map((item) => item.departmentName))).sort(),
    [items],
  )

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return items.filter((item) => {
      if (sourceFilter !== 'all' && item.sourceType !== sourceFilter) return false
      if (departmentFilter !== 'all' && item.departmentName !== departmentFilter) return false
      if (visibility === 'active' && (item.disabled || !supportsContentEditing(item.sourceType))) return false
      if (visibility === 'disabled' && !item.disabled) return false
      if (term && !item.question.toLowerCase().includes(term) && !item.answer.toLowerCase().includes(term)) return false
      return true
    })
  }, [items, sourceFilter, departmentFilter, visibility, search])

  const editableItems = items.filter((item) => supportsContentEditing(item.sourceType))
  const activeCount = editableItems.filter((item) => !item.disabled).length
  const disabledCount = editableItems.length - activeCount

  const startEdit = (item: PendingAnswerReview) => {
    if (!supportsContentEditing(item.sourceType)) return
    const draft: Record<string, string> = {}
    getEditableFields(item.sourceType).forEach((field) => {
      const value = item.raw[field.key]
      draft[field.key] = typeof value === 'string' ? value : ''
    })
    setEditDraft(draft)
    setEditTarget(item)
    setOpenMenuId(null)
  }

  const saveEdit = async () => {
    if (!editTarget || !supportsContentEditing(editTarget.sourceType)) return
    setBusy(true)
    try {
      const nextData = { ...editTarget.raw, ...editDraft }
      await updateItem(editTarget.id, nextData)
      showToast('수정했습니다. 색인이 갱신되면 챗봇 답변에 반영됩니다.', 'success', { duration: 6000 })
      setEditTarget(null)
      notifyDataChanged()
      await loadItems()
    } catch (saveError) {
      showToast(getApiErrorMessage(saveError, '수정에 실패했습니다.'), 'error')
    } finally {
      setBusy(false)
    }
  }

  const applyDisable = async () => {
    if (!disableTarget || !supportsContentEditing(disableTarget.item.sourceType)) return
    setBusy(true)
    try {
      await setItemDisabled(disableTarget.item.id, disableTarget.next)
      setItems((previous) => previous.map((item) => (
        item.id === disableTarget.item.id ? { ...item, disabled: disableTarget.next } : item
      )))
      showToast(
        disableTarget.next
          ? '챗봇 노출을 중단했습니다.'
          : '다시 챗봇 답변에 사용됩니다.',
        'success',
      )
      setDisableTarget(null)
      notifyDataChanged()
    } catch (disableError) {
      showToast(getApiErrorMessage(disableError, '노출 설정 변경에 실패했습니다.'), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="콘텐츠"
        description="FAQ·행사·공지의 내용을 수정하거나 노출을 관리합니다. 교직원 갱신 승인 이력은 전체 탭에서 확인합니다."
        actions={
          <button type="button" className="ac-btn" onClick={() => { void loadItems() }} disabled={loading}>
            {loading ? '새로고침 중...' : '새로고침'}
          </button>
        }
      />

      <div className="ac-metrics">
        <MetricCard label="사용 중" value={activeCount} tone="success" />
        <MetricCard label="노출 중단" value={disabledCount} tone={disabledCount > 0 ? 'warning' : undefined} />
        <MetricCard label="전체 승인" value={items.length} />
      </div>

      <Panel padded={false}>
        <FilterBar>
          <FilterChip active={visibility === 'active'} onClick={() => setVisibility('active')}>사용 중</FilterChip>
          <FilterChip active={visibility === 'disabled'} onClick={() => setVisibility('disabled')}>노출 중단</FilterChip>
          <FilterChip active={visibility === 'all'} onClick={() => setVisibility('all')}>전체</FilterChip>
          <select
            className="ac-select"
            value={sourceFilter}
            onChange={(event) => setSourceFilter(event.target.value)}
            aria-label="등록 유형 필터"
          >
            {SOURCE_FILTERS.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
          </select>
          <select
            className="ac-select"
            value={departmentFilter}
            onChange={(event) => setDepartmentFilter(event.target.value)}
            aria-label="학과 필터"
          >
            <option value="all">전체 학과</option>
            {departments.map((department) => <option key={department} value={department}>{department}</option>)}
          </select>
          <input
            type="search"
            className="ac-input ac-input--grow"
            placeholder="제목·내용 검색"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="콘텐츠 검색"
          />
        </FilterBar>

        {loading ? (
          <LoadingNote />
        ) : error ? (
          <ErrorNote>{error}</ErrorNote>
        ) : filtered.length === 0 ? (
          <EmptyState>조건에 맞는 승인 콘텐츠가 없습니다.</EmptyState>
        ) : (
          <TableScroll mobileCards minWidth={940}>
            <table className="ac-table" role="table">
              <thead role="rowgroup">
                <tr role="row">
                  <th role="columnheader" scope="col">제목</th>
                  <th role="columnheader" scope="col">유형</th>
                  <th role="columnheader" scope="col">학과</th>
                  <th role="columnheader" scope="col">등록자</th>
                  <th role="columnheader" scope="col">승인일</th>
                  <th role="columnheader" scope="col">노출</th>
                  <th role="columnheader" scope="col" className="actions">관리</th>
                </tr>
              </thead>
              <tbody role="rowgroup">
                {filtered.map((item) => (
                  <tr role="row" key={item.id}>
                    <td role="cell" data-label="제목" className="truncate" title={item.question}><b>{item.question}</b><details className="ac-cell-details"><summary>내용 보기</summary><p>{item.answer || '내용 확인 필요'}</p></details></td>
                    <td role="cell" data-label="유형">{getSourceTypeLabel(item.sourceType)}</td>
                    <td role="cell" data-label="학과">{item.departmentName}</td>
                    <td role="cell" data-label="등록자">{item.handler}</td>
                    <td role="cell" data-label="승인일" className="num" title={formatFullDateTime(item.reviewedAt ?? item.submittedAt)}>
                      {formatDateTime(item.reviewedAt ?? item.submittedAt)}
                    </td>
                    <td role="cell" data-label="상태">
                      <StatusPill tone={item.disabled ? 'neutral' : 'success'}>
                        {!supportsContentEditing(item.sourceType) ? '승인 이력' : item.disabled ? '중단' : '사용 중'}
                      </StatusPill>
                    </td>
                    <td role="cell" data-label="관리" className="actions" onClick={(event) => event.stopPropagation()}>
                      {!supportsContentEditing(item.sourceType) ? <Link className="ac-btn ac-btn--sm" to={`/admin/review?item=${item.id}`}>변경 검토 보기</Link> : <RowMenu
                        open={openMenuId === item.id}
                        onToggle={() => setOpenMenuId(openMenuId === item.id ? null : item.id)}
                        label={`${item.question} 관리`}
                      >
                        <button type="button" onClick={() => startEdit(item)}>내용 수정</button>
                        <button
                          type="button"
                          className={item.disabled ? '' : 'danger'}
                          onClick={() => { setDisableTarget({ item, next: !item.disabled }); setOpenMenuId(null) }}
                        >
                          {item.disabled ? '다시 노출' : '노출 중단'}
                        </button>
                      </RowMenu>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        )}
      </Panel>

      <Modal
        open={editTarget !== null}
        title="콘텐츠 수정"
        description="저장하면 챗봇이 참조하는 내용이 바뀝니다. 색인 갱신에는 잠시 시간이 걸릴 수 있습니다."
        confirmLabel="저장"
        busy={busy}
        onCancel={() => setEditTarget(null)}
        onConfirm={saveEdit}
      >
        {editTarget && getEditableFields(editTarget.sourceType).map((field) => (
          <div className="ac-field" key={field.key}>
            <label className="ac-label" htmlFor={`content-${field.key}`}>{field.label}</label>
            {field.type === 'textarea' ? (
              <textarea
                id={`content-${field.key}`}
                className="ac-textarea"
                rows={6}
                value={editDraft[field.key] ?? ''}
                onChange={(event) => setEditDraft((draft) => ({ ...draft, [field.key]: event.target.value }))}
              />
            ) : (
              <input
                id={`content-${field.key}`}
                type={field.type === 'date' ? 'date' : 'text'}
                className="ac-input"
                value={editDraft[field.key] ?? ''}
                onChange={(event) => setEditDraft((draft) => ({ ...draft, [field.key]: event.target.value }))}
              />
            )}
          </div>
        ))}
        {editTarget && (
          <div className="ac-preview">
            <span className="ac-preview__label">챗봇에 이렇게 반영됩니다</span>
            <div className="ac-preview__bubble">
              {buildChatPreview(editTarget.sourceType, { ...editTarget.raw, ...editDraft }) || '표시할 내용이 없습니다.'}
            </div>
          </div>
        )}
      </Modal>

      <Modal
        open={disableTarget !== null}
        title={disableTarget?.next ? '챗봇 노출 중단' : '챗봇 노출 재개'}
        description={
          disableTarget?.next
            ? <><b>{disableTarget?.item.question}</b> 항목을 챗봇 답변에서 제외합니다. 내용은 남아 있으므로 언제든 다시 노출할 수 있습니다.</>
            : <><b>{disableTarget?.item.question}</b> 항목을 다시 챗봇 답변에 사용합니다.</>
        }
        confirmLabel={disableTarget?.next ? '노출 중단' : '다시 노출'}
        tone={disableTarget?.next ? 'danger' : 'default'}
        busy={busy}
        onCancel={() => setDisableTarget(null)}
        onConfirm={applyDisable}
      />
    </>
  )
}

export default ContentPage
