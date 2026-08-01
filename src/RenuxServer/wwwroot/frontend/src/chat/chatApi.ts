import { apiFetch } from '../api/client'
import { withGuestTokenHeader } from './guestToken'
import type { ChatViewMessage } from './chatState'
import type { HomeBriefing } from '../types/briefing'
import type { ActiveChat } from '../types/chat'
import type { Department } from '../types/organization'
import type {
  DeadlineItem,
  NotificationPreferenceResponse,
  NotificationSyncResult,
  UserNotification,
} from '../types/notification'

/** 홈·채팅 화면이 쓰는 서버 호출을 한곳에 모은다. */

export const fetchDepartments = () => apiFetch<Department[]>('/req/orgs', { method: 'GET' })

export const fetchActiveChats = () => apiFetch<ActiveChat[]>('/chat/active', { method: 'GET' })

export const startChat = (org: Department, title: string, guestToken?: string) =>
  apiFetch<ActiveChat>('/chat/start', {
    method: 'POST',
    headers: withGuestTokenHeader({}, guestToken),
    json: { org, title },
  })

export const loadChatMessages = (chatId: string, lastTime: string) =>
  apiFetch<ChatViewMessage[]>('/chat/load', {
    method: 'POST',
    json: { chatId, lastTime },
  })

export const fetchFollowups = (requestId: string, guestToken?: string) =>
  apiFetch<{ questions: string[] }>('/chat/followups', {
    method: 'POST',
    headers: withGuestTokenHeader({}, guestToken),
    json: { requestId },
  })

export const deleteChat = (chatId: string) =>
  apiFetch(`/chat/${encodeURIComponent(chatId)}`, { method: 'DELETE' })

export const renameChat = (chatId: string, title: string) =>
  apiFetch<{ id: string; title: string }>(`/chat/${encodeURIComponent(chatId)}`, {
    method: 'PATCH',
    json: { title },
  })

/**
 * 게스트로 나눈 대화를 로그인 계정으로 옮긴다.
 * guestToken은 대화를 시작한 브라우저만 갖고 있으므로 서버가 소유권 증명으로 사용한다.
 */
export const claimGuestChats = (chatIds: string[], guestToken: string) =>
  apiFetch<{ claimed: number }>('/chat/claim', {
    method: 'POST',
    headers: withGuestTokenHeader({}, guestToken),
    json: { chatIds },
  })

export const fetchHomeBriefing = () => apiFetch<HomeBriefing>('/home/briefing', { method: 'GET' })

export const fetchNotifications = (includePast = false) =>
  apiFetch<UserNotification[]>(`/notifications?limit=100${includePast ? '&includePast=true' : ''}`)

export const fetchDeadlines = () => apiFetch<DeadlineItem[]>('/notifications/deadlines')

export const fetchNotificationPreferences = () =>
  apiFetch<NotificationPreferenceResponse>('/notifications/preferences')

export const syncNotifications = () =>
  apiFetch<NotificationSyncResult>('/notifications/sync', { method: 'POST' })

export const markNotificationRead = (notificationId: string) =>
  apiFetch<UserNotification>(`/notifications/${notificationId}/read`, { method: 'POST' })

export const markAllNotificationsRead = () =>
  apiFetch<{ updated: number }>('/notifications/read-all', { method: 'POST' })

export const deleteNotification = (notificationId: string) =>
  apiFetch<{ deleted: number }>(`/notifications/${notificationId}`, { method: 'DELETE' })

export const deleteReadNotifications = () =>
  apiFetch<{ deleted: number }>('/notifications/read', { method: 'DELETE' })
