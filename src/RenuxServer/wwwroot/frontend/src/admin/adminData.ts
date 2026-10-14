import { publicMajorName } from '../auth/publicMajor.ts'
import { staffRefreshSummary } from './staffReview.ts'
import type {
  AdminItemResponse,
  DepartmentKnowledge,
  PendingAnswerReview,
} from '../types/admin'

const parseItemData = (data: string): Record<string, unknown> => {
  try {
    const parsed = JSON.parse(data) as unknown
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {}
  } catch {
    return {}
  }
}

const readText = (data: Record<string, unknown>, key: string) => {
  const value = data[key]
  return typeof value === 'string' ? value : ''
}

const getItemDisplay = (item: AdminItemResponse) => {
  const data = parseItemData(item.data)

  if (item.source_type === 'custom_knowledge') {
    return {
      title: readText(data, 'question') || '질문 없음',
      content: readText(data, 'answer'),
      department: readText(data, 'category') || '공통',
      requester: readText(data, 'requester') || '정보 없음',
    }
  }

  if (item.source_type === 'event') {
    return {
      title: `[행사] ${readText(data, 'title')}`,
      content: `일시: ${readText(data, 'start_date')} ~ ${readText(data, 'end_date')}\n장소: ${readText(data, 'location')}\n\n${readText(data, 'description')}`,
      department: readText(data, 'department') || '공통',
      requester: readText(data, 'requester') || '정보 없음',
    }
  }

  if (item.source_type === 'announcement') {
    return {
      title: `[공지] ${readText(data, 'title')}`,
      content: `게시일: ${readText(data, 'date')}\n분류: ${readText(data, 'category')}\n\n${readText(data, 'content')}`,
      department: readText(data, 'department') || '공통',
      requester: readText(data, 'requester') || '정보 없음',
    }
  }

  if (item.source_type === 'staff_refresh') {
    return {
      title: '교직원 전화번호부 갱신',
      content: staffRefreshSummary(data),
      department: '대학 공통 · 교직원',
      requester: '교직원 자동 수집',
    }
  }

  return {
    title: `지원되지 않는 등록 유형 (${item.source_type})`,
    content: '',
    department: '공통',
    requester: readText(data, 'requester') || '정보 없음',
  }
}

export const toPendingAnswerReview = (item: AdminItemResponse): PendingAnswerReview => {
  const display = getItemDisplay(item)
  return {
    id: item.id.toString(),
    departmentName: display.department,
    submittedAt: item.created_at,
    handler: display.requester,
    question: display.title,
    answer: display.content,
    status: item.status,
    sourceType: item.source_type,
    reviewNote: item.review_note ?? null,
    reviewedBy: item.reviewed_by ?? null,
    reviewedAt: item.reviewed_at ?? null,
    disabled: item.disabled ?? false,
    raw: parseItemData(item.data),
  }
}

export const toDepartmentKnowledge = (item: AdminItemResponse): DepartmentKnowledge => {
  const display = getItemDisplay(item)
  const status = item.status === 'approved' || item.status === 'approved_manually'
    ? 'APPROVED'
    : item.status === 'rejected'
      ? 'REJECTED'
      : 'PENDING'

  return {
    id: item.id.toString(),
    title: display.title,
    content: display.content,
    status,
    createdAt: item.created_at,
    sourceType: item.source_type,
    rejectionReason: item.review_note ?? null,
    raw: parseItemData(item.data),
  }
}

/**
 * 승인 전 수정에서 편집 가능한 필드 정의.
 * 등록 유형마다 payload 스키마가 다르므로(FAQ=question/answer, 행사=title/start_date…)
 * 폼을 하드코딩하지 않고 이 표를 따라 렌더링한다.
 */
export interface EditableField {
  key: string
  label: string
  type: 'text' | 'textarea' | 'date'
}

export const EDITABLE_FIELDS_BY_TYPE: Record<string, EditableField[]> = {
  custom_knowledge: [
    { key: 'question', label: '질문', type: 'text' },
    { key: 'answer', label: '답변', type: 'textarea' },
  ],
  event: [
    { key: 'title', label: '행사명', type: 'text' },
    { key: 'start_date', label: '시작일', type: 'date' },
    { key: 'end_date', label: '종료일', type: 'date' },
    { key: 'location', label: '장소', type: 'text' },
    { key: 'description', label: '상세 내용', type: 'textarea' },
  ],
  announcement: [
    { key: 'title', label: '제목', type: 'text' },
    { key: 'date', label: '게시일', type: 'date' },
    { key: 'category', label: '카테고리', type: 'text' },
    { key: 'content', label: '상세 내용', type: 'textarea' },
  ],
}

export const getEditableFields = (sourceType: string): EditableField[] =>
  EDITABLE_FIELDS_BY_TYPE[sourceType] ?? []

/**
 * 승인 시 챗봇이 받게 될 텍스트를 재구성한다.
 * RAG 색인은 payload를 그대로 쓰지 않고 유형별로 문장을 합치므로,
 * 검수자가 "이 답변이 챗봇에서 어떻게 보이는지"를 등록 전에 확인할 수 있게 한다.
 */
export const buildChatPreview = (sourceType: string, data: Record<string, unknown>): string => {
  const text = (key: string) => (typeof data[key] === 'string' ? (data[key] as string).trim() : '')

  if (sourceType === 'custom_knowledge') {
    const question = text('question')
    const answer = text('answer')
    return [question && `Q. ${question}`, answer && `A. ${answer}`].filter(Boolean).join('\n\n')
  }

  if (sourceType === 'event') {
    const start = text('start_date')
    const end = text('end_date')
    const period = end && end !== start ? `${start} ~ ${end}` : start
    return [
      text('title') && `${text('title')} 행사 안내`,
      period && `일시: ${period}`,
      text('location') && `장소: ${text('location')}`,
      publicMajorName(text('department')) && `주관: ${publicMajorName(text('department'))}`,
      text('description'),
    ].filter(Boolean).join('\n')
  }

  if (sourceType === 'announcement') {
    return [
      text('title'),
      text('date') && `게시일: ${text('date')}`,
      text('category') && `분류: ${text('category')}`,
      publicMajorName(text('department')) && `학과: ${publicMajorName(text('department'))}`,
      text('content'),
    ].filter(Boolean).join('\n')
  }

  return Object.values(data)
    .filter((value): value is string => typeof value === 'string')
    .join('\n')
}
