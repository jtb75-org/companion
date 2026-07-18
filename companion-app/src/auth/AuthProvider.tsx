import React, { createContext, useContext, useEffect, useState } from 'react'
import messaging from '@react-native-firebase/messaging'
import { api } from '../api/client'
import { authentikLogin, authentikLogout } from './authApi'
import {
  clearSessionToken,
  getSessionTokenSync,
  loadSessionToken,
  persistSessionToken,
} from './sessionToken'

interface AuthContextType {
  // True when a member has a live self-hosted (Authentik BFF) session.
  isAuthenticated: boolean
  loading: boolean
  // Authentik (self-hosted) username/password sign-in against POST /auth/login.
  signInWithPassword: (username: string, password: string) => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthContextType>({
  isAuthenticated: false,
  loading: true,
  signInWithPassword: async () => {},
  signOut: async () => {},
})

export function useAuth() {
  return useContext(AuthContext)
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // Authentik opaque session token (null when signed out).
  const [sessionToken, setSessionToken] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Session restore: load the token saved on the device (if any).
    let cancelled = false
    loadSessionToken()
      .then((token) => {
        if (!cancelled) setSessionToken(token)
      })
      .catch(() => {
        if (!cancelled) setSessionToken(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const signInWithPassword = async (username: string, password: string) => {
    // Authentik (self-hosted) sign-in. Throws AuthLoginError on failure.
    const token = await authentikLogin(username, password)
    await persistSessionToken(token)
    setSessionToken(token)
  }

  const signOut = async () => {
    try {
      const token = await messaging().getToken()
      await api('/api/v1/me/devices', {
        method: 'DELETE',
        body: JSON.stringify({ fcm_token: token }),
      })
    } catch (err) {
      console.log('[AuthProvider] failed to deactivate FCM token:', err)
    }

    try {
      await authentikLogout(getSessionTokenSync())
    } catch (err) {
      console.log('[AuthProvider] logout request failed:', err)
    }
    await clearSessionToken()
    setSessionToken(null)
  }

  const isAuthenticated = sessionToken !== null

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        loading,
        signInWithPassword,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}
