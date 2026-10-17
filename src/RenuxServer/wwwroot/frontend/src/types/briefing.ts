/** 홈 브리핑 위젯이 쓰는 '오늘' 요약. 서버가 RAG 데이터셋에서 모아 내려준다. */

export interface BriefingMeal {
  corner: string
  menu: string
}

export interface BriefingSchedule {
  title: string
  period: string
}

export interface BriefingNotice {
  title: string
  url?: string | null
  publishedAt?: string | null
  /** 게시판 이름(예: 장학공지). 이전 서버 응답에는 없다. */
  board?: string | null
}

/** 오늘 운영하는 식당별 코너. 홈의 식당 선택에 쓴다. 이전 서버 응답에는 없다. */
export interface BriefingRestaurant {
  name: string
  corners: BriefingMeal[]
}

export interface HomeBriefing {
  generatedAt?: string | null
  meals: BriefingMeal[]
  restaurants?: BriefingRestaurant[]
  schedules: BriefingSchedule[]
  notices: BriefingNotice[]
}
