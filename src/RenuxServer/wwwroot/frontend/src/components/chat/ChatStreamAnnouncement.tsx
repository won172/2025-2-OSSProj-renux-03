import type { ChatStreamStatus } from '../../chat/streamAnnouncement'

const statusText: Record<Exclude<ChatStreamStatus, null>, string> = {
  completed: '답변 완료',
  stopped: '답변 중단',
}

const ChatStreamAnnouncement = ({ status }: { status: ChatStreamStatus }) => (
  <p className="ch-visually-hidden" role="status" aria-live="polite" aria-atomic="true">
    {status === null ? '' : statusText[status]}
  </p>
)

export default ChatStreamAnnouncement
