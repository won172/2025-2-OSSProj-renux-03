/** Schema produced by RAG services/staff_refresh.py, not a FAQ payload. */
export interface StaffSample {
  identity: string[]
  before_contacts: [string, string][]
  after_contacts: [string, string][]
}

export interface StaffRefreshPayload {
  schema_version: 1
  snapshot_file: string
  snapshot_sha256: string
  created_at: string
  diff: {
    current_rows: number
    incoming_rows: number
    added: number
    removed: number
    contact_changed: number
    changed: boolean
    samples: StaffSample[]
  }
}

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0
const contacts = (value: unknown): value is [string, string][] => Array.isArray(value)
  && value.every((pair) => Array.isArray(pair) && pair.length === 2 && pair.every((part) => typeof part === 'string'))

export const readStaffRefresh = (value: unknown): { payload: StaffRefreshPayload | null; error: string | null } => {
  const invalid = (detail: string) => ({ payload: null, error: `교직원 변경 검토 자료가 올바르지 않습니다: ${detail}. 다시 불러오거나 수집 담당자에게 확인해주세요. 승인할 수 없습니다.` })
  if (!record(value) || value.schema_version !== 1) return invalid('지원되는 스키마가 없음')
  if (typeof value.snapshot_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.snapshot_sha256)
    || value.snapshot_file !== `staff-${value.snapshot_sha256}.json`
    || typeof value.created_at !== 'string' || !Number.isFinite(Date.parse(value.created_at))) {
    return invalid('스냅샷 식별 정보가 없음')
  }
  const diff = value.diff
  if (!record(diff) || !['current_rows', 'incoming_rows', 'added', 'removed', 'contact_changed'].every((key) => count(diff[key]))) {
    return invalid('변경 집계가 없음')
  }
  const { current_rows: current, incoming_rows: incoming, added, removed, contact_changed: changed } = diff as StaffRefreshPayload['diff']
  if (incoming < 1 || current + added - removed !== incoming || removed + changed > current
    || added + changed > incoming || diff.changed !== true || added + removed + changed === 0) {
    return invalid('변경 집계가 서로 맞지 않음')
  }
  if (!Array.isArray(diff.samples) || diff.samples.length === 0 || diff.samples.length > 100
    || diff.samples.some((sample) => {
      if (!record(sample) || !Array.isArray(sample.identity) || !sample.identity.every((part) => typeof part === 'string')) return true
      const identity = sample.identity
      const validIdentity = (identity[0] === 'upstream' && identity.length === 2 && identity[1].trim())
        || (identity[0] === 'fallback' && identity.length === 5 && identity.slice(1).some((part) => part.trim()))
      return !validIdentity || !contacts(sample.before_contacts) || !contacts(sample.after_contacts)
        || sample.before_contacts.length + sample.after_contacts.length === 0
    })) return invalid('변경 전후 연락처 샘플이 없음 또는 손상됨')
  return { payload: value as unknown as StaffRefreshPayload, error: null }
}

export const staffRefreshSummary = (data: unknown) => {
  const { payload } = readStaffRefresh(data)
  if (!payload) return '변경 자료 확인 필요 · 승인 불가'
  const diff = payload.diff
  return `현재 ${diff.current_rows}건 → 수집 ${diff.incoming_rows}건 · 추가 ${diff.added} · 삭제 ${diff.removed} · 연락처 변경 ${diff.contact_changed}`
}

export const supportsContentEditing = (sourceType: string) =>
  ['custom_knowledge', 'event', 'announcement'].includes(sourceType)

/** Used both at the button and immediately before the request (including bulk requests). */
export const canApproveReview = (item: { sourceType: string; raw: Record<string, unknown>; status: string }) => {
  if (item.status !== 'pending') return false
  if (item.sourceType === 'staff_refresh') return readStaffRefresh(item.raw).payload !== null
  const keys = item.sourceType === 'custom_knowledge' ? ['question', 'answer']
    : item.sourceType === 'event' ? ['title', 'start_date']
      : item.sourceType === 'announcement' ? ['title', 'content'] : []
  return keys.length > 0 && keys.every((key) => typeof item.raw[key] === 'string' && (item.raw[key] as string).trim())
}
