interface AppHeaderProps {
  isAuthenticated: boolean
  welcomeMessage: string
  onLogin: () => void
  onSignup: () => void
  onLogout: () => void
  onOpenSettings: () => void
  onToggleSidebar: () => void
  isSidebarOpen: boolean
}

const AppHeader = ({
  isAuthenticated,
  welcomeMessage,
  onLogin,
  onSignup,
  onLogout,
  onOpenSettings,
  onToggleSidebar,
  isSidebarOpen,
}: AppHeaderProps) => {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <button
          type="button"
          className="app-header__icon-btn app-header__icon-btn--menu"
          onClick={onToggleSidebar}
          aria-label="채팅 목록 토글"
          aria-expanded={isSidebarOpen}
        >
          <span className="hamburger-btn__bar" />
          <span className="hamburger-btn__bar" />
          <span className="hamburger-btn__bar" />
        </button>
        <div>
          <p className="chatbot-hero__badge" aria-hidden="true">
            Dongguk Buddy
          </p>
          <h1 style={{ margin: '8px 0 0', fontSize: '1.35rem', letterSpacing: '0.04em' }}>동국대학교 AI 챗봇</h1>
        </div>
      </div>
      <nav className="app-header__actions">
        {isAuthenticated ? (
          <>
            <span className="app-header__welcome">{welcomeMessage}</span>
            <button id="logout-btn" className="auth-btn" type="button" onClick={onLogout}>
              로그아웃
            </button>
          </>
        ) : (
          <>
            <button id="login-btn" className="auth-btn" type="button" onClick={onLogin}>
              로그인
            </button>
            <button id="signup-btn" className="auth-btn" type="button" onClick={onSignup}>
              회원가입
            </button>
          </>
        )}
        <button
          id="settings-btn"
          className="app-header__icon-btn"
          type="button"
          onClick={onOpenSettings}
          aria-label="환경설정"
        >
          <span className="settings-btn__icon" aria-hidden="true">⚙️</span>
        </button>
      </nav>
    </header>
  )
}

export default AppHeader
