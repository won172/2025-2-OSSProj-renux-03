export type AdminOrgStatus = '활성' | '검토 중' | '일시중지'

export interface CouncilOrganization {
  id: string
  name: string
  manager: string
  updatedAt: string
  status: AdminOrgStatus
  pendingRequests: number
}

export type AdminItemStatus = 'pending' | 'approved' | 'approved_manually' | 'rejected'

export interface AdminItemResponse {
  id: number | string
  source_type: string
  data: string
  status: AdminItemStatus
  created_at: string
  /** 반려/승인 처리 시 남긴 메모. 백엔드 보강 이전 데이터에는 없을 수 있다. */
  review_note?: string | null
  /** 처리자 표시명. 감사 로그 보강 이전 데이터에는 없을 수 있다. */
  reviewed_by?: string | null
  reviewed_at?: string | null
  /** 승인 항목을 챗봇 노출에서 내렸는지 여부 */
  disabled?: boolean
}

export interface PendingAnswerReview {
  id: string
  departmentName: string
  submittedAt: string
  handler: string
  question: string
  answer: string
  status: AdminItemStatus
  sourceType: string
  reviewNote?: string | null
  reviewedBy?: string | null
  reviewedAt?: string | null
  disabled?: boolean
  /** 원본 payload — 승인 전 수정에서 필드 단위로 편집할 때 사용 */
  raw: Record<string, unknown>
}

export type KnowledgeStatus = 'PENDING' | 'APPROVED' | 'REJECTED'

export interface DepartmentKnowledge {
  id: string
  title: string
  content: string
  status: KnowledgeStatus
  createdAt: string
  sourceType: string
  rejectionReason?: string | null
  raw: Record<string, unknown>
}

export interface RagChatLog {
  id: number
  question: string
  answer: string
  fallback_triggered: boolean
  fallback_reason: string | null
  session_id?: string | null
  username?: string | null
  created_at: string
  route: string
  source_count: number
}

export interface RagFeedbackItem {
  id: number
  rating: number
  reason: string | null
  comment: string | null
  major: string | null
  createdAt: string | null
  question: string | null
  answer: string | null
}

export interface ApiRagFeedbackItem {
  id: number
  rating: number
  reason: string | null
  comment: string | null
  major: string | null
  created_at: string | null
  question: string | null
  answer: string | null
}

export interface CouncilSignupRequest {
  id: string
  userId: string
  username: string
  majorId: string
  majorName?: string | null
  status: string
  createdTime: string
  reviewedTime?: string | null
  reviewNote?: string | null
}

export interface RagAdminFeedbackSummary {
  total: number
  up: number
  down: number
  satisfaction: number | null
  downReasons?: Record<string, number>
  down_reasons?: Record<string, number>
}

export interface RagDatasetStatus {
  key: string
  collection: string
  chroma_count: number | null
  cached_chunk_count: number
  chunk_artifact_exists: boolean
  chunk_artifact_mtime: string | null
  latest_document_published_at?: string | null
  vectorizer_exists: boolean
  vectorizer_mtime: string | null
  last_successful_indexed_at?: string | null
  vectorizer_sklearn_version?: string | null
  status: 'ok' | 'degraded' | 'error'
  error?: string | null
}

export interface RagNoticesIngestion {
  last_collection_at: string | null
  last_successful_ingestion_at: string | null
  ingestion_summary: {
    status: string | null
    documents_seen: number
    documents_new: number
    documents_updated: number
    documents_deleted: number
    documents_failed: number
  }
  stage_summary: {
    raw_documents: number
    normalized_documents: number
    indexed_documents: number
  }
  quality_summary: {
    parse_failed: number
    severities: Record<string, number>
    recent_checks: Array<{
      document_key: string
      check_type: string
      severity: string
      message: string
      created_at: string
    }>
  }
}

export interface RagAdminStatus {
  status: 'ok' | 'degraded' | 'error'
  generated_at: string
  datasets: RagDatasetStatus[]
  pending_items: {
    pending: number
    approved: number
    rejected: number
  }
  rag_logs: {
    total_queries: number
    fallback_count: number
    latest_query_at: string | null
    fallback_reasons?: Record<string, number>
    /** 집계 기간(일). 지표는 누적이 아니라 이 기간 기준이다. */
    window_days?: number
    /** 같은 기간의 평가 하네스·시점 이동 요청 수(품질 지표에서 제외된 몫). */
    synthetic_query_count?: number
  }
  visitor_stats?: {
    today: number | null
    total: number | null
  }
  feedback?: RagAdminFeedbackSummary
  notices_ingestion?: RagNoticesIngestion
  scheduler?: RagSchedulerStatus
  error?: string
}

export interface RagSchedulerJob {
  id: string
  name?: string | null
  next_run_at: string | null
  trigger?: string | null
  last_run_at?: string | null
  last_status?: string | null
  last_message?: string | null
}

export interface RagSchedulerStatus {
  enabled: boolean
  jobs: RagSchedulerJob[]
}

export interface RagHealthStatus {
  status?: string
  ready?: boolean
  detail?: string | null
}

export interface ReindexResult {
  status: string
  message?: string
  details?: Record<string, number>
}

export interface MajorOption {
  id: string
  majorname?: string
  Majorname?: string
}

export interface AdminRoleOption {
  id: string
  roleName: string
}

export interface AdminUserAccount {
  id: string
  userId: string
  username: string
  majorId: string
  majorName?: string | null
  roleId: string
  roleName?: string | null
  createdTime: string
  updatedTime: string
}

export interface ProductKpiRatio {
  numerator: number
  denominator: number
  rate: number | null
}

export interface ProductKpiReport {
  from: string
  to: string
  helpfulAnswerRate: ProductKpiRatio
  sevenDayValidReuseRate: ProductKpiRatio
  excludedEventCount: number
  caveats: string[]
}

/** 대시보드 활동 피드 항목 — 여러 소스를 한 타임라인으로 합칠 때의 공통 계약 */
export interface AdminActivityEntry {
  id: string
  kind: 'submitted' | 'approved' | 'rejected' | 'signup'
  title: string
  meta: string
  occurredAt: string
  status?: AdminItemStatus | string
  href?: string
}
