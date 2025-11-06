import type { DepartmentMajor } from './organization'

export interface ActiveChatOrganization {
  major?: DepartmentMajor | null
}

export interface ActiveChat {
  id: string
  title?: string | null
  organization?: ActiveChatOrganization | null
}
