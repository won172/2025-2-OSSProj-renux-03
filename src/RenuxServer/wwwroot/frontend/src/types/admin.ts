export type AdminOrgStatus = '활성' | '검토 중' | '일시중지'

export interface CouncilOrganization {
  id: string
  name: string
  manager: string
  updatedAt: string
  status: AdminOrgStatus
  pendingRequests: number
}

export interface EscalationRequest {
  id: string
  title: string
  departmentName: string
  createdAt: string
  summary: string
  requester?: string
}

export interface DepartmentRequest extends EscalationRequest {
  chatId: string
  originalQuestion: string
}

export interface PendingAnswerReview {
  id: string
  departmentName: string
  submittedAt: string
  handler: string
  question: string
  answer: string
  status?: string
}

export type KnowledgeStatus = 'PENDING' | 'APPROVED' | 'REJECTED'

export interface DepartmentKnowledge {
  id: string
  title: string
  content: string
  status: KnowledgeStatus
  createdAt: string
  rejectionReason?: string
}

export interface RagChatLog {
  id: number
  question: string
  answer: string
  fallback_triggered: boolean
  fallback_reason: string | null
  created_at: string
  route: string
  source_count: number
}
