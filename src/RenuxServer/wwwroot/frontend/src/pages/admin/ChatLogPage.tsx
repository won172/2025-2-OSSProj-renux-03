import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import type { RagChatLog } from '../../types/admin'

const formatDateTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

const ChatLogPage = () => {
  const navigate = useNavigate()
  const [logs, setLogs] = useState<RagChatLog[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // 200건 일괄 렌더링 방지: 검색 필터 + 점진 표시
  const [searchTerm, setSearchTerm] = useState('')
  const [visibleCount, setVisibleCount] = useState(25)

  const filteredLogs = useMemo(() => {
    const term = searchTerm.trim().toLowerCase()
    if (!term) return logs
    return logs.filter(
      (log) =>
        (log.question ?? '').toLowerCase().includes(term) ||
        (log.answer ?? '').toLowerCase().includes(term),
    )
  }, [logs, searchTerm])
  const visibleLogs = filteredLogs.slice(0, visibleCount)

  useEffect(() => {
    const fetchLogs = async () => {
      setLoading(true)
      try {
        const data = await apiFetch<RagChatLog[]>('/admin/rag-logs-list?limit=200')
        if (data && Array.isArray(data)) {
          setLogs(data)
        } else {
          console.error('Invalid data received:', data)
          setLogs([])
          setError('서버에서 올바르지 않은 데이터를 반환했습니다.')
        }
      } catch (e) {
        console.error('Failed to fetch logs:', e)
        setError('질문 로그를 불러오는데 실패했습니다.')
      } finally {
        setLoading(false)
      }
    }
    fetchLogs()
  }, [])

  return (
    <div className="admin-page-wrapper">
      <div className="admin-shell">
        <header className="admin-header glass-panel compact">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              <button 
                  className="ghost-btn" 
                  onClick={() => navigate('/admin/university')}
                  style={{ padding: '8px 12px', fontSize: '0.9rem' }}
              >
                ← 뒤로가기
              </button>
              <h1 className="admin-title compact" style={{ margin: 0 }}>전체 질문 로그</h1>
            </div>
            <button className="hero-btn hero-btn--primary" onClick={() => navigate('/')}>
              홈으로
            </button>
          </div>
        </header>

        <section className="admin-content glass-panel" style={{ marginTop: '20px', padding: '20px', maxHeight: 'calc(100vh - 150px)', overflowY: 'auto' }}>
          <input
            type="search"
            className="admin-input"
            placeholder="질문/답변 내용 검색"
            value={searchTerm}
            onChange={(e) => {
              setSearchTerm(e.target.value)
              setVisibleCount(25)
            }}
            style={{ marginBottom: '16px', width: '100%', maxWidth: '420px' }}
          />
          {loading ? (
            <div className="admin-table__empty">로딩 중...</div>
          ) : error ? (
            <div className="admin-alert admin-alert--danger">{error}</div>
          ) : filteredLogs.length === 0 ? (
            <div className="admin-table__empty">{searchTerm ? '검색 결과가 없습니다.' : '기록된 질문 로그가 없습니다.'}</div>
          ) : (
            <div className="admin-table">
              <div className="admin-table__head" style={{ gridTemplateColumns: '0.7fr 1fr 3fr 0.5fr' }}>
                <span>일시</span>
                <span>분류 / 상태</span>
                <span>대화 내용</span>
                <span style={{ textAlign: 'center' }}>참조</span>
              </div>
              <ul className="admin-table__body">
                {visibleLogs.map((log) => (
                  <li key={log.id} className="admin-table__row" style={{ gridTemplateColumns: '0.7fr 1fr 3fr 0.5fr', alignItems: 'start', padding: '16px 12px' }}>
                    <span style={{ opacity: 0.8, fontSize: '0.9rem' }}>{formatDateTime(log.created_at)}</span>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                      <span className="status-pill status-pill--pending" style={{ alignSelf: 'flex-start' }}>{log.route}</span>
                      {log.fallback_triggered && (
                        <span className="status-pill status-pill--danger" style={{ alignSelf: 'flex-start' }}>Fallback: {log.fallback_reason}</span>
                      )}
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                      <div style={{ display: 'flex', gap: '8px' }}>
                        <strong style={{ color: '#007AFF', minWidth: '24px' }}>Q.</strong>
                        <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: '0.95rem' }}>{log.question}</span>
                      </div>
                      <div style={{ display: 'flex', gap: '8px', backgroundColor: 'rgba(255,255,255,0.03)', padding: '10px', borderRadius: '6px' }}>
                        <strong style={{ color: '#34C759', minWidth: '24px' }}>A.</strong>
                        <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', opacity: 0.9, fontSize: '0.95rem' }}>{log.answer}</span>
                      </div>
                    </div>
                    <span style={{ textAlign: 'center', opacity: 0.8, fontSize: '0.9rem' }}>{log.source_count}개</span>
                  </li>
                ))}
              </ul>
              {visibleCount < filteredLogs.length && (
                <div style={{ textAlign: 'center', padding: '16px' }}>
                  <button className="ghost-btn" type="button" onClick={() => setVisibleCount((c) => c + 25)}>
                    더 보기 ({visibleCount}/{filteredLogs.length})
                  </button>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

export default ChatLogPage
