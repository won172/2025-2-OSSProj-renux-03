import { Link } from 'react-router-dom'
import type { ActiveChat } from '../../types/chat'
//부모에서 받는 props: chats(배열), errorMessage(선택), isAuthenticated |
interface ActiveChatListProps {
  chats: ActiveChat[]
  errorMessage?: string | null
  isAuthenticated: boolean
}
//렌더링 여부 결정. 채팅 목록이 있거나 에러가 있거나 로그인 상태면 섹션을 표시하고, 아니면 null 반환 |
const ActiveChatList = ({ chats, errorMessage, isAuthenticated }: ActiveChatListProps) => {
  const shouldShowSection = chats.length > 0 || Boolean(errorMessage) || isAuthenticated

  if (!shouldShowSection) {
    return null
  }

  return (
    <section className="sidebar-card">
      <h2>최근 대화</h2>
      <p className="sidebar-card__desc">이전에 나눈 대화를 이어서 진행할 수 있어요.</p>
      <ul className="active-chat-list">
        {chats.map((chat) => {
          const title = chat.title
          return (
            <li key={chat.id}>
              <Link className="active-chat-item" to={`/chat/${chat.id}`}>
                <span className="chat-preview-card__title">{title}</span>
                <span className="chat-preview-card__subtitle">다시 이어서 대화하기</span>
              </Link>
            </li>
          )
        })}
      </ul>
      {errorMessage && <p className="error-text">{errorMessage}</p>}
      {isAuthenticated && !errorMessage && chats.length === 0 && (
        <p className="secondary-text">최근 채팅이 없습니다.</p>
      )}
    </section>
  )
}

export default ActiveChatList
