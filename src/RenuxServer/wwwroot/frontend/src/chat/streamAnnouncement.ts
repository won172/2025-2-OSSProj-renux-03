export type ChatStreamStatus = 'completed' | 'stopped' | null

export const createChatStreamAnnouncementTracker = () => {
  let activeRequest = 0
  let userStoppedRequest: number | null = null
  let announced = false

  return {
    beginRequest: () => {
      activeRequest += 1
      userStoppedRequest = null
      announced = false
      return activeRequest
    },
    markUserStop: () => {
      userStoppedRequest = activeRequest
    },
    isCurrent: (request: number) => request === activeRequest,
    terminal: (request: number, status: Exclude<ChatStreamStatus, null> | 'error'): ChatStreamStatus => {
      if (request !== activeRequest || announced) return null
      announced = true
      if (status === 'error') return null // ChatComposer's alert already announces the failure.
      return status === 'stopped' && userStoppedRequest !== request ? null : status
    },
  }
}
