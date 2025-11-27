export type UserRole = 'STUDENT' | 'DEPARTMENT_COUNCIL' | 'UNIVERSITY_COUNCIL'

export interface AuthNameResponse {
  name: string
  role?: string
  departmentName?: string
  departmentId?: string
}
