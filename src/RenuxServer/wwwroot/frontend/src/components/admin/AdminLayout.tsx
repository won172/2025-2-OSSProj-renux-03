import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import {
  fetchCouncilSignupRequests,
  fetchIdentity,
  fetchPendingItems,
  fetchRagStatus,
  readIdentityMajor,
  readIdentityName,
} from '../../admin/adminApi'
import { getSystemStatusLabel, getSystemStatusTone } from '../../admin/format'
import { mapRoleNameToUserRole } from '../../auth/roleMapping'
import type { RagAdminStatus } from '../../types/admin'
import type { UserRole } from '../../types/auth'
import { AdminConsoleContext, type AdminConsoleValue } from './adminConsoleContext'
import ToastStack, { type ToastMessage, type ToastTone } from './Toast'
import '../../styles/admin-console.css'

/** 요약 지표 자동 갱신 주기 — 대기 건수가 오래 묵지 않을 만큼 자주, 서버 부담은 적게. */
const SUMMARY_POLL_MS = 30_000

interface NavItem {
  to: string
  label: string
  /** 대학 수준 전용 메뉴 여부 */
  universityOnly: boolean
  badge?: 'review' | 'signup'
}

const NAV_ITEMS: NavItem[] = [
  { to: '/admin/dashboard', label: '대시보드', universityOnly: true },
  { to: '/admin/review', label: '검수함', universityOnly: true, badge: 'review' },
  { to: '/admin/content', label: '콘텐츠', universityOnly: true },
  { to: '/admin/logs', label: '대화 로그', universityOnly: true },
  { to: '/admin/feedback', label: '피드백', universityOnly: true },
  { to: '/admin/users', label: '사용자·조직', universityOnly: true, badge: 'signup' },
  { to: '/admin/system', label: '시스템', universityOnly: true },
]

const DEPARTMENT_NAV: NavItem = { to: '/admin/department', label: '학과 콘솔', universityOnly: false }

const ROLE_LABEL: Record<UserRole, string> = {
  STUDENT: '학생',
  DEPARTMENT_COUNCIL: '학생회',
  UNIVERSITY_COUNCIL: '총학생회·관리자',
}

/**
 * 관리자 콘솔의 껍데기 — 사이드바 내비게이션과 글로벌 헤더.
 * 대기 건수·시스템 상태를 여기서 한 번만 읽어 모든 화면이 공유하므로,
 * 어느 화면에 있든 "처리할 일"과 "이상 여부"가 보인다.
 */
const AdminLayout = () => {
  const navigate = useNavigate()
  const location = useLocation()

  const [role, setRole] = useState<UserRole | null>(null)
  const [identityResolved, setIdentityResolved] = useState(false)
  const [userName, setUserName] = useState('')
  const [majorName, setMajorName] = useState('')

  const [pendingReviewCount, setPendingReviewCount] = useState(0)
  const [pendingSignupCount, setPendingSignupCount] = useState(0)
  const [ragStatus, setRagStatus] = useState<RagAdminStatus | null>(null)
  const [ragStatusError, setRagStatusError] = useState<string | null>(null)
  const [lastRefreshedAt, setLastRefreshedAt] = useState<Date | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [dataVersion, setDataVersion] = useState(0)

  const [toasts, setToasts] = useState<ToastMessage[]>([])
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [bannerDismissed, setBannerDismissed] = useState(false)

  const toastSeq = useRef(0)

  const showToast = useCallback((
    text: string,
    tone: ToastTone = 'success',
    options: Partial<Omit<ToastMessage, 'id' | 'text' | 'tone'>> = {},
  ) => {
    toastSeq.current += 1
    const id = `toast-${toastSeq.current}`
    setToasts((prev) => [...prev, { id, text, tone, ...options }])
    return id
  }, [])

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id))
  }, [])

  // 역할 확인 — localStorage의 낡은 값이 아니라 서버 응답을 신뢰한다.
  useEffect(() => {
    let cancelled = false
    const resolve = async () => {
      try {
        const identity = await fetchIdentity()
        if (cancelled) return
        const resolved = mapRoleNameToUserRole(identity?.roleName || identity?.role)
        setRole(resolved)
        setUserName(readIdentityName(identity))
        setMajorName(readIdentityMajor(identity))
        try {
          window.localStorage.setItem('renux-user-role', resolved)
        } catch {
          // 저장소가 차단되어도 서버에서 확인한 역할은 그대로 사용한다.
        }
      } catch {
        if (!cancelled) setRole('STUDENT')
      } finally {
        if (!cancelled) setIdentityResolved(true)
      }
    }
    resolve()
    return () => { cancelled = true }
  }, [])

  const isUniversityLevel = role === 'UNIVERSITY_COUNCIL'

  const refreshSummary = useCallback(async () => {
    if (!isUniversityLevel) return
    setRefreshing(true)
    const [pendingResult, signupResult, statusResult] = await Promise.allSettled([
      fetchPendingItems(),
      fetchCouncilSignupRequests(),
      fetchRagStatus(),
    ])

    if (pendingResult.status === 'fulfilled' && Array.isArray(pendingResult.value)) {
      setPendingReviewCount(pendingResult.value.filter((item) => item.status === 'pending').length)
    }
    if (signupResult.status === 'fulfilled' && Array.isArray(signupResult.value)) {
      setPendingSignupCount(signupResult.value.filter((request) => request.status === 'pending').length)
    }
    if (statusResult.status === 'fulfilled') {
      setRagStatus(statusResult.value)
      setRagStatusError(null)
    } else {
      setRagStatus(null)
      setRagStatusError('RAG 운영 상태를 불러오지 못했습니다.')
    }

    setLastRefreshedAt(new Date())
    setRefreshing(false)
  }, [isUniversityLevel])

  const notifyDataChanged = useCallback(() => {
    setDataVersion((version) => version + 1)
    void refreshSummary()
  }, [refreshSummary])

  useEffect(() => {
    if (!isUniversityLevel) return
    void refreshSummary()
    const timer = setInterval(() => { void refreshSummary() }, SUMMARY_POLL_MS)
    return () => clearInterval(timer)
  }, [isUniversityLevel, refreshSummary])

  // 경로가 바뀌면 모바일 내비를 닫고, 새 화면의 경고를 다시 볼 수 있게 한다.
  useEffect(() => {
    setMobileNavOpen(false)
  }, [location.pathname])

  const handleLogout = async () => {
    try {
      await apiFetch('/auth/signout', { method: 'POST' })
      window.localStorage.removeItem('renux-user-role')
      navigate('/')
    } catch {
      showToast('로그아웃에 실패했습니다. 다시 시도해주세요.', 'error')
    }
  }

  const visibleNavItems = useMemo(() => {
    const items = isUniversityLevel ? [...NAV_ITEMS] : []
    if (role === 'DEPARTMENT_COUNCIL' || isUniversityLevel) {
      items.push(DEPARTMENT_NAV)
    }
    return items
  }, [isUniversityLevel, role])

  const contextValue = useMemo<AdminConsoleValue>(() => ({
    role: role ?? 'STUDENT',
    userName,
    majorName,
    isUniversityLevel,
    pendingReviewCount,
    pendingSignupCount,
    ragStatus,
    ragStatusError,
    lastRefreshedAt,
    refreshing,
    refreshSummary,
    notifyDataChanged,
    dataVersion,
    showToast,
    dismissToast,
  }), [
    role, userName, majorName, isUniversityLevel,
    pendingReviewCount, pendingSignupCount,
    ragStatus, ragStatusError, lastRefreshedAt, refreshing,
    refreshSummary, notifyDataChanged, dataVersion, showToast, dismissToast,
  ])

  if (!identityResolved) {
    return <div className="ac-boot" role="status">권한을 확인하는 중입니다...</div>
  }

  if (role === 'STUDENT') {
    return (
      <div className="ac-denied">
        <h2>접근 권한이 없습니다</h2>
        <p>이 페이지는 관리자 계정으로 로그인해야 이용할 수 있습니다.</p>
        <Link to="/" className="ac-btn ac-btn--primary">홈으로 돌아가기</Link>
      </div>
    )
  }

  const systemStatus = ragStatus?.status ?? (ragStatusError ? 'error' : undefined)
  const statusTone = getSystemStatusTone(systemStatus)
  const degradedDatasets = (ragStatus?.datasets ?? []).filter((dataset) => dataset.status !== 'ok')
  const ingestionFailed = (ragStatus?.notices_ingestion?.ingestion_summary.documents_failed ?? 0) > 0
  const showBanner = !bannerDismissed && isUniversityLevel && (statusTone === 'warning' || statusTone === 'danger')

  const badgeCount = (badge?: NavItem['badge']) => {
    if (badge === 'review') return pendingReviewCount
    if (badge === 'signup') return pendingSignupCount
    return 0
  }

  return (
    <AdminConsoleContext.Provider value={contextValue}>
      <div className={`ac-shell ${mobileNavOpen ? 'ac-shell--nav-open' : ''}`}>
        <aside className="ac-sidebar" aria-label="관리자 메뉴">
          <div className="ac-sidebar__brand">
            <span className="ac-sidebar__brand-mark" aria-hidden="true">동</span>
            <span>
              동똑이 <b>Admin</b>
            </span>
          </div>
          <nav className="ac-nav">
            {visibleNavItems.map((item) => {
              const count = badgeCount(item.badge)
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => `ac-nav__item ${isActive ? 'ac-nav__item--active' : ''}`}
                >
                  <span>{item.label}</span>
                  {count > 0 && <span className="ac-nav__badge" aria-label={`대기 ${count}건`}>{count}</span>}
                </NavLink>
              )
            })}
          </nav>
          <div className="ac-sidebar__footer">
            <Link to="/" className="ac-nav__item ac-nav__item--muted">← 챗봇으로 돌아가기</Link>
          </div>
        </aside>

        <div className="ac-main">
          <header className="ac-topbar">
            <button
              type="button"
              className="ac-topbar__menu"
              aria-label="메뉴 열기"
              aria-expanded={mobileNavOpen}
              onClick={() => setMobileNavOpen((open) => !open)}
            >
              ☰
            </button>

            <div className="ac-topbar__meta">
              {isUniversityLevel && (
                <button
                  type="button"
                  className={`ac-status ac-status--${statusTone}`}
                  onClick={() => navigate('/admin/system')}
                  title="시스템 상태 자세히 보기"
                >
                  <span className="ac-status__dot" aria-hidden="true" />
                  {getSystemStatusLabel(systemStatus)}
                </button>
              )}
              {lastRefreshedAt && (
                <span className="ac-topbar__time">
                  {lastRefreshedAt.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })} 갱신
                </span>
              )}
              {isUniversityLevel && (
                <button
                  type="button"
                  className="ac-btn ac-btn--icon"
                  onClick={() => { void refreshSummary() }}
                  disabled={refreshing}
                  aria-label="지표 새로고침"
                  title="새로고침"
                >
                  {refreshing ? '…' : '↻'}
                </button>
              )}
              <div className="ac-account">
                <span className="ac-account__name">{userName || '관리자'}</span>
                <span className="ac-account__role">
                  {ROLE_LABEL[role ?? 'STUDENT']}{majorName ? ` · ${majorName}` : ''}
                </span>
              </div>
              <button type="button" className="ac-btn ac-btn--ghost" onClick={handleLogout}>로그아웃</button>
            </div>
          </header>

          {showBanner && (
            <div className={`ac-banner ac-banner--${statusTone}`} role="alert">
              <span className="ac-banner__text">
                {ragStatusError
                  ? ragStatusError
                  : degradedDatasets.length > 0
                    ? `${degradedDatasets.map((dataset) => dataset.key).join(', ')} 인덱스 ${getSystemStatusLabel(statusTone === 'danger' ? 'error' : 'degraded')} — ${degradedDatasets[0].error ?? '상태를 확인해주세요.'}`
                    : ingestionFailed
                      ? `공지 수집에서 ${ragStatus?.notices_ingestion?.ingestion_summary.documents_failed}건이 실패했습니다.`
                      : '시스템 상태에 주의가 필요합니다.'}
              </span>
              <div className="ac-banner__actions">
                <Link to="/admin/system" className="ac-btn ac-btn--sm">시스템에서 확인</Link>
                <button
                  type="button"
                  className="ac-btn ac-btn--sm ac-btn--ghost"
                  onClick={() => setBannerDismissed(true)}
                  aria-label="경고 숨기기"
                >
                  닫기
                </button>
              </div>
            </div>
          )}

          <main className="ac-content">
            <Outlet />
          </main>
        </div>

        {mobileNavOpen && (
          <button
            type="button"
            className="ac-nav-scrim"
            aria-label="메뉴 닫기"
            onClick={() => setMobileNavOpen(false)}
          />
        )}

        <nav className="ac-tabbar" aria-label="빠른 이동">
          {visibleNavItems.slice(0, 4).map((item) => {
            const count = badgeCount(item.badge)
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) => `ac-tabbar__item ${isActive ? 'ac-tabbar__item--active' : ''}`}
              >
                {item.label}
                {count > 0 && <span className="ac-tabbar__badge">{count}</span>}
              </NavLink>
            )
          })}
        </nav>
      </div>

      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </AdminConsoleContext.Provider>
  )
}

export default AdminLayout
