import { useNavigate } from 'react-router-dom'

const SettingsPage = () => {
  const navigate = useNavigate()

  return (
    <div className="settings-page">
      <header className="settings-page__header">
        <h1>환경설정</h1>
        <button type="button" className="settings-page__back" onClick={() => navigate(-1)}>
          돌아가기
        </button>
      </header>
      <section className="settings-page__content">
        <p>환경설정 항목을 준비 중입니다. 필요한 옵션을 여기에 추가하세요.</p>
      </section>
    </div>
  )
}

export default SettingsPage
