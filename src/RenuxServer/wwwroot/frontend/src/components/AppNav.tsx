import { Link, useLocation } from 'react-router-dom'
import { appNavItems } from './appNavItems'

/**
 * 모바일은 하단 탭(tabs), 넓은 화면은 사이드바 상단 목록으로 보인다. 표시 여부는 CSS가 정한다.
 * 채팅 탭은 홈('/')과 개별 대화('/chat/:id') 모두에서 활성이다.
 * 대화 중 채팅 탭을 다시 누르면 새 대화('/')로 가지만, 답변 스트리밍 중(busy)에는 아무것도 하지 않는다.
 */
const AppNav = ({ tabs, calendar, busy }: { tabs?: boolean; calendar?: boolean; busy?: boolean }) => {
  const path = useLocation().pathname
  return (
    <nav className={tabs ? 'app-nav app-tabs' : 'app-nav app-side'} aria-label="주요 메뉴">
      {appNavItems(calendar).map(([to, label, d]) => {
        const active = to === '/' ? path === '/' || path.startsWith('/chat/') : path.startsWith(to)
        // 현재 탭을 다시 눌러도 화면을 바꾸지 않는다. 예외: 대화 화면의 채팅 탭은 새 대화로 간다.
        const stay = active && (to !== '/' || path === '/' || Boolean(busy))
        return (
          <Link key={to} to={to} aria-current={active ? 'page' : undefined} onClick={stay ? (event) => event.preventDefault() : undefined}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d={d} /></svg>
            {label}
          </Link>
        )
      })}
    </nav>
  )
}

export default AppNav
