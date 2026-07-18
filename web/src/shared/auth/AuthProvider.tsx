import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import {
  User,
  onAuthStateChanged,
  signInWithPopup,
  signInWithEmailAndPassword,
  signOut,
  createUserWithEmailAndPassword,
} from 'firebase/auth'
import { auth, googleProvider } from './firebase'
import { setCsrfToken, getCsrfToken } from '../api/client'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''
const AUTH_PROVIDER = import.meta.env.VITE_AUTH_PROVIDER || 'firebase'

type CaregiverUser = { user_id: string; contact_name: string; access_tier: string }

interface AuthContextType {
  user: User | null
  loading: boolean
  role: string | null        // "admin", "caregiver", "unauthorized", null (checking)
  adminRole: string | null   // "viewer", "editor", "admin"
  authorized: boolean | null // null = still checking, true/false = result
  profileComplete: boolean | null
  caregiverUsers: Array<CaregiverUser> | null
  loginWithGoogle: () => Promise<void>
  loginWithEmail: (email: string, password: string) => Promise<void>
  registerWithEmail: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  getToken: () => Promise<string | null>
}

const AuthContext = createContext<AuthContextType | null>(null)

function FirebaseAuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [role, setRole] = useState<string | null>(null)
  const [adminRole, setAdminRole] = useState<string | null>(null)
  const [authorized, setAuthorized] = useState<boolean | null>(null)
  const [profileComplete, setProfileComplete] = useState<boolean | null>(null)
  const [caregiverUsers, setCaregiverUsers] = useState<Array<CaregiverUser> | null>(null)
  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (u) => {
      setUser(u)
      setLoading(false)
    })
    return unsubscribe
  }, [])

  useEffect(() => {
    if (!user) {
      setRole(null)
      setAuthorized(null)
      setProfileComplete(null)
      setAdminRole(null)
      setCaregiverUsers(null)
      return
    }

    // Skip if already authorized (avoid re-check on token refresh)
    if (authorized !== null) return

    // Check authorization
    const checkAuth = async () => {
      try {
        const token = await user.getIdToken()
        const res = await fetch(
          `${API_BASE}/api/v1/auth/check`,
          { headers: { Authorization: `Bearer ${token}` } }
        )
        if (res.ok) {
          const data = await res.json()
          setRole(data.role)
          setAdminRole(data.admin_role || null)
          setAuthorized(true)
          setProfileComplete(data.profile_complete ?? true)
          setCaregiverUsers(data.has_charges ? [] : null)
        } else {
          setRole('unauthorized')
          setAuthorized(false)
        }
      } catch {
        setRole('unauthorized')
        setAuthorized(false)
      }
    }
    checkAuth()
  }, [user, authorized])

  const loginWithGoogle = async () => {
    await signInWithPopup(auth, googleProvider)
  }

  const loginWithEmail = async (email: string, password: string) => {
    await signInWithEmailAndPassword(auth, email, password)
  }

  const registerWithEmail = async (email: string, password: string) => {
    await createUserWithEmailAndPassword(auth, email, password)
  }

  const logout = async () => {
    await signOut(auth)
  }

  const getToken = async (): Promise<string | null> => {
    if (!user) return null
    return user.getIdToken()
  }

  return (
    <AuthContext.Provider value={{
      user, loading, role, adminRole, authorized, profileComplete, caregiverUsers,
      loginWithGoogle, loginWithEmail,
      registerWithEmail, logout, getToken,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

function AuthentikAuthProvider({ children }: { children: ReactNode }) {
  // `user` is a truthy object when a BFF cookie session exists. Consumers only
  // test truthiness, so a minimal { email } shape stands in for the Firebase User.
  const [user, setUser] = useState<{ email: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [role, setRole] = useState<string | null>(null)
  const [adminRole, setAdminRole] = useState<string | null>(null)
  const [authorized, setAuthorized] = useState<boolean | null>(null)
  const [profileComplete, setProfileComplete] = useState<boolean | null>(null)
  const [caregiverUsers, setCaregiverUsers] = useState<Array<CaregiverUser> | null>(null)

  // Drop all session state back to logged-out. Used on no-session, logout, and a
  // mid-session 401 (see the session-expired listener below). setState setters are
  // stable, so this is safe to reference from an effect without re-subscribing.
  // Resets the AUTHORIZATION/UI state, NOT the session's existence. Deliberately does
  // NOT clear the CSRF token: a pending caregiver's checkSession() 403s (not authorized
  // yet) and calls this, but their session cookie is still live and acceptAndEnter must
  // still send X-CSRF-Token. The token is cleared only where the SESSION truly ends —
  // logout and the session-expired listener.
  const clearSession = () => {
    setUser(null)
    setAuthorized(null)
    setRole(null)
    setAdminRole(null)
    setProfileComplete(null)
    setCaregiverUsers(null)
  }

  // Resolve the current session from the ambient cookie. Mirrors the Firebase
  // checkAuth field handling. A non-200 means "no session" → show login (NOT
  // AccessDenied), so we leave authorized=null rather than false.
  const checkSession = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/auth/check`, {
        credentials: 'include',
      })
      if (res.ok) {
        const data = await res.json()
        setRole(data.role)
        setAdminRole(data.admin_role || null)
        setAuthorized(true)
        setProfileComplete(data.profile_complete ?? true)
        setCaregiverUsers(data.has_charges ? [] : null)
        setUser({ email: data.email ?? '' })
        // Recover the double-submit CSRF token on a fresh load (the SPA can't read the
        // host-only cross-subdomain cookie); the API returns it in the check body.
        if (data.csrf_token) setCsrfToken(data.csrf_token)
      } else {
        clearSession()
      }
    } catch {
      clearSession()
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    checkSession()
  }, [])

  // The api client dispatches this when an authenticated request 401s (cookie
  // session expired mid-use). Clear state so the privileged shell can't linger;
  // ProtectedRoute then sends the user to /login.
  useEffect(() => {
    // Session is gone server-side — drop the CSRF token too (unlike a plain clearSession).
    const onExpired = () => {
      setCsrfToken(null)
      clearSession()
    }
    window.addEventListener('companion:session-expired', onExpired)
    return () => window.removeEventListener('companion:session-expired', onExpired)
  }, [])

  const loginWithEmail = async (email: string, password: string) => {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: email, password }),
      })
      if (res.ok) {
        // Store the CSRF token from the login body FIRST. A first-time caregiver invitee
        // is still PENDING, so the checkSession() below 403s and delivers nothing — but
        // acceptAndEnter must POST /invitations/accept with X-CSRF-Token right after. So
        // the token has to come from the login body, not the check.
        const data = await res.json().catch(() => ({}))
        if (data.csrf_token) setCsrfToken(data.csrf_token)
        await checkSession()
        return
      }
      if (res.status === 401) {
        throw new Error('Incorrect email or password.')
      }
      if (res.status === 403) {
        // Authenticated but not admitted (unverified email / inactive / identity
        // mismatch). Don't surface the backend's raw `detail` — those strings expose
        // internal auth architecture and read cold. One plain, warm message covers all.
        throw new Error(
          "Your account isn't able to sign in here. Please contact your administrator if you need help."
        )
      }
      throw new Error('Something went wrong. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const loginWithGoogle = async () => {
    throw new Error("Google sign-in isn't available here.")
  }

  const registerWithEmail = async () => {
    throw new Error('Accounts are created by invitation only.')
  }

  const logout = async () => {
    try {
      const headers: Record<string, string> = {}
      // Use the body-delivered token (getCsrfToken), NOT the host-only cookie — which the
      // SPA can't read cross-subdomain. Without it, logout's POST fails CSRF, the catch
      // swallows it, and the server-side Redis session is NEVER revoked (a reload could
      // silently re-authenticate).
      const csrf = getCsrfToken()
      if (csrf) {
        headers['X-CSRF-Token'] = csrf
      }
      await fetch(`${API_BASE}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
        headers,
      })
    } catch {
      // best-effort: swallow network errors, still clear local state
    }
    setCsrfToken(null)
    clearSession()
  }

  const getToken = async (): Promise<string | null> => null

  return (
    <AuthContext.Provider value={{
      user: user as unknown as User | null,
      loading, role, adminRole, authorized, profileComplete, caregiverUsers,
      loginWithGoogle, loginWithEmail,
      registerWithEmail, logout, getToken,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function AuthProvider({ children }: { children: ReactNode }) {
  return AUTH_PROVIDER === 'authentik' ? (
    <AuthentikAuthProvider>{children}</AuthentikAuthProvider>
  ) : (
    <FirebaseAuthProvider>{children}</FirebaseAuthProvider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}
