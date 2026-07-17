import { useState } from 'react'
import { Link } from 'react-router-dom'
import { forgotPassword } from '../api/client'
import { BRAND_MID, BRAND_EMOJI } from '../branding'
import { FORGOT_PASSWORD_COPY as COPY } from '../copy'

// Password-reset request page. Submitting an email calls POST /auth/forgot-password,
// which ALWAYS succeeds for a valid request shape (anti-enumeration). On any 2xx we
// show a single generic "check your email" card that never reveals whether the
// address exists. 429 → wait message; 422/network → generic error.
export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      await forgotPassword(email)
      // Any success → the same generic confirmation, regardless of whether the
      // email matched an account.
      setSent(true)
    } catch (err: any) {
      if (err?.status === 429) {
        setError(COPY.rateLimited)
      } else {
        // 422 (invalid email) and network/other errors collapse to one generic
        // message — we never surface account-state detail here.
        setError(COPY.genericError)
      }
      setSubmitting(false)
    }
  }

  if (sent) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-companion-cream">
        <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-md text-center">
          <div className="text-4xl mb-2">{BRAND_EMOJI}</div>
          <h1 className="text-2xl font-bold text-companion-blue">{BRAND_MID}</h1>
          <p className="text-gray-700 font-medium mt-4">{COPY.sentTitle}</p>
          <p className="text-gray-600 text-sm mt-2">{COPY.sentBody}</p>
          <Link
            to="/login"
            className="inline-block text-companion-blue text-sm hover:underline mt-6"
          >
            {COPY.backToLogin}
          </Link>
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
          <p className="text-gray-500 text-sm mt-2">{COPY.title}</p>
        </div>

        <p className="text-gray-600 text-sm text-center mb-4">{COPY.prompt}</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="email"
            placeholder={COPY.emailPlaceholder}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-companion-blue focus:outline-none transition"
          />
          {error && <p className="text-red-500 text-sm">{error}</p>}
          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-companion-blue text-white font-medium py-3 rounded-xl hover:bg-companion-blue-mid transition disabled:opacity-60"
          >
            {submitting ? COPY.submitting : COPY.submit}
          </button>
        </form>

        <p className="text-center text-sm text-gray-400 mt-4">
          <Link to="/login" className="text-companion-blue hover:underline">
            {COPY.backToLogin}
          </Link>
        </p>
      </div>
    </div>
  )
}
