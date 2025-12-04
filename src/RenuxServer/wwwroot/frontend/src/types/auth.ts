export type UserRole = 'STUDENT' | 'DEPARTMENT_COUNCIL' | 'UNIVERSITY_COUNCIL'

export interface AuthNameResponse {
  name: string
  roleName?: string
  role?: string
  departmentName?: string
  departmentId?: string
}
