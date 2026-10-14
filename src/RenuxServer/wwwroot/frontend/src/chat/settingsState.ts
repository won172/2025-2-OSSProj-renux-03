import type { NotificationPreference } from '../types/notification'

export const preferenceKey = (preferences: NotificationPreference[]) => JSON.stringify(
  preferences.map((item) => ({ ...item, remindDaysBefore: [...item.remindDaysBefore].sort((a, b) => a - b) }))
    .sort((a, b) => a.topic.localeCompare(b.topic)),
)

/** Strip only labelled ingestion annotations; keep dates, contacts and all useful prose.
 * This is presentation-only. API objects and original source URLs are never rewritten.
 */
export const cleanDeadlineSnippet = (snippet?: string | null) => (snippet ?? '')
  .replace(/\[본문 이미지 전사\s*·\s*방식:[^\]]*(?:\]|$)/g, '')
  .replace(/\[(?:게시판\s*:[^\]]*|(?:source_document_id|document_key|chunk_id|source_id|ocr_engine|ocr_confidence|parser_version|provenance)\s*[:=][^\]]*)\]/gi, '')
  .replace(/^\s*(?:source_document_id|document_key|chunk_id|source_id|ocr_engine|ocr_confidence|parser_version|provenance)\s*[:=][^\n]*(?:\n|$)/gim, '')
  .replace(/[ \t]{2,}/g, ' ')
  .replace(/\n{3,}/g, '\n\n')
  .trim()

export const pageSlice = <T,>(items: T[], requestedPage: number, size = 8) => {
  const pageCount = Math.max(1, Math.ceil(items.length / size))
  const page = Math.max(0, Math.min(requestedPage, pageCount - 1))
  return { page, pageCount, items: items.slice(page * size, (page + 1) * size) }
}
