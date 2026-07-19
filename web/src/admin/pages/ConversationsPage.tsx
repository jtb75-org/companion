import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../shared/api/client'

/* ── Types ─────────────────────────────────────────────────── */

interface ConversationSummary {
  session_id: string
  user_name: string
  user_email: string
  started_at: string
  message_count: number
  duration_seconds: number | null
}

interface ConversationMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

interface ConversationDetail {
  session_id: string
  started_at: string
  ended_at: string | null
  message_count: number
  messages: ConversationMessage[]
}

/* ── Helpers ───────────────────────────────────────────────── */

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || isNaN(seconds) || seconds === 0) return '-'
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  if (m === 0) return `${s}s`
  return `${m}m ${s}s`
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString()
}

/* ── Export helper ─────────────────────────────────────────── */

async function downloadExport(dateFrom: string, dateTo: string) {
  const params = new URLSearchParams()
  if (dateFrom) params.set('date_from', dateFrom)
  if (dateTo) params.set('date_to', dateTo)

  const API_BASE = import.meta.env.VITE_API_BASE_URL || ''
  // Authenticated by the ambient Authentik session cookie (credentials:'include').
  // This is a GET, so no CSRF token is needed. It returns a file blob, so it can't go
  // through the shared api() client (which parses JSON) — but it must still send the
  // cookie, which an earlier raw fetch did not, so export always 401'd.
  const res = await fetch(
    `${API_BASE}/admin/conversations/export?${params.toString()}`,
    { credentials: 'include', headers: { 'Content-Type': 'application/json' } },
  )
  if (!res.ok) throw new Error(`Export failed: ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `conversations-export-${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(url)
}

/* ── Main Page ─────────────────────────────────────────────── */

export function ConversationsPage() {
  // Filters
  const [search, setSearch] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [offset, setOffset] = useState(0)
  const limit = 50

  // Expanded row
  const [expandedId, setExpandedId] = useState<string | null>(null)

  // Build query params
  const queryParams = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (search) queryParams.set('user_email', search)
  if (dateFrom) queryParams.set('date_from', dateFrom)
  if (dateTo) queryParams.set('date_to', dateTo)

  const { data, isLoading } = useQuery({
    queryKey: ['admin-conversations', offset, search, dateFrom, dateTo],
    queryFn: () =>
      api<{ conversations: ConversationSummary[]; total: number }>(
        `/admin/conversations?${queryParams.toString()}`,
      ),
  })

  const conversations = data?.conversations ?? []
  const total = data?.total ?? 0

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <h1 className="text-xl font-semibold text-gray-900">Conversations</h1>
        <button
          onClick={() => downloadExport(dateFrom, dateTo)}
          className="px-4 py-2 text-sm font-medium text-white bg-companion-blue rounded-lg hover:bg-companion-blue-mid transition"
        >
          Export JSON
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">From</label>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => { setDateFrom(e.target.value); setOffset(0) }}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-companion-blue focus:border-companion-blue"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">To</label>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => { setDateTo(e.target.value); setOffset(0) }}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-companion-blue focus:border-companion-blue"
          />
        </div>
        <div className="flex-1">
          <label className="block text-xs font-medium text-gray-500 mb-1">Search name or email</label>
          <input
            type="text"
            placeholder="Search..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setOffset(0) }}
            className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-companion-blue focus:border-companion-blue"
          />
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        {isLoading ? (
          <p className="px-4 py-12 text-center text-sm text-gray-500">Loading conversations...</p>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Member Name
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Email
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Started
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Messages
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Duration
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {conversations.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-sm text-gray-400">
                    No conversations found.
                  </td>
                </tr>
              )}
              {conversations.map((c) => (
                <ConversationRow
                  key={c.session_id}
                  conversation={c}
                  isExpanded={expandedId === c.session_id}
                  onToggle={() =>
                    setExpandedId(expandedId === c.session_id ? null : c.session_id)
                  }
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {total > limit && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-gray-500">
            Showing {offset + 1}-{Math.min(offset + limit, total)} of {total}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - limit))}
              disabled={offset === 0}
              className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition"
            >
              Previous
            </button>
            <button
              onClick={() => setOffset(offset + limit)}
              disabled={offset + limit >= total}
              className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 disabled:opacity-40 hover:bg-gray-50 transition"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

/* ── Row + expandable transcript ───────────────────────────── */

function ConversationRow({
  conversation,
  isExpanded,
  onToggle,
}: {
  conversation: ConversationSummary
  isExpanded: boolean
  onToggle: () => void
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className="hover:bg-gray-50 cursor-pointer"
      >
        <td className="px-4 py-3 text-sm text-gray-800">{conversation.user_name}</td>
        <td className="px-4 py-3 text-sm text-gray-600">{conversation.user_email}</td>
        <td className="px-4 py-3 text-sm text-gray-600 whitespace-nowrap">
          {formatDate(conversation.started_at)}
        </td>
        <td className="px-4 py-3 text-sm text-gray-800">{conversation.message_count}</td>
        <td className="px-4 py-3 text-sm text-gray-600 whitespace-nowrap">
          {formatDuration(conversation.duration_seconds)}
        </td>
      </tr>
      {isExpanded && (
        <tr>
          <td colSpan={5} className="p-0">
            <TranscriptDetail sessionId={conversation.session_id} />
          </td>
        </tr>
      )}
    </>
  )
}

/* ── Transcript detail panel ───────────────────────────────── */

function TranscriptDetail({ sessionId }: { sessionId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['admin-conversation-detail', sessionId],
    queryFn: () => api<ConversationDetail>(`/admin/conversations/${sessionId}`),
  })

  if (isLoading) {
    return (
      <div className="px-6 py-8 text-center text-sm text-gray-500 bg-gray-50">
        Loading transcript...
      </div>
    )
  }

  if (!data) return null

  return (
    <div className="bg-gray-50 border-t border-gray-200">
      {/* Session metadata */}
      <div className="px-6 py-3 flex flex-wrap gap-6 text-xs text-gray-500 border-b border-gray-200">
        <span>
          <span className="font-medium text-gray-700">Started:</span>{' '}
          {formatDate(data.started_at)}
        </span>
        {data.ended_at && (
          <span>
            <span className="font-medium text-gray-700">Ended:</span>{' '}
            {formatDate(data.ended_at)}
          </span>
        )}
        <span>
          <span className="font-medium text-gray-700">Messages:</span> {data.message_count}
        </span>
      </div>

      {/* Chat bubbles */}
      <div className="px-6 py-4 space-y-3 max-h-[500px] overflow-y-auto">
        {data.messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[75%] rounded-lg px-4 py-2 text-sm ${
                msg.role === 'user'
                  ? 'bg-companion-blue text-white'
                  : 'bg-white border border-gray-200 text-gray-800'
              }`}
            >
              <p className="whitespace-pre-wrap">{msg.content}</p>
              <p
                className={`text-[10px] mt-1 ${
                  msg.role === 'user' ? 'text-white/60' : 'text-gray-400'
                }`}
              >
                {formatDate(msg.created_at)}
              </p>
            </div>
          </div>
        ))}
        {data.messages.length === 0 && (
          <p className="text-center text-sm text-gray-400 py-4">No messages in this session.</p>
        )}
      </div>
    </div>
  )
}
