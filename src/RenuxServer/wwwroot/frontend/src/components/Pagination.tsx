export default function Pagination({ page, pageCount, total, onChange }: {
  page: number; pageCount: number; total: number; onChange: (page: number) => void
}) {
  if (pageCount <= 1) return null
  return <nav className="list-pagination" aria-label="목록 페이지">
    <button type="button" onClick={() => onChange(page - 1)} disabled={page === 0}>이전</button>
    <span role="status">{page + 1} / {pageCount} · 조회 {total}건</span>
    <button type="button" onClick={() => onChange(page + 1)} disabled={page === pageCount - 1}>다음</button>
  </nav>
}
