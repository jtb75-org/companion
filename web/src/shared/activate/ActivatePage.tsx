import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate, Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { api } from '../api/client'
import { BRAND_MID, BRAND_EMOJI } from '../branding'
import { RESET_PASSWORD_COPY } from '../copy'

interface ActivationInfo {
  valid: boolean
  email: string
  name: string
}

// Generic account-activation landing page: a newly-created account (e.g. an admin)
// follows the activation link, sets their password on the identity provider via the
// branded form, then is signed in. Reached only via activation emails (Authentik mode).
export default function ActivatePage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  // `?reset=1` marks a password-reset visit (from the forgot-password email). The
  // set-password action and endpoint are identical to activation — this only
  // swaps the copy from activation-flavored to reset-flavored.
  const isReset = searchParams.get('reset') === '1'
  const navigate = useNavigate()
  const { user, loading: authLoading, loginWithEmail } = useAuth()

  const [info, setInfo] = useState<ActivationInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [invalid, setInvalid] = useState(false)
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // Already signed in → nothing to activate.
  useEffect(() => {
    if (!authLoading && user) navigate('/', { replace: true })
  }, [authLoading, user, navigate])

  // Validate the activation token on mount.
  useEffect(() => {
    if (!token) {
      setInvalid(true)
      setLoading(false)
      return
    }
    api<ActivationInfo>(`/api/v1/activation/validate?token=${encodeURIComponent(token)}`)
      .then(setInfo)
      .catch(() => setInvalid(true))
      .finally(() => setLoading(false))
  }, [token])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (!token || !info) return
    setSubmitting(true)
    try {
      await api('/api/v1/activation/set-password', {
        method: 'POST',
        body: JSON.stringify({ token, password }),
      })
      // Password set on the identity provider — sign in with it, then land on the
      // home route which redirects to the role's dashboard. (No submitting reset on
      // success: we navigate away.)
      await loginWithEmail(info.email, password)
      navigate('/', { replace: true })
    } catch (err: any) {
      setError(err?.message || 'Could not set your password. Please try again.')
      setSubmitting(false)
    }
  }

  if (loading || authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-companion-cream">
        <div className="text-companion-blue text-lg">Loading...</div>
      </div>
    )
  }

  if (invalid) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-companion-cream">
        <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-md text-center">
          <div className="text-4xl mb-2">{BRAND_EMOJI}</div>
          <h1 className="text-2xl font-bold text-companion-blue">{BRAND_MID}</h1>
          <p className="text-gray-600 text-sm mt-4">
            {isReset
              ? RESET_PASSWORD_COPY.invalidLink
              : 'This activation link is invalid or has expired. Please ask an administrator to send you a new one.'}
          </p>
          {/* A dead reset link MUST offer a way out — the copy tells the member
              they can ask for a new link, so the screen has to let them. */}
          {isReset && (
            <Link
              to="/forgot-password"
              className="inline-block mt-4 text-companion-blue font-medium hover:underline"
            >
              {RESET_PASSWORD_COPY.invalidLinkAction}
            </Link>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-companion-cream">
      <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-md">
        <div className="text-center mb-8">
          <div className="text-4xl mb-2">{BRAND_EMOJI}</div>
          <h1 className="text-2xl font-bold text-companion-blue">{BRAND_MID}</h1>
          <p className="text-gray-500 text-sm mt-2">
            {isReset ? RESET_PASSWORD_COPY.title : 'Set up your account'}
          </p>
        </div>

        {info && (
          <p className="text-gray-600 text-sm text-center mb-4">
            {isReset ? (
              <>
                {RESET_PASSWORD_COPY.greetingPrefix} <strong>{info.name}</strong>.{' '}
                {RESET_PASSWORD_COPY.promptSuffix}
              </>
            ) : (
              <>
                Welcome, <strong>{info.name}</strong>. Create a password to finish
                setting up your account.
              </>
            )}
          </p>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="email"
            value={info?.email ?? ''}
            readOnly
            disabled
            className="w-full px-4 py-3 border-2 border-gray-200 rounded-xl bg-gray-50 text-gray-500"
          />
          <input
            type="password"
            placeholder={isReset ? RESET_PASSWORD_COPY.passwordPlaceholder : 'Create a password'}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={10}
            className="w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-companion-blue focus:outline-none transition"
          />
          {error && <p className="text-red-500 text-sm">{error}</p>}
          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-companion-blue text-white font-medium py-3 rounded-xl hover:bg-companion-blue-mid transition disabled:opacity-60"
          >
            {submitting
              ? isReset
                ? RESET_PASSWORD_COPY.submitting
                : 'Setting up…'
              : isReset
                ? RESET_PASSWORD_COPY.submit
                : 'Set password & continue'}
          </button>
        </form>
      </div>
    </div>
  )
}
