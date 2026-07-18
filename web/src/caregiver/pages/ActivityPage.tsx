import { useQuery } from '@tanstack/react-query'
import { api } from '../../shared/api/client'

interface ActivityEntry {
  id: string
  action: string
  timestamp: string
}

interface Props {
  userId: string
}

export function ActivityPage({ userId }: Props) {
  const { data: activity, isLoading, isError } = useQuery({
    queryKey: ['caregiver-activity', userId],
    // No try/catch fallback: a failed load must surface, not be replaced with fabricated
    // activity rows that imply the caregiver did things they never did.
    queryFn: () => api<ActivityEntry[]>(`/api/v1/caregiver/activity?user_id=${userId}`),
    enabled: !!userId,
  })

  if (isLoading) {
    return <p className="text-gray-500">Loading activity...</p>
  }

  if (isError) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-gray-900 mb-4">Your Activity</h1>
        <p className="text-sm text-red-600">
          We couldn't load your activity right now. Please refresh in a moment.
        </p>
      </div>
    )
  }

  const entries = Array.isArray(activity) ? activity : []

  return (
    <div>
      <h1 className="text-xl font-semibold text-gray-900 mb-4">Your Activity</h1>
      {entries.length === 0 ? (
        <p className="text-gray-500">No activity recorded yet.</p>
      ) : (
        <ul className="space-y-2">
          {entries.map((entry) => {
            const dt = new Date(entry.timestamp)
            const formatted = dt.toLocaleDateString('en-US', {
              month: 'long',
              day: 'numeric',
            }) + ' at ' + dt.toLocaleTimeString('en-US', {
              hour: 'numeric',
              minute: '2-digit',
            })
            return (
              <li
                key={entry.id}
                className="bg-white border border-gray-200 rounded-lg px-4 py-3 text-sm text-gray-700 shadow-sm"
              >
                {entry.action} on {formatted}.
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
