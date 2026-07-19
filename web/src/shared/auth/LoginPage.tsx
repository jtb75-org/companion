import { useState } from 'react'
import { useAuth } from './AuthProvider'
import { Navigate, Link } from 'react-router-dom'
import { BRAND_MID } from '../branding'
import { FORGOT_PASSWORD_COPY } from '../copy'

export default function LoginPage() {
  const { user, loading, role, authorized, loginWithEmail } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-companion-cream">
        <div className="text-companion-blue text-lg">Loading...</div>
      </div>
    )
  }

  if (user && authorized) {
    const destination = role === 'admin' ? '/ops' : '/caregiver/alerts'
    return <Navigate to={destination} replace />
  }

  if (user && authorized === false) {
    return <Navigate to="/unauthorized" replace />
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      await loginWithEmail(email, password)
    } catch (err: any) {
      setError(err.message || 'Authentication failed')
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-companion-cream">
      <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-md">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="text-4xl mb-2">&#x1F31F;</div>
          <h1 className="text-2xl font-bold text-companion-blue">{BRAND_MID}</h1>
          <p className="text-gray-500 text-sm mt-1">Dashboard Login</p>
        </div>

        {/* Email/Password */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-companion-blue focus:outline-none transition"
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full px-4 py-3 border-2 border-gray-200 rounded-xl focus:border-companion-blue focus:outline-none transition"
          />
          {error && (
            <p className="text-red-500 text-sm">{error}</p>
          )}
          <button
            type="submit"
            className="w-full bg-companion-blue text-white font-medium py-3 rounded-xl hover:bg-companion-blue-mid transition"
          >
            Sign In
          </button>
        </form>

        <p className="text-center text-sm mt-4">
          <Link to="/forgot-password" className="text-companion-blue hover:underline">
            {FORGOT_PASSWORD_COPY.loginLink}
          </Link>
        </p>
      </div>
    </div>
  )
}
