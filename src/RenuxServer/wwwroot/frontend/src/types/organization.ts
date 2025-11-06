export interface DepartmentMajor {
  majorname?: string | null
}

export interface Department {
  id: string
  major?: DepartmentMajor | null
}
