export type ChatSource = {
  source?: string | null
  sourceRef?: string | null
  source_ref?: string | null
  citationNumber?: number | null
  citation_number?: number | null
  chunkId?: string | null
  chunk_id?: string | null
  title?: string | null
  url?: string | null
  publishedAt?: string | null
  published_at?: string | null
  snippet?: string | null
  vectorScore?: number | null
  vector_score?: number | null
  sparseScore?: number | null
  sparse_score?: number | null
  hybridScore?: number | null
  hybrid_score?: number | null
  recencyScore?: number | null
  recency_score?: number | null
  finalScore?: number | null
  final_score?: number | null
}

/** Never merge by URL: a calendar URL can contain several independently cited events.
 * Keep whole records together; missing legacy identity gets its original position only.
 */
export const normalizeSources = (sources: ChatSource[]) => {
  const explicitNumber = (source: ChatSource) => {
    const number = source.citationNumber ?? source.citation_number
    return typeof number === 'number' && Number.isInteger(number) && number > 0 ? number : null
  }
  const hasNumbers = sources.some((source) => explicitNumber(source) !== null)
  return sources.map((source, index) => ({
    ...source,
    sourceRef: source.sourceRef ?? source.source_ref ?? null,
    citationNumber: explicitNumber(source) ?? (hasNumbers ? null : index + 1),
    chunkId: source.chunkId ?? source.chunk_id ?? null,
    publishedAt: source.publishedAt ?? source.published_at ?? null,
    vectorScore: source.vectorScore ?? source.vector_score ?? null,
    sparseScore: source.sparseScore ?? source.sparse_score ?? null,
    hybridScore: source.hybridScore ?? source.hybrid_score ?? null,
    recencyScore: source.recencyScore ?? source.recency_score ?? null,
    finalScore: source.finalScore ?? source.final_score ?? null,
  }))
}
