/** 빌드 시점 플래그. false로 바꾸면 캘린더 탭과 경로를 모두 숨긴다. */
export const CALENDAR_ENABLED = true

// [경로, 이름, 22px 선 아이콘 path] — PWA 사전 캐시 예산 때문에 아이콘 라이브러리 대신 인라인 SVG를 쓴다.
const ITEMS = [
  ['/', '홈', 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z'],
  ['/calendar', '캘린더', 'M4 5h16v15H4zM4 10h16M8 3v4M16 3v4'],
  ['/chat', '채팅', 'M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z'],
  ['/settings', '설정', 'M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4'],
] as const

export const appNavItems = (calendar: boolean = CALENDAR_ENABLED) =>
  calendar ? ITEMS : ITEMS.filter(([to]) => to !== '/calendar')

/** 탭이 현재 경로의 탭인지. 홈은 '/'에서만, 나머지는 하위 경로까지 포함한다. */
export const isNavItemActive = (to: string, path: string) =>
  to === '/' ? path === '/' : path === to || path.startsWith(`${to}/`)
