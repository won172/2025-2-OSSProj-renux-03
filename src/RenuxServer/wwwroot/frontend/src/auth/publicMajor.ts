/** Administrative pseudo-majors must not become student questions or council copy. */
export const publicMajorName = (value?: string | null): string => {
  const name = value?.trim() ?? ''
  return name === '관리자' ? '' : name
}
