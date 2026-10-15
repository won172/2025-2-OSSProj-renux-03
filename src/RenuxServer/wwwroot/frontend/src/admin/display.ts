import type { StatusTone } from './format'

const datasetLabels: Record<string, string> = {
  notices: '공지사항', rules: '학칙·규정', schedule: '학사일정', courses: '교과목',
  staff: '교직원 연락처', meals: '학식', custom_knowledge: '학과 FAQ', event: '행사', announcement: '학과 공지',
}
export const datasetLabel = (key: string) => datasetLabels[key] ?? key

export const routeLabel = (route?: string | null): string => {
  if (!route) return '분류 없음'
  let values: unknown = route
  try { values = JSON.parse(route) } catch { /* Older logs contain a plain dataset key. */ }
  if (Array.isArray(values) && values.every((value) => typeof value === 'string')) {
    return values.length ? values.map(datasetLabel).join(' · ') : '분류 없음'
  }
  return typeof values === 'string' ? datasetLabel(values) : '분류 상세 확인'
}

export const jobStatus = (status?: string | null): { label: string; tone: StatusTone; hint?: string } => {
  if (!status) return { label: '기록 없음', tone: 'neutral' }
  if (['ok', 'success', 'completed', 'succeeded'].includes(status)) return { label: '완료', tone: 'success' }
  if (status === 'partial') return { label: '처리 확인 필요', tone: 'warning', hint: '일부 미완료 또는 검수 대기 상태일 수 있습니다. 실행 메시지와 수집 결과를 확인하세요.' }
  if (['failed', 'error'].includes(status)) return { label: '실패', tone: 'danger' }
  if (status === 'pending_review') return { label: '검수 대기', tone: 'pending' }
  if (status === 'running') return { label: '실행 중', tone: 'pending' }
  if (['skipped', 'disabled'].includes(status)) return { label: status === 'skipped' ? '건너뜀' : '비활성', tone: 'neutral' }
  return { label: '상태 확인 필요', tone: 'warning', hint: `서버 상태: ${status}` }
}

export const jobLabel = (id: string, name?: string | null) => {
  if (/staff/i.test(id)) return '교직원 연락처 수집'
  if (/notice/i.test(id)) return '공지사항 수집'
  if (/meal/i.test(id)) return '학식 수집'
  if (/schedule/i.test(id)) return '학사일정 수집'
  if (/rule/i.test(id)) return '학칙·규정 수집'
  if (/course/i.test(id)) return '교과과정 수집'
  if (/heartbeat/i.test(id)) return '서비스 상태 기록'
  return name || id
}

/** APScheduler 3 CronTrigger uses Monday=0 (not the Unix crontab Sunday=0).
 * Interpret only known forms; keep the original expression available in the UI.
 */
export const cronLabel = (raw?: string | null) => {
  if (!raw) return '주기 정보 없음'
  const match = /^cron\[(.*)\]$/.exec(raw)
  if (match) {
    const fields = Object.fromEntries(Array.from(match[1].matchAll(/(\w+)='([^']+)'/g), (part) => [part[1], part[2]]))
    const calendarUnrestricted = ['year', 'month', 'day', 'week'].every((key) => !fields[key] || fields[key] === '*')
    const knownFields = Object.keys(fields).every((key) => ['year', 'month', 'day', 'week', 'day_of_week', 'hour', 'minute', 'second'].includes(key))
    const weekdayNames: Record<string, string> = { mon: '월요일', tue: '화요일', wed: '수요일', thu: '목요일', fri: '금요일', sat: '토요일', sun: '일요일' }
    const weekday = fields.day_of_week ?? '*'
    const weekLabel = /^\d$/.test(weekday) ? Object.values(weekdayNames)[Number(weekday)] : weekdayNames[weekday]
    const period = weekday === '*' ? '매일' : weekLabel ? `매주 ${weekLabel}` : null
    if (calendarUnrestricted && knownFields && period
      && /^\d{1,2}(,\d{1,2})*$/.test(fields.hour ?? '') && /^\d{1,2}$/.test(fields.minute ?? '')
      && (!fields.second || fields.second === '0')) {
      const hours = fields.hour.split(',').map(Number)
      if (hours.every((hour) => hour <= 23) && Number(fields.minute) <= 59) {
        return `${period} ${hours.map((hour) => `${hour}:${fields.minute.padStart(2, '0')}`).join(' · ')} (서버 시간대)`
      }
    }
  }
  const interval = /^interval\[(\d+):(\d{2}):(\d{2})\]$/.exec(raw)
  if (interval && Number(interval[2]) < 60 && Number(interval[3]) < 60) {
    return `${Number(interval[1])}시간 ${Number(interval[2])}분 ${Number(interval[3])}초마다`
  }
  return '사용자 지정 주기 · 상세 확인'
}
