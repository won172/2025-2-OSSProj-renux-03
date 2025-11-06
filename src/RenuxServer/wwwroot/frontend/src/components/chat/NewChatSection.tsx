interface NewChatSectionProps {
  disabled: boolean
  loading: boolean
  hasDepartments: boolean
  onNewChat: () => void
}

const NewChatSection = ({ disabled, loading, hasDepartments, onNewChat }: NewChatSectionProps) => {
  return (
    <section className="sidebar-card">
      <h2>새로운 대화 시작</h2>
      <p className="sidebar-card__desc">학과를 선택해 맞춤형 상담을 시작하세요.</p>
      <button id="new-chat-btn" className="buddy-primary-btn" type="button" disabled={disabled} onClick={onNewChat}>
        새 채팅 만들기
      </button>
      {loading && <p className="secondary-text">학과 정보를 불러오는 중...</p>}
      {!loading && !hasDepartments && <p className="secondary-text">등록된 학과가 없습니다.</p>}
    </section>
  )
}

export default NewChatSection
