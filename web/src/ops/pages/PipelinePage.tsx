import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../shared/api/client'
import { Card } from '../../shared/components/Card'
import { StatusBadge } from '../../shared/components/StatusBadge'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PipelineStage {
  stage: string
  status: 'completed' | 'in_progress' | 'pending' | 'failed'
  duration_ms?: number
}

interface PipelineDocument {
  id: string
  user_name: string
  user_email: string
  source_channel: string
  status: string
  classification: string | null
  urgency_level: string | null
  card_summary: string | null
  created_at: string
  processed_at: string | null
  pipeline_stages: PipelineStage[]
}

interface DocumentsResponse {
  documents: PipelineDocument[]
  total: number
}

interface StageHealth {
  stage: string
  success_rate: number
  avg_time_ms: number
  status: 'healthy' | 'warning' | 'critical'
}

interface PipelineHealth {
  documents_in_flight: number
  stages: StageHealth[]
  recent_failures: { document_id: string; stage: string; error: string; timestamp: string }[]
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

type StatusFilter = 'all' | 'processing' | 'completed' | 'failed'

const PIPELINE_STAGE_ORDER = [
  'Ingestion',
  'Classification',
  'Extraction',
  'Summarization',
  'Embedding',
  'Routing',
]

// ---------------------------------------------------------------------------
// Pipeline Stepper
// ---------------------------------------------------------------------------

function PipelineStepper({ stages }: { stages: PipelineStage[] }) {
  // Build a map of stage status from the document's pipeline_stages
  const stageMap = new Map<string, PipelineStage>()
  stages.forEach((s) => stageMap.set(s.stage, s))

  return (
    <div className="flex items-center gap-0">
      {PIPELINE_STAGE_ORDER.map((name, i) => {
        const stage = stageMap.get(name)
        const status = stage?.status ?? 'pending'

        return (
          <div key={name} className="flex items-center">
            {/* Connector line */}
            {i > 0 && (
              <div
                className={`h-0.5 w-6 sm:w-8 ${
                  status === 'completed' ? 'bg-emerald-400' : 'bg-gray-200'
                }`}
              />
            )}
            {/* Stage dot + label */}
            <div className="flex flex-col items-center">
              <div
                className={`flex h-5 w-5 items-center justify-center rounded-full border-2 text-xs font-bold transition-all ${
                  status === 'completed'
                    ? 'border-emerald-500 bg-emerald-500 text-white'
                    : status === 'in_progress'
                      ? 'border-companion-blue bg-companion-blue/10 text-companion-blue animate-pulse'
                      : status === 'failed'
                        ? 'border-rose-500 bg-rose-500 text-white'
                        : 'border-gray-300 bg-white text-gray-300'
                }`}
              >
                {status === 'completed' ? (
                  <svg className="h-2.5 w-2.5" fill="none" viewBox="0 0 24 24" strokeWidth={3} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                ) : status === 'failed' ? (
                  <svg className="h-2.5 w-2.5" fill="none" viewBox="0 0 24 24" strokeWidth={3} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                ) : status === 'in_progress' ? (
                  <div className="h-1.5 w-1.5 rounded-full bg-companion-blue" />
                ) : (
                  <div className="h-1.5 w-1.5 rounded-full bg-gray-300" />
                )}
              </div>
              <span
                className={`mt-1 text-[10px] leading-tight whitespace-nowrap ${
                  status === 'completed'
                    ? 'text-emerald-600 font-medium'
                    : status === 'in_progress'
                      ? 'text-companion-blue font-medium'
                      : status === 'failed'
                        ? 'text-rose-600 font-medium'
                        : 'text-gray-400'
                }`}
              >
                {name}
              </span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Document Detail Panel
// ---------------------------------------------------------------------------

interface DocumentDetail {
  id: string
  user_name: string | null
  user_email: string | null
  source_channel: string | null
  status: string | null
  classification: string | null
  urgency_level: string | null
  confidence_score: number | null
  card_summary: string | null
  spoken_summary: string | null
  extracted_fields: Record<string, unknown> | null
  routing_destination: string | null
  page_count: number | null
  ocr_text: string | null
  source_metadata: Record<string, unknown> | null
  created_at: string | null
  processed_at: string | null
  pipeline_stages: {
    stage: string
    status: string
    duration_ms: number | null
    error_message: string | null
    metadata: Record<string, unknown> | null
    recorded_at: string | null
  }[]
}

function DetailPanel({ documentId, onClose }: { documentId: string; onClose: () => void }) {
  const { data: detail, isLoading } = useQuery({
    queryKey: ['pipeline-document-detail', documentId],
    queryFn: () => api<DocumentDetail>(`/admin/documents/${documentId}`),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 pt-12" onClick={onClose}>
      <div
        className="relative w-full max-w-2xl max-h-[80vh] overflow-y-auto rounded-xl bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 z-10 flex items-center justify-between border-b bg-white px-5 py-3 rounded-t-xl">
          <h2 className="text-sm font-semibold text-gray-900">Document Details</h2>
          <button onClick={onClose} className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600">
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-sm text-gray-500">Loading...</div>
        ) : !detail ? (
          <div className="p-8 text-center text-sm text-gray-500">Not found</div>
        ) : (
          <div className="space-y-4 p-5">
            {/* Overview */}
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <span className="text-gray-400">User</span>
                <p className="font-medium text-gray-900">{detail.user_name} ({detail.user_email})</p>
              </div>
              <div>
                <span className="text-gray-400">Source</span>
                <p className="font-medium text-gray-900">{detail.source_channel} &middot; {detail.page_count ?? 1} page(s)</p>
              </div>
              <div>
                <span className="text-gray-400">Classification</span>
                <p className="font-medium text-gray-900">
                  {detail.classification || 'none'}
                  {detail.confidence_score != null && (
                    <span className="ml-1 text-gray-400">({Math.round(detail.confidence_score * 100)}%)</span>
                  )}
                </p>
              </div>
              <div>
                <span className="text-gray-400">Urgency</span>
                <p className="font-medium text-gray-900">{detail.urgency_level || 'none'}</p>
              </div>
              <div>
                <span className="text-gray-400">Routing</span>
                <p className="font-medium text-gray-900">{detail.routing_destination || 'none'}</p>
              </div>
              <div>
                <span className="text-gray-400">Status</span>
                <p className="font-medium text-gray-900">{detail.status}</p>
              </div>
            </div>

            {/* Summaries */}
            {(detail.card_summary || detail.spoken_summary) && (
              <div>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Summaries</h3>
                {detail.card_summary && (
                  <div className="mb-2">
                    <span className="text-[10px] text-gray-400">Card</span>
                    <p className="text-xs text-gray-700">{detail.card_summary}</p>
                  </div>
                )}
                {detail.spoken_summary && (
                  <div>
                    <span className="text-[10px] text-gray-400">Spoken</span>
                    <p className="text-xs text-gray-700">{detail.spoken_summary}</p>
                  </div>
                )}
              </div>
            )}

            {/* Extracted Fields */}
            {detail.extracted_fields && Object.keys(detail.extracted_fields).length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Extracted Fields</h3>
                <div className="rounded-lg bg-gray-50 p-3">
                  <pre className="text-[11px] text-gray-700 whitespace-pre-wrap break-words">
                    {JSON.stringify(detail.extracted_fields, null, 2)}
                  </pre>
                </div>
              </div>
            )}

            {/* Pipeline Stages */}
            <div>
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Pipeline Stages</h3>
              <div className="space-y-1.5">
                {detail.pipeline_stages.map((s, i) => (
                  <div
                    key={i}
                    className={`rounded-lg border px-3 py-2 text-xs ${
                      s.status === 'error' ? 'border-rose-200 bg-rose-50' : 'border-gray-100 bg-gray-50'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className={`font-medium ${s.status === 'error' ? 'text-rose-700' : 'text-gray-700'}`}>
                        {s.stage}
                      </span>
                      <div className="flex items-center gap-2">
                        {s.duration_ms != null && (
                          <span className="text-gray-400">{s.duration_ms}ms</span>
                        )}
                        <span className={`font-medium ${s.status === 'error' ? 'text-rose-600' : 'text-emerald-600'}`}>
                          {s.status}
                        </span>
                      </div>
                    </div>
                    {s.error_message && (
                      <p className="mt-1 text-rose-600">{s.error_message}</p>
                    )}
                    {s.metadata && Object.keys(s.metadata).length > 0 && (
                      <p className="mt-1 text-gray-500">
                        {Object.entries(s.metadata).map(([k, v]) => `${k}: ${v}`).join(' · ')}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* OCR Text */}
            {detail.ocr_text && (
              <div>
                <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">OCR Text</h3>
                <div className="rounded-lg bg-gray-50 p-3 max-h-48 overflow-y-auto">
                  <pre className="text-[11px] text-gray-600 whitespace-pre-wrap break-words">{detail.ocr_text}</pre>
                </div>
              </div>
            )}

            {/* ID */}
            <p className="text-[10px] text-gray-300 select-all">{detail.id}</p>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Document Card
// ---------------------------------------------------------------------------

function DocumentCard({
  doc,
  onCancel,
  onResubmit,
  onShowDetails,
}: {
  doc: PipelineDocument
  onCancel: (id: string) => void
  onResubmit: (id: string) => void
  onShowDetails: (id: string) => void
}) {
  const sourceLabel = doc.source_channel === 'camera_scan' ? 'Camera Scan' : doc.source_channel === 'email' ? 'Email' : doc.source_channel || 'Unknown'
  const uploadTime = new Date(doc.created_at).toLocaleString()

  const urgencyColors: Record<string, string> = {
    high: 'bg-rose-100 text-rose-800',
    medium: 'bg-amber-100 text-amber-800',
    low: 'bg-emerald-100 text-emerald-800',
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white px-4 py-2.5 shadow-sm">
      <div className="flex items-center gap-3">
        {/* Left: Member info */}
        <div className="min-w-0 shrink-0 w-40">
          <p className="text-sm font-semibold text-gray-900 truncate">{doc.user_name}</p>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="inline-flex items-center rounded-full bg-gray-100 px-1.5 py-0 text-[10px] font-medium text-gray-600">
              {sourceLabel}
            </span>
            <span className="text-[10px] text-gray-400">{uploadTime}</span>
          </div>
        </div>

        {/* Stepper */}
        <div className="flex-1 flex justify-start overflow-x-auto">
          <PipelineStepper stages={doc.pipeline_stages ?? []} />
        </div>

        {/* Right: Badges + summary + actions */}
        <div className="shrink-0 flex items-center gap-3">
          <div className="flex flex-col items-end gap-0.5">
            <div className="flex items-center gap-1 flex-wrap justify-end">
              {doc.classification && (
                <span className="inline-flex items-center rounded-full bg-sky-100 px-2 py-0 text-[10px] font-medium text-sky-800">
                  {doc.classification}
                </span>
              )}
              {doc.urgency_level && (
                <span
                  className={`inline-flex items-center rounded-full px-2 py-0 text-[10px] font-medium ${urgencyColors[doc.urgency_level] || 'bg-gray-100 text-gray-700'}`}
                >
                  {doc.urgency_level}
                </span>
              )}
            </div>
            {doc.card_summary && (
              <p className="text-[11px] text-gray-500 text-right line-clamp-1 max-w-[200px]">{doc.card_summary}</p>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => onShowDetails(doc.id)}
              className="rounded border border-gray-300 px-2 py-0.5 text-[10px] font-medium text-gray-600 hover:bg-gray-50 transition-colors"
            >
              Details
            </button>
            <button
              onClick={() => onCancel(doc.id)}
              className="rounded border border-rose-300 px-2 py-0.5 text-[10px] font-medium text-rose-600 hover:bg-rose-50 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => onResubmit(doc.id)}
              className="rounded border border-companion-blue px-2 py-0.5 text-[10px] font-medium text-companion-blue hover:bg-companion-blue/5 transition-colors"
            >
              Resubmit
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Reprocess Button
// ---------------------------------------------------------------------------

function ReprocessButton() {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const handleReprocess = async () => {
    setBusy(true)
    setResult(null)
    try {
      const res = await api<{ reprocessed: number; results: { id: string; status: string; classification?: string; error?: string }[] }>(
        '/admin/workers/reprocess-documents',
        { method: 'POST' },
      )
      const summary = res.results
        .map((r) => (r.status === 'processed' ? `${r.classification}` : `failed: ${r.error}`))
        .join(', ')
      setResult(`${res.reprocessed} docs: ${summary}`)
      queryClient.invalidateQueries({ queryKey: ['pipeline-documents'] })
      queryClient.invalidateQueries({ queryKey: ['pipeline-health'] })
    } catch (e: any) {
      setResult(`Error: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center gap-3">
      <button
        onClick={handleReprocess}
        disabled={busy}
        className="rounded-lg bg-companion-blue px-4 py-2 text-sm font-medium text-white hover:bg-companion-blue-mid disabled:opacity-50 transition-colors"
      >
        {busy ? 'Reprocessing...' : 'Reprocess Stuck'}
      </button>
      {result && <p className="text-xs text-gray-500 max-w-xs truncate">{result}</p>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export function PipelinePage() {
  const queryClient = useQueryClient()
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [documents, setDocuments] = useState<PipelineDocument[]>([])
  const [detailDocId, setDetailDocId] = useState<string | null>(null)

  // --- Fetch ALL documents (unfiltered for counts) ---
  const { data: docsData, isLoading: docsLoading } = useQuery({
    queryKey: ['pipeline-documents'],
    queryFn: async () => {
      return api<DocumentsResponse>('/admin/documents?limit=50&offset=0')
    },
    refetchInterval: 5000,
  })

  // Sync fetched data into local state
  useEffect(() => {
    if (docsData?.documents) {
      setDocuments(docsData.documents)
    }
  }, [docsData])

  // --- Fetch pipeline health (stage health cards) ---
  // No placeholder/fallback. A failed health fetch must surface as an error, NOT
  // masquerade as "healthy 99%". The render distinguishes three real states: skeleton
  // while loading, an error card on failure, and real cards only once real data arrives.
  const {
    data: healthData,
    isLoading: healthLoading,
    isError: healthError,
  } = useQuery({
    queryKey: ['pipeline-health'],
    queryFn: async () => {
      const raw = await api<Record<string, unknown>>('/admin/pipeline/health')
      let stages: StageHealth[] = []
      if (Array.isArray(raw.stages)) {
        stages = raw.stages
      } else if (raw.stages && typeof raw.stages === 'object') {
        stages = Object.entries(raw.stages as Record<string, Record<string, unknown>>).map(
          ([name, s]) => ({
            stage: name.charAt(0).toUpperCase() + name.slice(1),
            success_rate: (s.success_rate as number) ?? 1,
            avg_time_ms: (s.avg_ms as number) ?? 0,
            status: ((s.success_rate as number) >= 0.95
              ? 'healthy'
              : (s.success_rate as number) >= 0.85
                ? 'warning'
                : 'critical') as StageHealth['status'],
          }),
        )
      }
      return {
        documents_in_flight: (raw.documents_in_flight as number) ?? 0,
        stages,
        recent_failures: Array.isArray(raw.recent_failures)
          ? (raw.recent_failures as PipelineHealth['recent_failures'])
          : [],
      }
    },
  })

  // --- Actions ---
  const handleCancel = async (id: string) => {
    if (!window.confirm('Cancel processing for this document?')) return
    try {
      await api(`/admin/documents/${id}/cancel`, { method: 'POST' })
      queryClient.invalidateQueries({ queryKey: ['pipeline-documents'] })
    } catch (e: any) {
      alert(`Failed to cancel: ${e.message}`)
    }
  }

  const handleResubmit = async (id: string) => {
    if (!window.confirm('Resubmit this document for processing?')) return
    try {
      await api(`/admin/documents/${id}/resubmit`, { method: 'POST' })
      queryClient.invalidateQueries({ queryKey: ['pipeline-documents'] })
    } catch (e: any) {
      alert(`Failed to resubmit: ${e.message}`)
    }
  }

  // --- Counts from full (unfiltered) list ---
  const isProcessing = (d: PipelineDocument) =>
    d.status === 'processing' || d.status === 'received' || d.status === 'classified' || d.status === 'summarized'
  const isCompleted = (d: PipelineDocument) =>
    d.status === 'routed' || d.status === 'pending_review' || d.status === 'acknowledged' || d.status === 'handled'
  const isFailed = (d: PipelineDocument) => d.status === 'failed'

  const countProcessing = documents.filter(isProcessing).length
  const countCompleted = documents.filter(isCompleted).length
  const countFailed = documents.filter(isFailed).length

  const filters: { key: StatusFilter; label: string; count: number }[] = [
    { key: 'all', label: 'All', count: documents.length },
    { key: 'processing', label: 'Processing', count: countProcessing },
    { key: 'completed', label: 'Completed', count: countCompleted },
    { key: 'failed', label: 'Failed', count: countFailed },
  ]

  // Client-side filter
  const filteredDocs = statusFilter === 'all'
    ? documents
    : statusFilter === 'processing'
      ? documents.filter(isProcessing)
      : statusFilter === 'completed'
        ? documents.filter(isCompleted)
        : documents.filter(isFailed)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-gray-900">Pipeline</h1>
          {/* Refresh indicator — the list polls /admin/documents every 5s */}
          <div className="flex items-center gap-1.5">
            <div
              className={`h-2 w-2 rounded-full ${!docsLoading ? 'bg-emerald-500 animate-pulse' : 'bg-amber-500'}`}
            />
            <span className={`text-xs font-medium ${!docsLoading ? 'text-emerald-600' : 'text-amber-600'}`}>
              {docsLoading ? 'Connecting' : 'Polling'}
            </span>
          </div>
        </div>
        <ReprocessButton />
      </div>

      {/* Pill filters */}
      <div className="flex items-center gap-2 flex-wrap">
        {filters.map(({ key, label, count }) => (
          <button
            key={key}
            onClick={() => setStatusFilter(key)}
            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ring-1 transition-colors ${
              statusFilter === key
                ? key === 'failed'
                  ? 'bg-rose-100 text-rose-800 ring-rose-300'
                  : key === 'processing'
                    ? 'bg-sky-100 text-sky-800 ring-sky-300'
                    : key === 'completed'
                      ? 'bg-emerald-100 text-emerald-800 ring-emerald-300'
                      : 'bg-companion-blue/10 text-companion-blue ring-companion-blue/30'
                : 'bg-white text-gray-500 ring-gray-200 hover:bg-gray-50'
            }`}
          >
            {label}
            <span className={`${statusFilter === key ? 'opacity-70' : 'text-gray-400'}`}>
              {count}
            </span>
          </button>
        ))}
      </div>

      {/* Document list */}
      {docsLoading ? (
        <p className="text-gray-500">Loading documents...</p>
      ) : filteredDocs.length === 0 ? (
        <div className="rounded-lg border border-gray-200 bg-white p-8 text-center text-sm text-gray-500">
          No documents found.
        </div>
      ) : (
        <div className="space-y-2">
          {filteredDocs.map((doc) => (
            <DocumentCard
              key={doc.id}
              doc={doc}
              onCancel={handleCancel}
              onResubmit={handleResubmit}
              onShowDetails={setDetailDocId}
            />
          ))}
        </div>
      )}

      {/* Stage health — skeleton while loading, error card on failure, real cards only
          once real data arrives. Never fabricated numbers. */}
      {healthLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {PIPELINE_STAGE_ORDER.map((name) => (
            <Card key={name} title={name}>
              <div className="animate-pulse space-y-2" aria-hidden>
                <div className="h-4 w-24 bg-gray-200 rounded" />
                <div className="h-2 bg-gray-200 rounded-full" />
              </div>
            </Card>
          ))}
        </div>
      ) : healthError ? (
        <Card title="Pipeline health">
          <p className="text-sm text-red-600">
            Couldn't load pipeline health metrics. This doesn't mean the pipeline is down —
            only that its health can't be read right now.
          </p>
        </Card>
      ) : healthData && healthData.stages.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {healthData.stages.map((stage) => (
            <Card key={stage.stage} title={stage.stage}>
              <div className="flex items-center justify-between mb-2">
                <StatusBadge status={stage.status} label={stage.status} />
                <span className="text-xs text-gray-400">{stage.avg_time_ms}ms avg</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="flex-1 bg-gray-200 rounded-full h-2">
                  <div
                    className={`h-2 rounded-full ${
                      stage.success_rate >= 0.95
                        ? 'bg-green-500'
                        : stage.success_rate >= 0.85
                          ? 'bg-yellow-500'
                          : 'bg-red-500'
                    }`}
                    style={{ width: `${stage.success_rate * 100}%` }}
                  />
                </div>
                <span className="text-sm text-gray-600 w-12 text-right">
                  {Math.round(stage.success_rate * 100)}%
                </span>
              </div>
            </Card>
          ))}
        </div>
      ) : null}

      {/* Recent failures — suppressed when health is erroring, so a failed refetch that
          retains stale data (TanStack v5 keeps `data` on refetch error) can't show the
          error card AND stale failures at once. */}
      {!healthError && healthData && healthData.recent_failures.length > 0 && (
        <Card title="Recent Failures">
          <div className="space-y-2">
            {healthData.recent_failures.map((f, i) => (
              <div key={i} className="border border-red-100 bg-red-50 rounded p-3 text-sm">
                <div className="flex justify-between">
                  <span className="font-medium text-red-800">{f.stage}</span>
                  <span className="text-red-400 text-xs">
                    {new Date(f.timestamp).toLocaleString()}
                  </span>
                </div>
                <p className="text-red-700 mt-1">{f.error}</p>
                <p className="text-red-400 text-xs mt-1">{f.document_id}</p>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Detail modal */}
      {detailDocId && (
        <DetailPanel documentId={detailDocId} onClose={() => setDetailDocId(null)} />
      )}
    </div>
  )
}
