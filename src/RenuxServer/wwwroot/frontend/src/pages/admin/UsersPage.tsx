import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  deleteAdminUser,
  fetchAdminRoles,
  fetchAdminUsers,
  fetchCouncilSignupRequests,
  fetchMajors,
  fetchOrganizations,
  resetAdminUserPassword,
  reviewCouncilSignup,
  updateAdminUser,
} from '../../admin/adminApi'
import {
  formatDateTime,
  getApiErrorMessage,
  getRequestStatusLabel,
  getRequestStatusTone,
} from '../../admin/format'
import Modal from '../../components/admin/Modal'
import { useAdminConsole } from '../../components/admin/adminConsoleContext'
import {
  EmptyState,
  ErrorNote,
  FilterBar,
  LoadingNote,
  PageHeader,
  Panel,
  RowMenu,
  StatusPill,
  TableScroll,
} from '../../components/admin/ui'
import type {
  AdminRoleOption,
  AdminUserAccount,
  CouncilOrganization,
  CouncilSignupRequest,
  MajorOption,
} from '../../types/admin'

type Tab = 'accounts' | 'signup' | 'orgs'
type SortKey = 'username' | 'userId' | 'roleName' | 'updatedTime'

const PAGE_SIZE = 25
const MIN_PASSWORD = 10
const MAX_PASSWORD = 30

const majorLabel = (major: MajorOption) => major.majorname ?? major.Majorname ?? '전공 미상'

const UsersPage = () => {
  const { showToast, notifyDataChanged, dataVersion, pendingSignupCount } = useAdminConsole()
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab')
  const tab: Tab = tabParam === 'signup' || tabParam === 'orgs' ? tabParam : 'accounts'

  const [compact, setCompact] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 960px)').matches)
  useEffect(() => {
    const media = window.matchMedia('(max-width: 960px)')
    const update = () => setCompact(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  const [users, setUsers] = useState<AdminUserAccount[]>([])
  const [roles, setRoles] = useState<AdminRoleOption[]>([])
  const [majors, setMajors] = useState<MajorOption[]>([])
  const [signupRequests, setSignupRequests] = useState<CouncilSignupRequest[]>([])
  const [organizations, setOrganizations] = useState<CouncilOrganization[]>([])

  const [loading, setLoading] = useState(true)
  const [accountError, setAccountError] = useState<string | null>(null)
  const [signupError, setSignupError] = useState<string | null>(null)
  const [orgError, setOrgError] = useState<string | null>(null)

  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState('all')
  const [majorFilter, setMajorFilter] = useState('all')
  const [sortKey, setSortKey] = useState<SortKey>('updatedTime')
  const [sortAsc, setSortAsc] = useState(false)
  const [page, setPage] = useState(0)
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)

  const [roleChange, setRoleChange] = useState<
    { user: AdminUserAccount; field: 'roleId' | 'majorId'; value: string; label: string } | null
  >(null)
  const [passwordTarget, setPasswordTarget] = useState<AdminUserAccount | null>(null)
  const [passwordValue, setPasswordValue] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<AdminUserAccount | null>(null)
  const [deleteConfirmText, setDeleteConfirmText] = useState('')
  const [signupAction, setSignupAction] = useState<
    { request: CouncilSignupRequest; action: 'approve' | 'reject' } | null
  >(null)
  const [signupNote, setSignupNote] = useState('')
  const [busy, setBusy] = useState(false)

  const loadAll = useCallback(async () => {
    setLoading(true)
    const [usersResult, rolesResult, majorsResult, signupResult, orgsResult] = await Promise.allSettled([
      fetchAdminUsers(),
      fetchAdminRoles(),
      fetchMajors(),
      fetchCouncilSignupRequests(),
      fetchOrganizations(),
    ])

    if (usersResult.status === 'fulfilled' && Array.isArray(usersResult.value)) {
      setUsers(usersResult.value)
      setAccountError(null)
    } else {
      setUsers([])
      setAccountError('계정 관리는 관리자 계정만 사용할 수 있습니다.')
    }

    setRoles(rolesResult.status === 'fulfilled' && Array.isArray(rolesResult.value) ? rolesResult.value : [])
    setMajors(majorsResult.status === 'fulfilled' && Array.isArray(majorsResult.value) ? majorsResult.value : [])

    if (signupResult.status === 'fulfilled' && Array.isArray(signupResult.value)) {
      const sorted = [...signupResult.value].sort((a, b) => {
        const aPending = a.status === 'pending'
        const bPending = b.status === 'pending'
        if (aPending !== bPending) return aPending ? -1 : 1
        return new Date(b.createdTime).getTime() - new Date(a.createdTime).getTime()
      })
      setSignupRequests(sorted)
      setSignupError(null)
    } else {
      setSignupRequests([])
      setSignupError('학생회 가입 요청을 불러오지 못했습니다.')
    }

    if (orgsResult.status === 'fulfilled') {
      setOrganizations(orgsResult.value)
      setOrgError(null)
    } else {
      setOrganizations([])
      setOrgError('조직 현황을 불러오지 못했습니다.')
    }

    setLoading(false)
  }, [])

  useEffect(() => {
    void loadAll()
  }, [loadAll, dataVersion])

  // 행 메뉴는 바깥을 누르면 닫힌다 — 열린 메뉴가 다른 조작을 가리지 않도록.
  useEffect(() => {
    if (!openMenuId) return
    const close = () => setOpenMenuId(null)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [openMenuId])

  const filteredUsers = useMemo(() => {
    const term = search.trim().toLowerCase()
    const result = users.filter((user) => {
      const matchesSearch = !term
        || user.username.toLowerCase().includes(term)
        || user.userId.toLowerCase().includes(term)
      const matchesRole = roleFilter === 'all' || user.roleId === roleFilter
      const matchesMajor = majorFilter === 'all' || user.majorId === majorFilter
      return matchesSearch && matchesRole && matchesMajor
    })

    return result.sort((a, b) => {
      const direction = sortAsc ? 1 : -1
      if (sortKey === 'updatedTime') {
        return (new Date(a.updatedTime).getTime() - new Date(b.updatedTime).getTime()) * direction
      }
      const aValue = String(a[sortKey] ?? '')
      const bValue = String(b[sortKey] ?? '')
      return aValue.localeCompare(bValue, 'ko') * direction
    })
  }, [users, search, roleFilter, majorFilter, sortKey, sortAsc])

  const safePage = Math.min(page, Math.max(0, Math.ceil(filteredUsers.length / PAGE_SIZE) - 1))
  const pagedUsers = filteredUsers.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE)
  const pageCount = Math.max(1, Math.ceil(filteredUsers.length / PAGE_SIZE))

  useEffect(() => { setPage(0) }, [search, roleFilter, majorFilter])

  const setTab = (next: Tab) => {
    searchParams.set('tab', next)
    setSearchParams(searchParams, { replace: true })
  }

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc((asc) => !asc)
    else { setSortKey(key); setSortAsc(true) }
  }

  const applyUserUpdate = async () => {
    if (!roleChange) return
    setBusy(true)
    try {
      const update = roleChange.field === 'roleId'
        ? { roleId: roleChange.value }
        : { majorId: roleChange.value }
      await updateAdminUser(roleChange.user.id, update)

      setUsers((previous) => previous.map((user) => {
        if (user.id !== roleChange.user.id) return user
        const nextRole = roles.find((role) => role.id === roleChange.value)
        const nextMajor = majors.find((major) => major.id === roleChange.value)
        return {
          ...user,
          roleId: roleChange.field === 'roleId' ? roleChange.value : user.roleId,
          roleName: roleChange.field === 'roleId' ? nextRole?.roleName ?? user.roleName : user.roleName,
          majorId: roleChange.field === 'majorId' ? roleChange.value : user.majorId,
          majorName: roleChange.field === 'majorId' ? (nextMajor ? majorLabel(nextMajor) : user.majorName) : user.majorName,
          updatedTime: new Date().toISOString(),
        }
      }))
      showToast(`${roleChange.user.userId} 계정을 수정했습니다.`, 'success')
      notifyDataChanged()
      setRoleChange(null)
    } catch (error) {
      showToast(getApiErrorMessage(error, '계정 정보 수정에 실패했습니다.'), 'error')
    } finally {
      setBusy(false)
    }
  }

  const applyPasswordReset = async () => {
    if (!passwordTarget) return
    setBusy(true)
    try {
      await resetAdminUserPassword(passwordTarget.id, passwordValue)
      showToast(`${passwordTarget.userId} 비밀번호를 재설정했습니다.`, 'success')
      setPasswordTarget(null)
      setPasswordValue('')
    } catch (error) {
      showToast(getApiErrorMessage(error, '비밀번호 재설정에 실패했습니다.'), 'error')
    } finally {
      setBusy(false)
    }
  }

  const applyDelete = async () => {
    if (!deleteTarget) return
    setBusy(true)
    try {
      await deleteAdminUser(deleteTarget.id)
      setUsers((previous) => previous.filter((user) => user.id !== deleteTarget.id))
      showToast(`${deleteTarget.userId} 계정을 삭제했습니다.`, 'success')
      setDeleteTarget(null)
      setDeleteConfirmText('')
      notifyDataChanged()
    } catch (error) {
      showToast(getApiErrorMessage(error, '계정 삭제에 실패했습니다.'), 'error')
    } finally {
      setBusy(false)
    }
  }

  const applySignupAction = async () => {
    if (!signupAction) return
    setBusy(true)
    try {
      const result = await reviewCouncilSignup(signupAction.request.id, signupAction.action, signupNote)
      setSignupRequests((previous) => previous.map((request) => (
        request.id === signupAction.request.id
          ? {
              ...request,
              status: signupAction.action === 'approve' ? 'approved' : 'rejected',
              reviewedTime: new Date().toISOString(),
              reviewNote: signupNote.trim() || null,
            }
          : request
      )))
      showToast(
        result.message ?? `가입 요청을 ${signupAction.action === 'approve' ? '승인' : '반려'}했습니다.`,
        'success',
      )
      notifyDataChanged()
      setSignupAction(null)
      setSignupNote('')
    } catch (error) {
      showToast(getApiErrorMessage(error, '가입 요청 처리에 실패했습니다.'), 'error')
    } finally {
      setBusy(false)
    }
  }

  const passwordValid = passwordValue.length >= MIN_PASSWORD && passwordValue.length <= MAX_PASSWORD

  return (
    <>
      <PageHeader
        title="사용자·조직"
        description="계정 권한, 학생회 가입 요청, 조직 현황을 관리합니다."
      />

      <div className="ac-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'accounts'}
          className={`ac-tab ${tab === 'accounts' ? 'ac-tab--active' : ''}`}
          onClick={() => setTab('accounts')}
        >
          계정 {users.length > 0 && `(${users.length})`}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'signup'}
          className={`ac-tab ${tab === 'signup' ? 'ac-tab--active' : ''}`}
          onClick={() => setTab('signup')}
        >
          가입 요청
          {pendingSignupCount > 0 && <span className="ac-tab__badge">{pendingSignupCount}</span>}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'orgs'}
          className={`ac-tab ${tab === 'orgs' ? 'ac-tab--active' : ''}`}
          onClick={() => setTab('orgs')}
        >
          조직 {organizations.length > 0 && `(${organizations.length})`}
        </button>
      </div>

      {tab === 'accounts' && (
        <Panel padded={false}>
          <FilterBar>
            <input
              type="search"
              className="ac-input ac-input--grow"
              placeholder="이름 또는 아이디 검색"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              aria-label="계정 검색"
            />
            <select
              className="ac-select"
              value={roleFilter}
              onChange={(event) => setRoleFilter(event.target.value)}
              aria-label="역할 필터"
            >
              <option value="all">전체 역할</option>
              {roles.map((role) => <option key={role.id} value={role.id}>{role.roleName}</option>)}
            </select>
            <select
              className="ac-select"
              value={majorFilter}
              onChange={(event) => setMajorFilter(event.target.value)}
              aria-label="학과 필터"
            >
              <option value="all">전체 학과</option>
              {majors.map((major) => <option key={major.id} value={major.id}>{majorLabel(major)}</option>)}
            </select>
          </FilterBar>

          {loading ? (
            <LoadingNote />
          ) : accountError ? (
            <ErrorNote>{accountError}</ErrorNote>
          ) : filteredUsers.length === 0 ? (
            <EmptyState>{users.length === 0 ? '관리할 계정이 없습니다.' : '검색 결과가 없습니다.'}</EmptyState>
          ) : (
            <>
              <div className="ac-mobile-sort">
                <label htmlFor="user-sort">정렬 기준</label>
                <select id="user-sort" className="ac-select" value={sortKey} onChange={(event) => { setSortKey(event.target.value as SortKey); setPage(0) }}>
                  <option value="userId">아이디</option><option value="username">이름</option><option value="roleName">역할</option><option value="updatedTime">수정일</option>
                </select>
                <button type="button" className="ac-btn" onClick={() => setSortAsc((value) => !value)}>{sortAsc ? '오름차순 ↑' : '내림차순 ↓'}</button>
              </div>
              <TableScroll mobileCards minWidth={900}>
                <table className="ac-table" role="table">
                  <thead role="rowgroup">
                    <tr role="row">
                      <th role="columnheader" scope="col"><button type="button" tabIndex={compact ? -1 : 0} onClick={() => toggleSort('userId')}>아이디 {sortKey === 'userId' ? (sortAsc ? '▲' : '▼') : ''}</button></th>
                      <th role="columnheader" scope="col"><button type="button" tabIndex={compact ? -1 : 0} onClick={() => toggleSort('username')}>이름 {sortKey === 'username' ? (sortAsc ? '▲' : '▼') : ''}</button></th>
                      <th role="columnheader" scope="col">전공</th>
                      <th role="columnheader" scope="col"><button type="button" tabIndex={compact ? -1 : 0} onClick={() => toggleSort('roleName')}>역할 {sortKey === 'roleName' ? (sortAsc ? '▲' : '▼') : ''}</button></th>
                      <th role="columnheader" scope="col"><button type="button" tabIndex={compact ? -1 : 0} onClick={() => toggleSort('updatedTime')}>수정일 {sortKey === 'updatedTime' ? (sortAsc ? '▲' : '▼') : ''}</button></th>
                      <th role="columnheader" scope="col" className="actions">관리</th>
                    </tr>
                  </thead>
                  <tbody role="rowgroup">
                    {pagedUsers.map((user) => (
                      <tr role="row" key={user.id}>
                        <td role="cell" data-label="아이디">{user.userId}</td>
                        <td role="cell" data-label="이름">{user.username}</td>
                        <td role="cell" data-label="전공">
                          <select
                            className="ac-select"
                            value={user.majorId}
                            onChange={(event) => setRoleChange({
                              user,
                              field: 'majorId',
                              value: event.target.value,
                              label: majors.find((major) => major.id === event.target.value)
                                ? majorLabel(majors.find((major) => major.id === event.target.value)!)
                                : '선택한 전공',
                            })}
                            aria-label={`${user.username} 전공 변경`}
                          >
                            {majors.map((major) => (
                              <option key={major.id} value={major.id}>{majorLabel(major)}</option>
                            ))}
                          </select>
                        </td>
                        <td role="cell" data-label="역할">
                          <select
                            className="ac-select"
                            value={user.roleId}
                            onChange={(event) => setRoleChange({
                              user,
                              field: 'roleId',
                              value: event.target.value,
                              label: roles.find((role) => role.id === event.target.value)?.roleName ?? '선택한 역할',
                            })}
                            aria-label={`${user.username} 역할 변경`}
                          >
                            {roles.map((role) => (
                              <option key={role.id} value={role.id}>{role.roleName}</option>
                            ))}
                          </select>
                        </td>
                        <td role="cell" data-label="수정일" className="num">{formatDateTime(user.updatedTime)}</td>
                        <td role="cell" data-label="관리" className="actions" onClick={(event) => event.stopPropagation()}>
                          <RowMenu
                            open={openMenuId === user.id}
                            onToggle={() => setOpenMenuId(openMenuId === user.id ? null : user.id)}
                            label={`${user.username} 계정 관리`}
                          >
                            <button
                              type="button"
                              onClick={() => { setPasswordTarget(user); setPasswordValue(''); setOpenMenuId(null) }}
                            >
                              비밀번호 재설정
                            </button>
                            <button
                              type="button"
                              className="danger"
                              onClick={() => { setDeleteTarget(user); setDeleteConfirmText(''); setOpenMenuId(null) }}
                            >
                              계정 삭제
                            </button>
                          </RowMenu>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>

              {pageCount > 1 && (
                <div className="ac-filterbar" style={{ justifyContent: 'center', borderTop: '1px solid var(--ac-line)', borderBottom: 0 }}>
                  <button type="button" className="ac-btn ac-btn--sm" onClick={() => setPage(Math.max(0, safePage - 1))} disabled={safePage === 0}>
                    이전
                  </button>
                  <span className="ac-hint">{safePage + 1} / {pageCount} · 총 {filteredUsers.length}개</span>
                  <button type="button" className="ac-btn ac-btn--sm" onClick={() => setPage(Math.min(pageCount - 1, safePage + 1))} disabled={safePage >= pageCount - 1}>
                    다음
                  </button>
                </div>
              )}
            </>
          )}
        </Panel>
      )}

      {tab === 'signup' && (
        <Panel padded={false} title="학생회 가입 요청" description="신청을 확인하고 승인 또는 반려합니다.">
          {loading ? (
            <LoadingNote />
          ) : signupError ? (
            <ErrorNote>{signupError}</ErrorNote>
          ) : signupRequests.length === 0 ? (
            <EmptyState>학생회 가입 요청이 없습니다.</EmptyState>
          ) : (
            <TableScroll mobileCards minWidth={860}>
              <table className="ac-table" role="table">
                <thead role="rowgroup">
                  <tr role="row">
                    <th role="columnheader" scope="col">아이디</th>
                    <th role="columnheader" scope="col">이름</th>
                    <th role="columnheader" scope="col">전공</th>
                    <th role="columnheader" scope="col">신청일</th>
                    <th role="columnheader" scope="col">상태</th>
                    <th role="columnheader" scope="col">처리 메모</th>
                    <th role="columnheader" scope="col" className="actions">처리</th>
                  </tr>
                </thead>
                <tbody role="rowgroup">
                  {signupRequests.map((request) => (
                    <tr role="row" key={request.id}>
                      <td role="cell" data-label="아이디">{request.userId}</td>
                      <td role="cell" data-label="이름">{request.username}</td>
                      <td role="cell" data-label="전공">{request.majorName ?? '-'}</td>
                      <td role="cell" data-label="신청일" className="num">{formatDateTime(request.createdTime)}</td>
                      <td role="cell" data-label="상태">
                        <StatusPill tone={getRequestStatusTone(request.status)}>
                          {getRequestStatusLabel(request.status)}
                        </StatusPill>
                      </td>
                      <td role="cell" data-label="처리 메모" className="truncate" title={request.reviewNote ?? ''}>
                        {request.reviewNote?.trim() || (request.reviewedTime ? `${formatDateTime(request.reviewedTime)} 처리` : '-')}
                      </td>
                      <td role="cell" data-label="처리" className="actions">
                        {request.status === 'pending' ? (
                          <>
                            <button
                              type="button"
                              className="ac-btn ac-btn--sm ac-btn--danger-ghost"
                              onClick={() => { setSignupAction({ request, action: 'reject' }); setSignupNote('') }}
                            >
                              반려
                            </button>{' '}
                            <button
                              type="button"
                              className="ac-btn ac-btn--sm ac-btn--primary"
                              onClick={() => { setSignupAction({ request, action: 'approve' }); setSignupNote('') }}
                            >
                              승인
                            </button>
                          </>
                        ) : (
                          <span className="ac-hint">처리 완료</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}
        </Panel>
      )}

      {tab === 'orgs' && (
        <Panel padded={false} title="학생회 조직 현황" description="등록된 학과 학생회와 담당자입니다.">
          {loading ? (
            <LoadingNote />
          ) : orgError ? (
            <ErrorNote>{orgError}</ErrorNote>
          ) : organizations.length === 0 ? (
            <EmptyState>등록된 조직이 없습니다.</EmptyState>
          ) : (
            <TableScroll mobileCards minWidth={720}>
              <table className="ac-table" role="table">
                <thead role="rowgroup">
                  <tr role="row">
                    <th role="columnheader" scope="col">조직명</th>
                    <th role="columnheader" scope="col">담당자</th>
                    <th role="columnheader" scope="col">대기 요청</th>
                    <th role="columnheader" scope="col">최근 갱신</th>
                    <th role="columnheader" scope="col">상태</th>
                  </tr>
                </thead>
                <tbody role="rowgroup">
                  {organizations.map((org) => (
                    <tr role="row" key={org.id}>
                      <td role="cell" data-label="조직명">{org.name}</td>
                      <td role="cell" data-label="담당자">{org.manager}</td>
                      <td role="cell" data-label="대기 요청" className="num">{org.pendingRequests > 0 ? `${org.pendingRequests}건` : '-'}</td>
                      <td role="cell" data-label="최근 갱신" className="num">{org.updatedAt ? formatDateTime(org.updatedAt) : '-'}</td>
                      <td role="cell" data-label="상태"><StatusPill tone="success">{org.status}</StatusPill></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}
        </Panel>
      )}

      <Modal
        open={roleChange !== null}
        title={roleChange?.field === 'roleId' ? '역할 변경 확인' : '전공 변경 확인'}
        description={
          roleChange?.field === 'roleId' ? (
            <>
              <b>{roleChange?.user.username}</b>({roleChange?.user.userId}) 계정의 역할을{' '}
              <b>{roleChange?.label}</b>(으)로 변경합니다. 역할 변경은 즉시 관리자 기능 접근 권한에 반영됩니다.
            </>
          ) : (
            <>
              <b>{roleChange?.user.username}</b>({roleChange?.user.userId}) 계정의 전공을{' '}
              <b>{roleChange?.label}</b>(으)로 변경합니다. 학과 관리자는 변경된 학과의 콘텐츠만 볼 수 있게 됩니다.
            </>
          )
        }
        confirmLabel="변경"
        busy={busy}
        onCancel={() => setRoleChange(null)}
        onConfirm={applyUserUpdate}
      />

      <Modal
        open={passwordTarget !== null}
        title="비밀번호 재설정"
        description={<><b>{passwordTarget?.userId}</b> 계정에 설정할 새 비밀번호를 입력하세요. 사용자에게 직접 전달해야 합니다.</>}
        confirmLabel="재설정"
        busy={busy}
        confirmDisabled={!passwordValid}
        onCancel={() => { setPasswordTarget(null); setPasswordValue('') }}
        onConfirm={applyPasswordReset}
      >
        <div className="ac-field">
          <label className="ac-label" htmlFor="new-password">새 비밀번호</label>
          <input
            id="new-password"
            type="text"
            className="ac-input"
            value={passwordValue}
            onChange={(event) => setPasswordValue(event.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
          <span className="ac-hint">
            {MIN_PASSWORD}~{MAX_PASSWORD}자 · 현재 {passwordValue.length}자
            {passwordValue.length > 0 && !passwordValid && ' — 길이를 확인해주세요.'}
          </span>
        </div>
      </Modal>

      <Modal
        open={deleteTarget !== null}
        title="계정 삭제"
        description={
          <>
            <b>{deleteTarget?.userId}</b> 계정을 삭제합니다. 삭제된 계정과 대화 기록은 복구할 수 없습니다.
            확인을 위해 아래에 아이디를 그대로 입력하세요.
          </>
        }
        confirmLabel="영구 삭제"
        tone="danger"
        busy={busy}
        confirmDisabled={deleteConfirmText !== deleteTarget?.userId}
        onCancel={() => { setDeleteTarget(null); setDeleteConfirmText('') }}
        onConfirm={applyDelete}
      >
        <div className="ac-field">
          <label className="ac-label" htmlFor="delete-confirm">아이디 확인 입력</label>
          <input
            id="delete-confirm"
            type="text"
            className="ac-input"
            value={deleteConfirmText}
            onChange={(event) => setDeleteConfirmText(event.target.value)}
            placeholder={deleteTarget?.userId}
            autoComplete="off"
          />
        </div>
      </Modal>

      <Modal
        open={signupAction !== null}
        title={signupAction?.action === 'approve' ? '가입 요청 승인' : '가입 요청 반려'}
        description={
          <>
            <b>{signupAction?.request.username}</b>({signupAction?.request.userId} ·{' '}
            {signupAction?.request.majorName ?? '학과 미상'}) 님의 학생회 가입 요청을{' '}
            {signupAction?.action === 'approve' ? '승인' : '반려'}합니다.
          </>
        }
        confirmLabel={signupAction?.action === 'approve' ? '승인' : '반려'}
        tone={signupAction?.action === 'reject' ? 'danger' : 'default'}
        busy={busy}
        confirmDisabled={signupAction?.action === 'reject' && signupNote.trim().length === 0}
        onCancel={() => { setSignupAction(null); setSignupNote('') }}
        onConfirm={applySignupAction}
      >
        <div className="ac-field">
          <label className="ac-label" htmlFor="signup-note">
            처리 메모 {signupAction?.action === 'reject' ? '(필수)' : '(선택)'}
          </label>
          <textarea
            id="signup-note"
            className="ac-textarea"
            value={signupNote}
            onChange={(event) => setSignupNote(event.target.value)}
            placeholder={signupAction?.action === 'approve' ? '예: 학과 사무실 확인 완료' : '예: 학생회 명단에서 확인되지 않습니다.'}
          />
        </div>
      </Modal>
    </>
  )
}

export default UsersPage
