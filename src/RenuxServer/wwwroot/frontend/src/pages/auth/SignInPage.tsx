import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch, type ApiError } from '../../api/client'

const SignInPage = () => {
  const [userId, setUserId] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError(null)

    if (!userId || !password) {
      setError('아이디와 비밀번호를 모두 입력해주세요.')
      return
    }

    try {
      setIsSubmitting(true)
      await apiFetch('/auth/signin', {
        method: 'POST',
        json: { userId, password },
      })
      window.location.href = '/'
    } catch (submitError) {
      console.error('로그인 실패:', submitError)
      if (submitError && typeof submitError === 'object' && 'status' in submitError) {
        const apiError = submitError as ApiError
        if (apiError.status === 401 || apiError.status === 400) {
          setError('아이디 또는 비밀번호가 올바르지 않습니다.')
        } else {
          setError(apiError.message ?? '로그인 중 오류가 발생했습니다.')
        }
      } else {
        setError(submitError instanceof Error ? submitError.message : '로그인 중 오류가 발생했습니다.')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-container">
        <h2>로그인</h2>
        <form className="auth-form" onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="userId">아이디</label>
            <input
              id="userId"
              name="userId"
              type="text"
              autoComplete="username"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              disabled={isSubmitting}
              required
            />
          </div>
          <div className="form-group">
            <label htmlFor="password">비밀번호</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={isSubmitting}
              required
            />
          </div>
          {error && <div className="auth-error">{error}</div>}
          <button className="auth-submit" type="submit" disabled={isSubmitting}>
            {isSubmitting ? '로그인 중...' : '로그인'}
          </button>
        </form>
        <div className="auth-footer">
          <p>
            계정이 없으신가요? <Link to="/auth/up">회원가입</Link>
          </p>
        </div>
      </div>
    </div>
  )
}

export default SignInPage
