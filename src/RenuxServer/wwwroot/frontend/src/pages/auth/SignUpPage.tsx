import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiFetch, type ApiError } from '../../api/client'
import type { MajorOption, ApiMessageResponse } from '../../types/user'

type IdStatus = 'available' | 'unavailable' | null

type PasswordStatus = 'match' | 'mismatch' | null
type SignupMode = 'student' | 'council'

const councilInstagramUrl =
  'https://www.instagram.com/dongttok.dgu?igsh=MWs3MWJ4OWU3NjdlMw%3D%3D&utm_source=qr'

const SignUpPage = () => {
  const navigate = useNavigate()
  const [userId, setUserId] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [username, setUsername] = useState('')
  const [majors, setMajors] = useState<MajorOption[]>([])
  const [selectedMajorId, setSelectedMajorId] = useState('')
  const [idStatus, setIdStatus] = useState<IdStatus>(null)
  const [idMessage, setIdMessage] = useState('')
  const [isCheckingId, setIsCheckingId] = useState(false)
  const [isIdAvailable, setIsIdAvailable] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [signupMode, setSignupMode] = useState<SignupMode>('student')
  const [requestSuccess, setRequestSuccess] = useState<string | null>(null)

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

    loadMajors()
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
    setRequestSuccess(null)

    if (!isIdAvailable) {
      setSubmitError('아이디 중복 확인을 완료해주세요.')
      return
    }

    if (passwordStatus !== 'match') {
      setSubmitError('비밀번호를 다시 확인해주세요.')
      return
    }

    const selectedMajor = findMajorById(selectedMajorId)

    if (!selectedMajor || !username) {
      setSubmitError('모든 정보를 입력 및 선택해주세요.')
      return
    }

    try {
      setIsSubmitting(true)
      const endpoint = signupMode === 'council' ? '/auth/council-signup-requests' : '/auth/signup'
      await apiFetch<{ message?: string; instagramUrl?: string } | boolean>(endpoint, {
        method: 'POST',
        json: {
          userId,
          password,
          username,
          majorId: selectedMajor.id ?? selectedMajor.majorId,
        },
      })
      if (signupMode === 'council') {
        setRequestSuccess('학생회 가입 요청이 접수되었습니다. 확인을 위해 동똑이 인스타그램으로 DM을 보내주세요.')
        setPassword('')
        setPasswordConfirm('')
        return
      }
      // alert() 대신 로그인 페이지로 이동하며 성공 상태를 전달 (블로킹 다이얼로그 제거)
      navigate('/auth/in', { state: { signupSuccess: true } })
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
        <div className="auth-mode-tabs" role="tablist" aria-label="가입 유형">
          <button
            type="button"
            className={`auth-mode-tab ${signupMode === 'student' ? 'auth-mode-tab--active' : ''}`}
            onClick={() => setSignupMode('student')}
            aria-pressed={signupMode === 'student'}
          >
            일반학생 가입
          </button>
          <button
            type="button"
            className={`auth-mode-tab ${signupMode === 'council' ? 'auth-mode-tab--active' : ''}`}
            onClick={() => setSignupMode('council')}
            aria-pressed={signupMode === 'council'}
          >
            학생회 가입 요청
          </button>
        </div>
        <p className="auth-help-text">
          관리자 계정은 공개 회원가입으로 만들 수 없으며, 운영자가 별도로 아이디와 비밀번호를 발급합니다.
        </p>
        {signupMode === 'council' && (
          <div className="auth-info-box" role="status">
            학생회 계정은 요청 접수 후 확인 절차를 거쳐 승인됩니다. 요청 후 동똑이 인스타그램으로 DM을 보내주세요.
            <br />
            <a href={councilInstagramUrl} target="_blank" rel="noopener noreferrer">
              @dongttok.dgu 인스타그램으로 DM 보내기
            </a>
          </div>
        )}
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

          {submitError && <div className="auth-error">{submitError}</div>}
          {requestSuccess && (
            <div className="auth-success">
              {requestSuccess}
              <br />
              <a href={councilInstagramUrl} target="_blank" rel="noopener noreferrer">
                @dongttok.dgu 인스타그램으로 DM 보내기
              </a>
            </div>
          )}

          <button className="auth-submit" type="submit" disabled={isSubmitting || isCheckingId}>
            {isSubmitting ? '처리 중...' : signupMode === 'council' ? '학생회 가입 요청 보내기' : '가입하기'}
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
