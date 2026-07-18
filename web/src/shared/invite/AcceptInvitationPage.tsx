import { useEffect, useRef, useState } from 'react'
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
  contact_email: string
  needs_password_setup: boolean
}

export default function AcceptInvitationPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const navigate = useNavigate()
  const { user, loading: authLoading, loginWithEmail } = useAuth()

  const [invitation, setInvitation] = useState<InvitationInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [accepting, setAccepting] = useState(false)

  // Inline email/password login (Authentik mode). On success the session cookie is set
  // and the handler calls acceptAndEnter() directly with the token still in the URL.
  const [loginEmail, setLoginEmail] = useState('')
  const [loginPassword, setLoginPassword] = useState('')
  const [loginError, setLoginError] = useState('')

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoginError('')
    try {
      await loginWithEmail(loginEmail, loginPassword)
      // The session cookie now exists; accept directly rather than waiting on `user`
      // (a pending invitee's /auth/check 403s, so `user` would never arrive).
      await acceptAndEnter()
    } catch (err: any) {
      setLoginError(err.message || 'Sign in failed. Please try again.')
    }
  }

  // First-time invitee (Authentik mode): set a password, sign in with it to mint the
  // session cookie, then acceptAndEnter() directly. The email is fixed to the invited
  // address.
  const [newPassword, setNewPassword] = useState('')
  const [setupError, setSetupError] = useState('')
  const [settingUp, setSettingUp] = useState(false)

  const handleCreatePassword = async (e: React.FormEvent) => {
    e.preventDefault()
    setSetupError('')
    if (!invitation || !token) return
    setSettingUp(true)
    try {
      await api('/api/v1/invitations/set-password', {
        method: 'POST',
        body: JSON.stringify({ token, password: newPassword }),
      })
      // Password set on the identity provider — sign in with it to establish the session
      // cookie, then accept directly. We do NOT wait for `user`: a first-time invitee is
      // a PENDING caregiver, so /auth/check 403s and `user` never becomes truthy —
      // acceptAndEnter is what flips them active. (No settingUp reset on success: it
      // reloads into the dashboard.)
      await loginWithEmail(invitation.contact_email, newPassword)
      await acceptAndEnter()
    } catch (err: any) {
      setSetupError(err?.message || 'Could not set your password. Please try again.')
      setSettingUp(false)
    }
  }

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

  // Accept the invitation, then ENTER the dashboard.
  //
  // This must NOT be gated on `user` becoming truthy. A first-time invitee is a PENDING
  // caregiver until this call runs, so /auth/check (authorize_by_email) returns 403 and
  // `user` never populates — gating accept on `user` deadlocked the flow (accept was the
  // only thing that could make the caregiver authorized). So the login handlers call this
  // directly once the session cookie exists, regardless of `user`.
  //
  // After accept succeeds the session is an ACTIVE caregiver, but AuthProvider still holds
  // the stale `authorized=false` from the login-time check. A hard reload re-runs the
  // session check so the now-authorized role is picked up and the caregiver dashboard
  // renders; an SPA navigate() would race that stale state and bounce to /login.
  const acceptFiredRef = useRef(false)
  const acceptAndEnter = async () => {
    if (!token || acceptFiredRef.current) return
    acceptFiredRef.current = true
    setAccepting(true)
    try {
      await api('/api/v1/invitations/accept', {
        method: 'POST',
        body: JSON.stringify({ token }),
      })
      window.location.assign('/caregiver/alerts')
    } catch {
      acceptFiredRef.current = false
      setError('We could not finish setting up your access. The invitation may have expired.')
      setAccepting(false)
      setSettingUp(false)
    }
  }

  // A caregiver who is ALREADY signed in (e.g. accepting a second charge) lands here with
  // `user` already truthy — accept on mount. First-time invitees never hit this branch
  // (their login leaves `user` null); their login handlers call acceptAndEnter directly.
  useEffect(() => {
    if (user && token && invitation) acceptAndEnter()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, token, invitation])

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

        {!user && invitation?.needs_password_setup && (
          <>
            <p className="text-gray-600 text-sm text-center mb-4">
              Create a password to accept this invitation.
            </p>
            <form onSubmit={handleCreatePassword} className="space-y-4">
              <input
                type="email"
                value={invitation.contact_email}
                readOnly
                disabled
                className="w-full px-4 py-3 border-2 border-gray-200 rounded-xl bg-gray-50 text-gray-500"
              />
              <input
                type="password"
                placeholder="Create a password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                minLength={10}
                disabled={settingUp}
                className="w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-companion-blue focus:outline-none transition disabled:opacity-60 disabled:bg-gray-50"
              />
              {setupError && (
                <p className="text-red-500 text-sm">{setupError}</p>
              )}
              <button
                type="submit"
                disabled={settingUp}
                className="w-full bg-companion-blue text-white font-medium py-3 rounded-xl hover:bg-companion-blue-mid transition disabled:opacity-60"
              >
                {settingUp ? 'Setting up…' : 'Create password & continue'}
              </button>
            </form>
          </>
        )}

        {!user && !invitation?.needs_password_setup && (
          <>
            <p className="text-gray-600 text-sm text-center mb-4">
              Sign in to accept this invitation.
            </p>
            <form onSubmit={handleLogin} className="space-y-4">
              <input
                type="email"
                placeholder="Email"
                value={loginEmail}
                onChange={(e) => setLoginEmail(e.target.value)}
                className="w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-companion-blue focus:outline-none transition"
              />
              <input
                type="password"
                placeholder="Password"
                value={loginPassword}
                onChange={(e) => setLoginPassword(e.target.value)}
                className="w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-companion-blue focus:outline-none transition"
              />
              {loginError && (
                <p className="text-red-500 text-sm">{loginError}</p>
              )}
              <button
                type="submit"
                className="w-full bg-companion-blue text-white font-medium py-3 rounded-xl hover:bg-companion-blue-mid transition"
              >
                Sign In
              </button>
            </form>
          </>
        )}

      </div>
    </div>
  )
}
