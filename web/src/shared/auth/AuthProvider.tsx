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

// Read a cookie value by name from document.cookie (Authentik CSRF token).
function readCookie(name: string): string | null {
  const match = document.cookie.match(
    new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()[\]\\/+^])/g, '\\$1') + '=([^;]*)')
  )
  return match ? decodeURIComponent(match[1]) : null
}

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
      } else {
        setUser(null)
        setAuthorized(null)
        setRole(null)
        setAdminRole(null)
        setProfileComplete(null)
        setCaregiverUsers(null)
      }
    } catch {
      setUser(null)
      setAuthorized(null)
      setRole(null)
      setAdminRole(null)
      setProfileComplete(null)
      setCaregiverUsers(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    checkSession()
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
        await checkSession()
        return
      }
      if (res.status === 401) {
        throw new Error('Incorrect email or password.')
      }
      if (res.status === 403) {
        let detail = ''
        try {
          const body = await res.json()
          detail = body?.detail || ''
        } catch {
          // ignore parse errors
        }
        throw new Error(detail || "Your account isn't able to sign in here.")
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
      const csrf = readCookie('companion_csrf')
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
    setUser(null)
    setAuthorized(null)
    setRole(null)
    setAdminRole(null)
    setProfileComplete(null)
    setCaregiverUsers(null)
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
