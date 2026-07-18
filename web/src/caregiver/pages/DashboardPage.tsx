import { useQuery } from '@tanstack/react-query'
import { api } from '../../shared/api/client'
import { Card } from '../../shared/components/Card'
import { StatusBadge } from '../../shared/components/StatusBadge'

interface DashboardData {
  status: 'managing_well' | 'needs_attention'
  tasks: { completed: number; total: number }
  medication_adherence: number
  upcoming_bills: { description: string; due_date: string; amount: string }[]
  upcoming_appointments: { description: string; date: string }[]
}

interface Props {
  userId: string
}

export function DashboardPage({ userId }: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['caregiver-dashboard', userId],
    // No try/catch fallback: a failed load must surface, NOT be replaced with a calm
    // fabricated "Managing Well" state a caregiver could act on. See the isError branch.
    queryFn: () => api<DashboardData>(`/api/v1/caregiver/dashboard?user_id=${userId}`),
    enabled: !!userId,
  })

  if (isLoading) {
    return <p className="text-gray-500">Loading dashboard...</p>
  }

  // Never fabricate care data. If the dashboard can't load, say so plainly and show no
  // status — a false "all is well" is the most dangerous thing to put in front of a
  // caregiver.
  if (isError || !data) {
    return (
      <div className="space-y-6">
        <h1 className="text-xl font-semibold text-gray-900">Caregiver Dashboard</h1>
        <Card title="Dashboard unavailable">
          <p className="text-sm text-red-600">
            We couldn't load this dashboard right now, so we're not showing a status.
            This doesn't necessarily mean anything is wrong — only that the latest
            information couldn't be reached. Please refresh in a moment.
          </p>
        </Card>
      </div>
    )
  }

  const raw = data
  const dashboard = {
    status: raw.status,
    tasks: raw.tasks ?? { completed: 0, total: 0 },
    medication_adherence: raw.medication_adherence ?? 0,
    upcoming_bills: Array.isArray(raw.upcoming_bills)
      ? raw.upcoming_bills : [],
    upcoming_appointments: Array.isArray(raw.upcoming_appointments)
      ? raw.upcoming_appointments : [],
  }

  const adherencePct = Math.round(dashboard.medication_adherence * 100)

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-gray-900">Caregiver Dashboard</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {/* Status — only render a verdict the data actually reported. A missing/unknown
            status must NOT default to either "Managing Well" (false calm) or "Needs
            Attention" (false alarm). */}
        <Card title="Status">
          {dashboard.status === 'managing_well' ? (
            <StatusBadge status="healthy" label="Managing Well" />
          ) : dashboard.status === 'needs_attention' ? (
            <StatusBadge status="warning" label="Needs Attention" />
          ) : (
            <span className="text-sm text-gray-500">Status unavailable</span>
          )}
        </Card>

        {/* Tasks */}
        <Card title="Tasks" subtitle={`${dashboard.tasks.completed} of ${dashboard.tasks.total} completed`}>
          <div className="w-full bg-gray-200 rounded-full h-2.5">
            <div
              className="bg-companion-sage h-2.5 rounded-full"
              style={{ width: `${dashboard.tasks.total > 0 ? (dashboard.tasks.completed / dashboard.tasks.total) * 100 : 0}%` }}
            />
          </div>
        </Card>

        {/* Medication Adherence */}
        <Card title="Medication Adherence" subtitle={`${adherencePct}%`}>
          <div className="w-full bg-gray-200 rounded-full h-2.5">
            <div
              className={`h-2.5 rounded-full ${adherencePct >= 80 ? 'bg-companion-sage' : 'bg-companion-amber'}`}
              style={{ width: `${adherencePct}%` }}
            />
          </div>
        </Card>
      </div>

      {/* Overdue Bills */}
      {(raw as any).overdue_bills_list?.length > 0 && (
        <Card title="Overdue Bills">
          <ul className="space-y-2">
            {(raw as any).overdue_bills_list.map((bill: any, i: number) => (
              <li key={i} className="flex justify-between text-sm">
                <span className="font-medium text-red-700">{bill.description}</span>
                <span className="text-red-600 font-medium">
                  {bill.amount} &middot; due {new Date(bill.due_date + 'Z').toLocaleDateString()}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Upcoming Bills */}
        <Card title="Upcoming Bills">
          {dashboard.upcoming_bills.length === 0 ? (
            <p className="text-sm text-gray-500">No upcoming bills.</p>
          ) : (
            <ul className="space-y-2">
              {dashboard.upcoming_bills.map((bill, i) => (
                <li key={i} className="flex justify-between text-sm">
                  <span className="text-gray-700">{bill.description}</span>
                  <span className="text-gray-500">
                    {bill.amount} &middot; {new Date(bill.due_date).toLocaleDateString()}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* Upcoming Appointments */}
        <Card title="Upcoming Appointments">
          {dashboard.upcoming_appointments.length === 0 ? (
            <p className="text-sm text-gray-500">No upcoming appointments.</p>
          ) : (
            <ul className="space-y-2">
              {dashboard.upcoming_appointments.map((appt, i) => (
                <li key={i} className="flex justify-between text-sm">
                  <span className="text-gray-700">{appt.description}</span>
                  <span className="text-gray-500">{new Date(appt.date).toLocaleDateString()}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* Recent Documents */}
      {(raw as any).recent_documents?.length > 0 && (
        <Card title="Recent Documents">
          <ul className="space-y-3">
            {(raw as any).recent_documents.map((doc: any, i: number) => {
              const statusLabels: Record<string, { text: string; color: string }> = {
                pending: { text: 'Waiting for review', color: 'text-amber-600' },
                presented: { text: 'In review', color: 'text-blue-600' },
                confirmed: { text: 'Reviewed and added', color: 'text-green-600' },
                skipped: { text: 'Skipped', color: 'text-gray-400' },
                auto_created: { text: 'Added automatically', color: 'text-gray-500' },
              }
              const statusInfo = statusLabels[doc.review_status] || { text: doc.review_status, color: 'text-gray-500' }
              return (
                <li key={i} className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-gray-700 truncate">
                      {doc.card_summary || doc.source_description}
                    </p>
                    <p className="text-xs text-gray-400">
                      {doc.classification} · {doc.source_description}
                      {doc.created_at && ` · ${new Date(doc.created_at).toLocaleDateString()}`}
                    </p>
                  </div>
                  <span className={`text-xs font-medium whitespace-nowrap ${statusInfo.color}`}>
                    {statusInfo.text}
                  </span>
                </li>
              )
            })}
          </ul>
        </Card>
      )}
    </div>
  )
}
