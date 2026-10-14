import { readStaffRefresh } from '../../admin/staffReview'
import { formatFullDateTime } from '../../admin/format'
import { ErrorNote } from './ui'

const Contacts = ({ contacts }: { contacts: [string, string][] }) => contacts.length === 0
  ? <p>해당 연락처 없음</p>
  : <ul>{contacts.map(([phone, email], index) => <li key={index}>전화: {phone || '없음'}<br />이메일: {email || '없음'}</li>)}</ul>

export default function StaffRefreshPreview({ data }: { data: Record<string, unknown> }) {
  const { payload, error } = readStaffRefresh(data)
  if (!payload) return <ErrorNote>{error}</ErrorNote>
  const { diff } = payload
  return (
    <section className="ac-staff-review" aria-label="교직원 연락처 변경 검토">
      <h3>교직원 연락처 갱신</h3>
      <dl className="ac-detail__meta">
        <div><dt>현재 / 수집 기록</dt><dd>{diff.current_rows}건 / {diff.incoming_rows}건</dd></div>
        <div><dt>추가 / 삭제</dt><dd>{diff.added}건 / {diff.removed}건</dd></div>
        <div><dt>연락처 변경</dt><dd>{diff.contact_changed}건</dd></div>
        <div><dt>스냅샷 생성 시각</dt><dd>{formatFullDateTime(payload.created_at)}</dd></div>
      </dl>
      <p className="ac-hint">변경된 식별 그룹 {diff.samples.length}개를 표시합니다. 서버는 최대 100개 샘플만 제공합니다. 샘플은 전체 변경 명단이 아니며, 승인은 이 스냅샷 전체에 적용됩니다.</p>
      <details><summary>스냅샷 식별 정보</summary><dl>
        <dt>파일</dt><dd>{payload.snapshot_file}</dd>
        <dt>SHA-256</dt><dd>{payload.snapshot_sha256}</dd>
        <dt>스키마 버전</dt><dd>{payload.schema_version}</dd>
      </dl></details>
      <div className="ac-staff-samples">
        {diff.samples.map((sample, index) => (
          <article key={index} className="ac-staff-sample">
            <h4>변경 샘플 {index + 1}</h4>
            {sample.identity[0] === 'upstream' ? <p>원천 식별자: {sample.identity[1]}</p> : (
              <dl className="ac-detail__meta">{['부서 경로', '성명', '직위', '담당 업무'].map((label, part) => (
                <div key={label}><dt>{label}</dt><dd>{sample.identity[part + 1] || '기록 없음'}</dd></div>
              ))}</dl>
            )}
            <div className="ac-staff-contacts">
              <div><h5>변경 전 · 빠지는 연락처</h5><Contacts contacts={sample.before_contacts} /></div>
              <div><h5>변경 후 · 들어오는 연락처</h5><Contacts contacts={sample.after_contacts} /></div>
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}
