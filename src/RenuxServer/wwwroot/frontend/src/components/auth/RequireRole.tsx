import { type ReactNode, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import type { UserRole } from '../../types/auth'

const mapRoleNameToUserRole = (roleName?: string | null): UserRole => {
  if (!roleName) return 'STUDENT'
  const normalized = roleName.trim().toLowerCase()
  if (normalized.includes('관리자')) return 'UNIVERSITY_COUNCIL'
  if (normalized.includes('학생회')) return 'DEPARTMENT_COUNCIL'
  return 'STUDENT'
}

const readStoredRole = (): UserRole | null => {
  if (typeof window === 'undefined') return null
  const stored = window.localStorage.getItem('renux-user-role')
  if (stored === 'STUDENT' || stored === 'DEPARTMENT_COUNCIL' || stored === 'UNIVERSITY_COUNCIL') {
    return stored
  }
  return null
}

interface RequireRoleProps {
  allow: UserRole[]
  children: ReactNode
}

/**
 * 관리자 라우트의 클라이언트 측 가드. 서버 API 인가가 1차 방어선이지만,
 * 권한 없는 사용자에게 관리자 UI가 노출되는 것을 막는다.
 * localStorage 역할을 우선 사용하고, 없으면 /auth/name으로 1회 조회한다.
 */
const RequireRole = ({ allow, children }: RequireRoleProps) => {
  const [role, setRole] = useState<UserRole | null>(() => readStoredRole())
  const [status, setStatus] = useState<'pending' | 'resolved'>(() =>
    readStoredRole() ? 'resolved' : 'pending',
  )

  useEffect(() => {
    if (status === 'resolved') return

    let cancelled = false
    const resolveRole = async () => {
      try {
        const data = await apiFetch<{ roleName?: string; role?: string }>('/auth/name', { method: 'GET' })
        const resolved = mapRoleNameToUserRole(data?.roleName || data?.role)
        if (!cancelled) {
          window.localStorage.setItem('renux-user-role', resolved)
          setRole(resolved)
        }
      } catch {
        if (!cancelled) setRole('STUDENT')
      } finally {
        if (!cancelled) setStatus('resolved')
      }
    }

    resolveRole()
    return () => {
      cancelled = true
    }
  }, [status])

  if (status === 'pending') {
    return <div className="app-shell" style={{ padding: '2rem' }}>권한을 확인하는 중입니다...</div>
  }

  if (!role || !allow.includes(role)) {
    // 무언 리다이렉트 대신 이유를 안내 — 사용자가 왜 이동했는지 알 수 있도록
    return (
      <div className="app-shell" style={{ padding: '2rem', textAlign: 'center' }}>
        <h2 style={{ marginBottom: '0.5rem' }}>접근 권한이 없습니다</h2>
        <p style={{ marginBottom: '1.5rem', color: '#666' }}>
          이 페이지는 관리자 계정으로 로그인해야 이용할 수 있습니다.
        </p>
        <Link to="/" className="buddy-secondary-btn" style={{ textDecoration: 'none' }}>
          홈으로 돌아가기
        </Link>
      </div>
    )
  }

  return <>{children}</>
}

export default RequireRole
