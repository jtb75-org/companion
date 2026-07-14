import React, { createContext, useContext, useEffect, useState } from 'react'
import auth, { FirebaseAuthTypes } from '@react-native-firebase/auth'
import messaging from '@react-native-firebase/messaging'
import { GoogleSignin } from '@react-native-google-signin/google-signin'
import { api } from '../api/client'
import { AUTH_PROVIDER } from './authConfig'
import { authentikLogin, authentikLogout } from './authApi'
import {
  clearSessionToken,
  getSessionTokenSync,
  loadSessionToken,
  persistSessionToken,
} from './sessionToken'

interface AuthContextType {
  // Firebase user object (null when signed out, and always null in Authentik mode).
  user: FirebaseAuthTypes.User | null
  // Unified "is the member signed in?" flag that works for BOTH providers.
  isAuthenticated: boolean
  loading: boolean
  signInWithGoogle: () => Promise<void>
  signInWithEmail: (email: string, password: string) => Promise<void>
  registerWithEmail: (email: string, password: string) => Promise<void>
  // Authentik (self-hosted) username/password sign-in. Inert in Firebase mode.
  signInWithPassword: (username: string, password: string) => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  isAuthenticated: false,
  loading: true,
  signInWithGoogle: async () => {},
  signInWithEmail: async () => {},
  registerWithEmail: async () => {},
  signInWithPassword: async () => {},
  signOut: async () => {},
})

export function useAuth() {
  return useContext(AuthContext)
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<FirebaseAuthTypes.User | null>(null)
  // Authentik opaque session token (null when signed out / in Firebase mode).
  const [sessionToken, setSessionToken] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (AUTH_PROVIDER === 'authentik') {
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
    }

    // Firebase (default, live path) — unchanged behavior.
    GoogleSignin.configure({
      scopes: ['email', 'profile'],
    })
    const unsubscribe = auth().onAuthStateChanged((u) => {
      setUser(u)
      setLoading(false)
    })
    return unsubscribe
  }, [])

  const signInWithGoogle = async () => {
    await GoogleSignin.hasPlayServices()
    const signInResult = await GoogleSignin.signIn()
    const idToken = signInResult?.data?.idToken
    if (!idToken) throw new Error('No ID token')
    const credential = auth.GoogleAuthProvider.credential(idToken)
    await auth().signInWithCredential(credential)
  }

  const signInWithEmail = async (email: string, password: string) => {
    await auth().signInWithEmailAndPassword(email, password)
  }

  const registerWithEmail = async (email: string, password: string) => {
    await auth().createUserWithEmailAndPassword(email, password)
  }

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

    if (AUTH_PROVIDER === 'authentik') {
      try {
        await authentikLogout(getSessionTokenSync())
      } catch (err) {
        console.log('[AuthProvider] logout request failed:', err)
      }
      await clearSessionToken()
      setSessionToken(null)
      return
    }

    await auth().signOut()
  }

  const isAuthenticated = AUTH_PROVIDER === 'authentik' ? sessionToken !== null : user !== null

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated,
        loading,
        signInWithGoogle,
        signInWithEmail,
        registerWithEmail,
        signInWithPassword,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}
