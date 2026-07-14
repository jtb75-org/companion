import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { api } from '../api/client'
import { BRAND_MID, BRAND_EMOJI } from '../branding'

interface InvitationInfo {
  valid: boolean
  contact_name: string
  member_name: string
  relationship_type: string
  access_tier: string
}

export default function AcceptInvitationPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const navigate = useNavigate()
  const { user, loading: authLoading, loginWithGoogle } = useAuth()

  const [invitation, setInvitation] = useState<InvitationInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [accepting, setAccepting] = useState(false)

  // Validate the token on mount
  useEffect(() => {
    if (!token) {
      navigate('/invite/expired', { replace: true })
      return
    }
    api<InvitationInfo>(`/api/v1/invitations/validate?token=${encodeURIComponent(token)}`)
      .then(setInvitation)
      .catch(() => navigate('/invite/expired', { replace: true }))
      .finally(() => setLoading(false))
  }, [token, navigate])

  // Once user is signed in, accept the invitation
  useEffect(() => {
    if (!user || !token || !invitation || accepting) return

    setAccepting(true)
    api('/api/v1/invitations/accept', {
      method: 'POST',
      body: JSON.stringify({ token }),
    })
      .then(() => navigate('/caregiver/alerts', { replace: true }))
      .catch(() => {
        setError('Failed to accept invitation. It may have expired.')
        setAccepting(false)
      })
  }, [user, token, invitation, navigate, accepting])

  if (loading || authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-companion-cream">
        <div className="text-companion-blue text-lg">Loading...</div>
      </div>
    )
  }

  if (accepting) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-companion-cream">
        <div className="text-companion-blue text-lg">Accepting invitation...</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-companion-cream">
      <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-md">
        <div className="text-center mb-8">
          <div className="text-4xl mb-2">{BRAND_EMOJI}</div>
          <h1 className="text-2xl font-bold text-companion-blue">{BRAND_MID}</h1>
          <p className="text-gray-500 text-sm mt-2">Caregiver Invitation</p>
        </div>

        {invitation && (
          <div className="bg-blue-50 rounded-xl p-4 mb-6">
            <p className="text-gray-700">
              You've been invited as a <strong>{invitation.relationship_type.replace('_', ' ')}</strong> for{' '}
              <strong>{invitation.member_name}</strong>.
            </p>
            <p className="text-gray-500 text-sm mt-2">
              Access level: Tier {invitation.access_tier.replace('tier_', '')}
            </p>
          </div>
        )}

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-3 mb-4">
            <p className="text-red-600 text-sm">{error}</p>
          </div>
        )}

        {!user && (
          <>
            <p className="text-gray-600 text-sm text-center mb-4">
              Sign in with your Google account to accept this invitation.
            </p>
            <button
              onClick={loginWithGoogle}
              className="w-full flex items-center justify-center gap-3 bg-white border-2 border-gray-200 rounded-xl px-4 py-3 text-gray-700 font-medium hover:bg-gray-50 transition"
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/>
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
              </svg>
              Sign in with Google
            </button>
          </>
        )}
      </div>
    </div>
  )
}
