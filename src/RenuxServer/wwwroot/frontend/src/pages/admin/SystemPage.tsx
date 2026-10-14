import { cronLabel, datasetLabel, jobLabel, jobStatus } from '../../admin/display'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { reindexDataset } from '../../admin/adminApi'
import {
  formatDateTime,
  formatFullDateTime,
  getApiErrorMessage,
  getSystemStatusLabel,
  getSystemStatusTone,
} from '../../admin/format'
import Modal from '../../components/admin/Modal'
import { useAdminConsole } from '../../components/admin/adminConsoleContext'
import {
  EmptyState,
  ErrorNote,
  LoadingNote,
  MetricCard,
  PageHeader,
  Panel,
  StatusPill,
  TableScroll,
} from '../../components/admin/ui'
import type { StatusTone } from '../../admin/format'

type Tab = 'index' | 'ingestion' | 'jobs'

const severityTone = (severity: string): StatusTone => {
  const normalized = severity.toLowerCase()
  if (normalized === 'error' || normalized === 'critical') return 'danger'
  if (normalized === 'warning' || normalized === 'warn') return 'warning'
  return 'neutral'
}

const SystemPage = () => {
  const { ragStatus, ragStatusError, refreshSummary, refreshing, showToast } = useAdminConsole()
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab')
  const tab: Tab = tabParam === 'ingestion' || tabParam === 'jobs' ? tabParam : 'index'

  const [reindexTarget, setReindexTarget] = useState<string | null>(null)
  const [runningTarget, setRunningTarget] = useState<string | null>(null)

  const setTab = (next: Tab) => {
    searchParams.set('tab', next)
    setSearchParams(searchParams, { replace: true })
  }

  const runReindex = async (target: string) => {
    setRunningTarget(target)
    try {
      const result = await reindexDataset(target)
      const detail = result.details
        ? Object.entries(result.details).map(([key, count]) => `${key} ${count}건`).join(', ')
        : ''
      showToast(
        `${target === 'all' ? '전체' : target} 재인덱싱을 완료했습니다.${detail ? ` (${detail})` : ''}`,
        'success',
        { duration: 8000 },
      )
      await refreshSummary()
    } catch (error) {
      showToast(getApiErrorMessage(error, '재인덱싱에 실패했습니다.'), 'error')
    } finally {
      setRunningTarget(null)
      setReindexTarget(null)
    }
  }

  const ingestion = ragStatus?.notices_ingestion
  const scheduler = ragStatus?.scheduler

  return (
    <>
      <PageHeader
        title="시스템"
        description="RAG 인덱스, 데이터 수집 파이프라인, 자동 작업 상태를 확인하고 조치합니다."
        actions={
          <>
            <button type="button" className="ac-btn" onClick={() => { void refreshSummary() }} disabled={refreshing}>
              {refreshing ? '새로고침 중...' : '새로고침'}
            </button>
            <button
              type="button"
              className="ac-btn ac-btn--primary"
              onClick={() => setReindexTarget('all')}
              disabled={runningTarget !== null}
            >
              {runningTarget === 'all' ? '재인덱싱 중...' : '전체 재인덱싱'}
            </button>
          </>
        }
      />

      <div className="ac-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'index'} className={`ac-tab ${tab === 'index' ? 'ac-tab--active' : ''}`} onClick={() => setTab('index')}>
          인덱스
        </button>
        <button type="button" role="tab" aria-selected={tab === 'ingestion'} className={`ac-tab ${tab === 'ingestion' ? 'ac-tab--active' : ''}`} onClick={() => setTab('ingestion')}>
          수집 파이프라인
        </button>
        <button type="button" role="tab" aria-selected={tab === 'jobs'} className={`ac-tab ${tab === 'jobs' ? 'ac-tab--active' : ''}`} onClick={() => setTab('jobs')}>
          작업·스케줄러
        </button>
      </div>

      {ragStatusError ? (
        <Panel><ErrorNote>{ragStatusError}</ErrorNote></Panel>
      ) : !ragStatus ? (
        <Panel><LoadingNote>운영 상태를 확인하는 중입니다.</LoadingNote></Panel>
      ) : tab === 'index' ? (
        <>
          <div className="ac-metrics">
            <MetricCard
              label="전체 상태"
              value={getSystemStatusLabel(ragStatus.status)}
              tone={getSystemStatusTone(ragStatus.status)}
            />
            {/* 이 두 값은 누적이 아니라 최근 기간의 실제 사용자 질문만 센다. */}
            <MetricCard
              label="질문 로그"
              value={ragStatus.rag_logs.total_queries.toLocaleString('ko-KR')}
              hint={ragStatus.rag_logs.window_days ? `최근 ${ragStatus.rag_logs.window_days}일` : undefined}
            />
            <MetricCard
              label="Fallback"
              value={ragStatus.rag_logs.fallback_count.toLocaleString('ko-KR')}
              hint={ragStatus.rag_logs.window_days ? `최근 ${ragStatus.rag_logs.window_days}일` : undefined}
            />
            <MetricCard label="승인 대기" value={ragStatus.pending_items.pending} />
            <MetricCard label="최근 질문" value={formatDateTime(ragStatus.rag_logs.latest_query_at)} />
            <MetricCard label="상태 기준 시각" value={formatDateTime(ragStatus.generated_at)} />
          </div>

          <Panel
            padded={false}
            title="데이터셋 인덱스"
            description="ChromaDB 컬렉션, 로컬 아티팩트, TF-IDF 벡터라이저 상태입니다."
          >
            <TableScroll mobileCards minWidth={1120}>
              <table className="ac-table" role="table">
                <thead role="rowgroup">
                  <tr role="row">
                    <th role="columnheader" scope="col">자료</th>
                    <th role="columnheader" scope="col">컬렉션</th>
                    <th role="columnheader" scope="col">Chroma</th>
                    <th role="columnheader" scope="col">캐시 청크</th>
                    <th role="columnheader" scope="col">청크 파일</th>
                    <th role="columnheader" scope="col">벡터라이저</th>
                    <th role="columnheader" scope="col">최신 문서일</th>
                    <th role="columnheader" scope="col">마지막 인덱싱</th>
                    <th role="columnheader" scope="col">TF-IDF 버전</th>
                    <th role="columnheader" scope="col">상태</th>
                    <th role="columnheader" scope="col" className="actions">동작</th>
                  </tr>
                </thead>
                <tbody role="rowgroup">
                  {ragStatus.datasets.map((dataset) => (
                    <tr role="row" key={dataset.key}>
                      <td role="cell" data-label="자료"><b>{datasetLabel(dataset.key)}</b></td>
                      <td role="cell" data-label="컬렉션">{dataset.collection}</td>
                      <td role="cell" data-label="검색 문서 수" className="num">{dataset.chroma_count ?? '-'}</td>
                      <td role="cell" data-label="캐시 청크 수" className="num">{dataset.cached_chunk_count}</td>
                      <td role="cell" data-label="청크 파일" title={formatFullDateTime(dataset.chunk_artifact_mtime)}>
                        {dataset.chunk_artifact_exists ? '있음' : '없음'}<details className="ac-cell-details"><summary>파일 시각</summary>{formatFullDateTime(dataset.chunk_artifact_mtime)}</details>
                      </td>
                      <td role="cell" data-label="벡터라이저" title={formatFullDateTime(dataset.vectorizer_mtime)}>
                        {dataset.vectorizer_exists ? '있음' : '없음'}<details className="ac-cell-details"><summary>파일 시각</summary>{formatFullDateTime(dataset.vectorizer_mtime)}</details>
                      </td>
                      <td role="cell" data-label="최신 문서일" className="num">{formatDateTime(dataset.latest_document_published_at)}</td>
                      <td role="cell" data-label="마지막 인덱싱" className="num" title={formatFullDateTime(dataset.last_successful_indexed_at)}>
                        {formatDateTime(dataset.last_successful_indexed_at)}
                      </td>
                      <td role="cell" data-label="벡터라이저 버전" className="num">{dataset.vectorizer_sklearn_version ?? '-'}</td>
                      <td role="cell" data-label="상태" title={dataset.error ?? undefined}>
                        <StatusPill tone={getSystemStatusTone(dataset.status)}>
                          {getSystemStatusLabel(dataset.status)}
                        </StatusPill>
                      </td>
                      <td role="cell" data-label="동작" className="actions">
                        <button
                          type="button"
                          className="ac-btn ac-btn--sm"
                          onClick={() => setReindexTarget(dataset.key)}
                          disabled={runningTarget !== null}
                        >
                          {runningTarget === dataset.key ? '실행 중...' : '재인덱싱'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          </Panel>

          {ragStatus.datasets.some((dataset) => dataset.error) && (
            <Panel title="상태 상세" description="주의·오류로 표시된 데이터셋의 원인입니다.">
              <ul style={{ margin: 0, paddingLeft: '18px', fontSize: '13px' }}>
                {ragStatus.datasets.filter((dataset) => dataset.error).map((dataset) => (
                  <li key={dataset.key} style={{ marginBottom: '6px' }}>
                    <b>{datasetLabel(dataset.key)}</b> — {dataset.error}
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </>
      ) : tab === 'ingestion' ? (
        !ingestion ? (
          <Panel><EmptyState>수집 파이프라인 정보가 없습니다.</EmptyState></Panel>
        ) : (
          <>
            <Panel
              title="공지 수집 상태"
              description="raw / normalized / indexed 단계별 수집 결과와 품질 경고입니다."
              actions={
                <StatusPill tone={
                  ingestion.ingestion_summary.status === 'failed'
                    ? 'danger'
                    : ingestion.ingestion_summary.documents_failed > 0 ? 'warning' : jobStatus(ingestion.ingestion_summary.status).tone
                }>
                  {jobStatus(ingestion.ingestion_summary.status).label}
                </StatusPill>
              }
            >
              <div className="ac-metrics">
                <MetricCard label="마지막 수집" value={formatDateTime(ingestion.last_collection_at)} />
                <MetricCard label="마지막 성공 run" value={formatDateTime(ingestion.last_successful_ingestion_at)} />
                <MetricCard
                  label="파싱 실패"
                  value={ingestion.quality_summary.parse_failed}
                  tone={ingestion.quality_summary.parse_failed > 0 ? 'warning' : undefined}
                />
                <MetricCard
                  label="품질 경고"
                  value={ingestion.quality_summary.severities.warning ?? 0}
                  tone={(ingestion.quality_summary.severities.warning ?? 0) > 0 ? 'warning' : undefined}
                />
              </div>
            </Panel>

            <Panel title="문서 처리 결과" description="마지막 수집 run에서 문서가 어떻게 처리되었는지입니다.">
              <div className="ac-metrics">
                <MetricCard label="확인한 문서" value={ingestion.ingestion_summary.documents_seen} />
                <MetricCard label="신규" value={ingestion.ingestion_summary.documents_new} />
                <MetricCard label="수정" value={ingestion.ingestion_summary.documents_updated} />
                <MetricCard label="숨김/삭제" value={ingestion.ingestion_summary.documents_deleted} />
                <MetricCard
                  label="실패"
                  value={ingestion.ingestion_summary.documents_failed}
                  tone={ingestion.ingestion_summary.documents_failed > 0 ? 'danger' : undefined}
                />
              </div>
            </Panel>

            <Panel title="단계별 문서 수" description="수집 → 정규화 → 색인 각 단계에 남아 있는 문서 수입니다.">
              <div className="ac-metrics">
                <MetricCard label="수집 원문" value={ingestion.stage_summary.raw_documents} />
                <MetricCard label="정규화 문서" value={ingestion.stage_summary.normalized_documents} />
                <MetricCard label="색인 문서" value={ingestion.stage_summary.indexed_documents} />
              </div>
            </Panel>

            <Panel padded={false} title="최근 품질 검사" description="심각도가 높은 항목이 위에 옵니다.">
              {ingestion.quality_summary.recent_checks.length === 0 ? (
                <EmptyState>기록된 품질 검사가 없습니다.</EmptyState>
              ) : (
                <TableScroll mobileCards minWidth={860}>
                  <table className="ac-table" role="table">
                    <thead role="rowgroup">
                      <tr role="row">
                        <th role="columnheader" scope="col">문서</th>
                        <th role="columnheader" scope="col">검사</th>
                        <th role="columnheader" scope="col">심각도</th>
                        <th role="columnheader" scope="col">메시지</th>
                        <th role="columnheader" scope="col">시각</th>
                      </tr>
                    </thead>
                    <tbody role="rowgroup">
                      {[...ingestion.quality_summary.recent_checks]
                        .sort((a, b) => {
                          const rank = (severity: string) => (severityTone(severity) === 'danger' ? 0 : severityTone(severity) === 'warning' ? 1 : 2)
                          const diff = rank(a.severity) - rank(b.severity)
                          if (diff !== 0) return diff
                          return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
                        })
                        .map((check) => (
                          <tr role="row" key={`${check.document_key}-${check.check_type}-${check.created_at}`}>
                            <td role="cell" data-label="문서" className="truncate" title={check.document_key}>{check.document_key}</td>
                            <td role="cell" data-label="검사">{check.check_type}</td>
                            <td role="cell" data-label="심각도"><StatusPill tone={severityTone(check.severity)}>{check.severity}</StatusPill></td>
                            <td role="cell" data-label="메시지" className="truncate" title={check.message}>{check.message}</td>
                            <td role="cell" data-label="시각" className="num" title={formatFullDateTime(check.created_at)}>{formatDateTime(check.created_at)}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </TableScroll>
              )}
            </Panel>
          </>
        )
      ) : (
        <>
          <Panel
            title="자동 수집 스케줄러"
            description="공지·학식 데이터를 정해진 시각에 자동으로 갱신합니다."
            actions={
              scheduler
                ? <StatusPill tone={scheduler.enabled ? 'success' : 'neutral'}>{scheduler.enabled ? '동작 중' : '비활성'}</StatusPill>
                : undefined
            }
          >
            {!scheduler ? (
              <EmptyState>
                스케줄러 상태를 제공하지 않는 버전의 RAG 서비스입니다.
                기본 설정은 공지 매일 0·6·12·18시, 학식 매일 04:30입니다.
              </EmptyState>
            ) : scheduler.jobs.length === 0 ? (
              <EmptyState>등록된 자동 작업이 없습니다.</EmptyState>
            ) : (
              <TableScroll mobileCards minWidth={820}>
                <table className="ac-table" role="table">
                  <thead role="rowgroup">
                    <tr role="row">
                      <th role="columnheader" scope="col">작업</th>
                      <th role="columnheader" scope="col">주기</th>
                      <th role="columnheader" scope="col">다음 실행</th>
                      <th role="columnheader" scope="col">마지막 실행</th>
                      <th role="columnheader" scope="col">결과</th>
                    </tr>
                  </thead>
                  <tbody role="rowgroup">
                    {scheduler.jobs.map((job) => (
                      <tr role="row" key={job.id}>
                        <td role="cell" data-label="작업"><b>{jobLabel(job.id, job.name)}</b></td>
                        <td role="cell" data-label="주기" className="num"><span>{cronLabel(job.trigger)}</span><details className="ac-cell-details"><summary>원본 주기·작업 ID</summary><p>{job.id}</p><p>{job.trigger || '정보 없음'}</p></details></td>
                        <td role="cell" data-label="다음 실행" className="num">{formatDateTime(job.next_run_at)}</td>
                        <td role="cell" data-label="마지막 실행" className="num">{formatDateTime(job.last_run_at)}</td>
                        <td role="cell" data-label="결과" title={job.last_message ?? undefined}>
                          {job.last_status
                            ? (
                                <StatusPill tone={jobStatus(job.last_status).tone}>
                                  {jobStatus(job.last_status).label}
                                </StatusPill>
                              )
                            : <span className="ac-hint">기록 없음</span>}
                          {jobStatus(job.last_status).hint && <p>{jobStatus(job.last_status).hint}</p>}
                          {job.last_message && <details className="ac-cell-details"><summary>실행 메시지</summary><p>{job.last_message}</p><p>서버 상태: {job.last_status}</p></details>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
            )}
          </Panel>

          <Panel title="수동 재인덱싱" description="특정 데이터셋의 색인을 지금 다시 만듭니다. 데이터 양에 따라 수 분이 걸릴 수 있습니다.">
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              {ragStatus.datasets.map((dataset) => (
                <button
                  key={dataset.key}
                  type="button"
                  className="ac-btn"
                  onClick={() => setReindexTarget(dataset.key)}
                  disabled={runningTarget !== null}
                >
                  {runningTarget === dataset.key ? `${dataset.key} 실행 중...` : `${dataset.key} 재인덱싱`}
                </button>
              ))}
            </div>
          </Panel>
        </>
      )}

      <Modal
        open={reindexTarget !== null}
        title="재인덱싱 실행"
        description={
          <>
            <b>{reindexTarget === 'all' ? '모든 데이터셋' : reindexTarget}</b>의 색인을 다시 만듭니다.
            작업 중에는 해당 데이터셋 검색 결과가 일시적으로 달라질 수 있고, 데이터 양에 따라 수 분이 걸립니다.
          </>
        }
        confirmLabel="실행"
        busy={runningTarget !== null}
        onCancel={() => setReindexTarget(null)}
        onConfirm={() => { if (reindexTarget) void runReindex(reindexTarget) }}
      />
    </>
  )
}

export default SystemPage
