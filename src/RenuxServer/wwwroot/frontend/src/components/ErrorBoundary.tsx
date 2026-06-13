import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
}

/**
 * 앱 전역 에러 경계.
 * 렌더 트리 어디서든 예외가 발생해도 흰 화면 대신 복구 가능한 안내를 보여준다.
 * (향후 Sentry 등 오류 수집 SDK와 연동할 수 있도록 componentDidCatch에 훅 지점을 둔다.)
 */
class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false }

  static getDerivedStateFromError(): State {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // TODO: 오류 수집(Sentry 등) 연동 시 이 지점에서 전송.
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  private handleReload = () => {
    window.location.reload()
  }

  render() {
    if (!this.state.hasError) return this.props.children

    return (
      <div
        role="alert"
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '16px',
          minHeight: '100dvh',
          padding: '24px',
          textAlign: 'center',
        }}
      >
        <h1 style={{ fontSize: '1.25rem', margin: 0 }}>일시적인 오류가 발생했어요</h1>
        <p style={{ color: '#6b7280', margin: 0 }}>
          페이지를 새로고침하면 대부분 해결됩니다. 문제가 계속되면 잠시 후 다시 시도해 주세요.
        </p>
        <button
          type="button"
          onClick={this.handleReload}
          style={{
            padding: '10px 20px',
            borderRadius: '8px',
            border: 'none',
            background: '#1f2937',
            color: '#fff',
            cursor: 'pointer',
          }}
        >
          새로고침
        </button>
      </div>
    )
  }
}

export default ErrorBoundary
