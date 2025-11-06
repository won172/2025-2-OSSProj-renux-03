import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch, type ApiError } from '../../api/client'
import type { MajorOption, RoleOption, ApiMessageResponse } from '../../types/user'

type IdStatus = 'available' | 'unavailable' | null

type PasswordStatus = 'match' | 'mismatch' | null

const SignUpPage = () => {
  const [userId, setUserId] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [username, setUsername] = useState('')
  const [majors, setMajors] = useState<MajorOption[]>([])
  const [roles, setRoles] = useState<RoleOption[]>([])
  const [selectedMajorId, setSelectedMajorId] = useState('')
  const [selectedRoleId, setSelectedRoleId] = useState('')
  const [idStatus, setIdStatus] = useState<IdStatus>(null)
  const [idMessage, setIdMessage] = useState('')
  const [isCheckingId, setIsCheckingId] = useState(false)
  const [isIdAvailable, setIsIdAvailable] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  useEffect(() => {
    const loadMajors = async () => {
      try {
        const data = await apiFetch<MajorOption[]>('/req/major', { method: 'GET' })
        if (Array.isArray(data)) {
          setMajors(data)
        }
      } catch (error) {
        console.error('전공 데이터 로드 실패', error)
        setMajors([])
      }
    }

    const loadRoles = async () => {
      try {
        const data = await apiFetch<RoleOption[]>('/req/role', { method: 'GET' })
        if (Array.isArray(data)) {
          setRoles(data)
        }
      } catch (error) {
        console.error('역할 데이터 로드 실패', error)
        setRoles([])
      }
    }

    loadMajors()
    loadRoles()
  }, [])

  useEffect(() => {
    setIsIdAvailable(false)
    setIdStatus(null)
    setIdMessage('')
  }, [userId])

  const passwordStatus = useMemo<PasswordStatus>(() => {
    if (!password && !passwordConfirm) return null
    return password === passwordConfirm ? 'match' : 'mismatch'
  }, [password, passwordConfirm])

  const passwordMessage = useMemo(() => {
    if (!passwordStatus) return ''
    return passwordStatus === 'match' ? '비밀번호가 일치합니다.' : '비밀번호가 일치하지 않습니다.'
  }, [passwordStatus])

  const findMajorById = (value: string) =>
    majors.find((major) => String(major.id ?? major.majorId ?? '') === value)

  const findRoleById = (value: string) => roles.find((role) => String(role.id ?? role.roleId ?? '') === value)

  const handleIdBlur = async () => {
    if (!userId) {
      setIdStatus(null)
      setIdMessage('')
      setIsIdAvailable(false)
      return
    }

    try {
      setIsCheckingId(true)
      const isDuplicate = await apiFetch<boolean>('/auth/idcheck', {
        method: 'POST',
        json: { id: userId },
      })
      if (isDuplicate) {
        setIdStatus('unavailable')
        setIdMessage('이미 사용 중인 아이디입니다.')
        setIsIdAvailable(false)
      } else {
        setIdStatus('available')
        setIdMessage('사용 가능한 아이디입니다.')
        setIsIdAvailable(true)
      }
    } catch (error) {
      console.error('ID check error:', error)
      setIdStatus('unavailable')
      setIdMessage('아이디 확인 중 오류가 발생했습니다.')
      setIsIdAvailable(false)
    } finally {
      setIsCheckingId(false)
    }
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSubmitError(null)

    if (!isIdAvailable) {
      setSubmitError('아이디 중복 확인을 완료해주세요.')
      return
    }

    if (passwordStatus !== 'match') {
      setSubmitError('비밀번호를 다시 확인해주세요.')
      return
    }

    const selectedMajor = findMajorById(selectedMajorId)
    const selectedRole = findRoleById(selectedRoleId)

    if (!selectedMajor || !selectedRole || !username) {
      setSubmitError('모든 정보를 입력 및 선택해주세요.')
      return
    }

    try {
      setIsSubmitting(true)
      await apiFetch('/auth/signup', {
        method: 'POST',
        json: {
          userId,
          password,
          username,
          majorId: selectedMajor.id ?? selectedMajor.majorId,
          roleId: selectedRole.id ?? selectedRole.roleId,
        },
      })
      window.alert('회원가입이 성공적으로 완료되었습니다!')
      window.location.href = '/auth/in'
    } catch (submitError) {
      console.error('회원가입 실패:', submitError)
      if (submitError && typeof submitError === 'object' && 'status' in submitError) {
        const apiError = submitError as ApiError
        const messageFromDetails =
          typeof apiError.details === 'object' && apiError.details && 'message' in apiError.details
            ? String((apiError.details as ApiMessageResponse).message)
            : undefined
        setSubmitError(messageFromDetails ?? apiError.message ?? '회원가입 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.')
      } else {
        setSubmitError(
          submitError instanceof Error ? submitError.message : '회원가입 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.',
        )
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-container">
        <h2>회원가입</h2>
        <form className="auth-form" onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="userId">아이디</label>
            <input
              id="userId"
              name="userId"
              type="text"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              onBlur={handleIdBlur}
              autoComplete="off"
              disabled={isSubmitting}
              required
            />
            <span className={`auth-field-message${idStatus ? ` ${idStatus}` : ''}`}>{idMessage}</span>
          </div>

          <div className="form-group">
            <label htmlFor="password">비밀번호</label>
            <input
              id="password"
              name="password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              disabled={isSubmitting}
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="password-confirm">비밀번호 확인</label>
            <input
              id="password-confirm"
              name="password-confirm"
              type="password"
              value={passwordConfirm}
              onChange={(event) => setPasswordConfirm(event.target.value)}
              autoComplete="new-password"
              disabled={isSubmitting}
              required
            />
            {passwordStatus && (
              <span className={`auth-field-message ${passwordStatus}`}>{passwordMessage}</span>
            )}
          </div>

          <div className="form-group">
            <label htmlFor="username">이름</label>
            <input
              id="username"
              name="username"
              type="text"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="name"
              disabled={isSubmitting}
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="major">전공</label>
            <select
              id="major"
              name="major"
              value={selectedMajorId}
              onChange={(event) => setSelectedMajorId(event.target.value)}
              disabled={isSubmitting || majors.length === 0}
              required
            >
              <option value="">전공 선택</option>
              {majors.map((major, index) => {
                const value = String(major.id ?? major.majorId ?? '')
                const key = value || major.majorname || `major-${index}`
                return (
                  <option key={key} value={value}>
                    {major.majorname ?? '알 수 없는 전공'}
                  </option>
                )
              })}
            </select>
          </div>

          <div className="form-group">
            <label htmlFor="role">역할</label>
            <select
              id="role"
              name="role"
              value={selectedRoleId}
              onChange={(event) => setSelectedRoleId(event.target.value)}
              disabled={isSubmitting || roles.length === 0}
              required
            >
              <option value="">역할 선택</option>
              {roles.map((role, index) => {
                const value = String(role.id ?? role.roleId ?? '')
                const key = value || role.rolename || `role-${index}`
                return (
                  <option key={key} value={value}>
                    {role.rolename ?? '알 수 없는 역할'}
                  </option>
                )
              })}
            </select>
          </div>

          {submitError && <div className="auth-error">{submitError}</div>}

          <button className="auth-submit" type="submit" disabled={isSubmitting || isCheckingId}>
            {isSubmitting ? '가입 중...' : '가입하기'}
          </button>
        </form>

        <div className="auth-footer">
          <p>
            이미 계정이 있으신가요? <Link to="/auth/in">로그인</Link>
          </p>
        </div>
      </div>
    </div>
  )
}

export default SignUpPage
