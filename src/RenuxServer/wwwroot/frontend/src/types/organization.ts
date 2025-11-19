export interface DepartmentMajor {
  id?: string
  majorname?: string | null
}

export interface Department {
  id: string
  major?: DepartmentMajor | null
}
