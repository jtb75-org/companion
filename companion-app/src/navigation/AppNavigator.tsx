import React, { useEffect, useState } from 'react'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'
import { NavigationContainer } from '@react-navigation/native'
import { Text, ActivityIndicator, View, Linking } from 'react-native'
import { TodayScreen } from '../screens/TodayScreen'
import { ChatScreen } from '../screens/ChatScreen'
import { MyStuffScreen } from '../screens/MyStuffScreen'
import { ProfileScreen } from '../screens/ProfileScreen'
import { LoginScreen } from '../auth/LoginScreen'
import { AuthentikLoginScreen } from '../auth/AuthentikLoginScreen'
import { AuthentikActivateScreen } from '../auth/AuthentikActivateScreen'
import { OnboardingScreen } from '../auth/OnboardingScreen'
import { useAuth } from '../auth/AuthProvider'
import { AUTH_PROVIDER } from '../auth/authConfig'
import { ActivationLink, parseActivationLink } from './linking'
import { api, ApiError } from '../api/client'
import { usePushNotifications } from '../hooks/usePushNotifications'
import { colors } from '../theme/colors'

const Tab = createBottomTabNavigator()

function TabIcon({ label, focused }: { label: string; focused: boolean }) {
  const icons: Record<string, string> = {
    Today: '🏠',
    Chat: '💬',
    'My Stuff': '📋',
    Profile: '👤',
  }
  return (
    <Text style={{ fontSize: focused ? 22 : 20, opacity: focused ? 1 : 0.5 }}>
      {icons[label] || '•'}
    </Text>
  )
}

export function AppNavigator() {
  const { user, isAuthenticated, loading, signOut } = useAuth()
  const [profileComplete, setProfileComplete] = useState<boolean | null>(null)
  // Pending account-activation link ({token, reset}) from an inbound /activate
  // deep link. Authentik-only: in Firebase mode this stays null and the screen is
  // never shown, so the live path is completely unchanged.
  const [activationLink, setActivationLink] = useState<ActivationLink | null>(null)
  usePushNotifications(profileComplete === true)

  // Handle the account-activation / password-reset universal / app link:
  //   https://app.mydailydignity.com/activate?token=...[&reset=1]
  // Cold start via getInitialURL, warm via the 'url' event. Only acts under
  // AUTH_PROVIDER === 'authentik'; under firebase the link is ignored (inert).
  // `reset` only picks the screen's wording — both flavors route the same way.
  useEffect(() => {
    if (AUTH_PROVIDER !== 'authentik') return
    let cancelled = false

    const handleUrl = (url: string | null | undefined) => {
      const link = parseActivationLink(url)
      // Only set on a real activation link; leave other deep links alone.
      if (link && !cancelled) setActivationLink(link)
    }

    Linking.getInitialURL()
      .then((url) => handleUrl(url))
      .catch(() => {})
    const sub = Linking.addEventListener('url', ({ url }) => handleUrl(url))

    return () => {
      cancelled = true
      sub.remove()
    }
  }, [])

  useEffect(() => {
    if (!isAuthenticated) {
      setProfileComplete(null)
      return
    }
    // Check if user has a profile in our backend
    const checkProfile = async () => {
      try {
        const data = await api<{ exists?: boolean; profile_complete?: boolean; first_name?: string; last_name?: string }>('/api/v1/me')
        // Handle both structured response ({exists, profile_complete}) and raw user model ({first_name, last_name})
        if ('profile_complete' in data) {
          setProfileComplete(data.exists !== false && data.profile_complete === true)
        } else {
          setProfileComplete(Boolean(data.first_name && data.last_name))
        }
      } catch (err) {
        console.log('[AppNavigator] /api/v1/me error:', err)
        // A 401/403 means the session is invalid/expired — clear it and return to
        // sign-in. Do NOT fall through to setProfileComplete(false): that misroutes an
        // auth failure into the onboarding screen, whose complete-profile POST would then
        // 401 too, trapping the user.
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          await signOut()
          return
        }
        // Non-auth error (network / 5xx): we can't determine the profile; surface it as
        // incomplete so the user isn't stranded on a spinner. (Completing the profile is
        // idempotent; a stale transient error at worst re-shows onboarding once.)
        setProfileComplete(false)
      }
    }
    checkProfile()
    // `user` is kept in deps so Firebase mode re-checks on every auth-state
    // change exactly as before; `isAuthenticated` drives the Authentik path
    // (where `user` is always null).
  }, [isAuthenticated, user])

  if (loading) return null

  if (!isAuthenticated) {
    // A member who tapped their email link lands on "set your password" first
    // (Authentik only) — for a first-time invite OR a password reset, which is
    // the same screen with reset-flavored copy. After they set it, AuthProvider
    // signs them in and this branch is left automatically. "Back to Sign In"
    // clears the link.
    if (AUTH_PROVIDER === 'authentik' && activationLink) {
      return (
        <AuthentikActivateScreen
          token={activationLink.token}
          reset={activationLink.reset}
          onBackToSignIn={() => setActivationLink(null)}
        />
      )
    }
    return AUTH_PROVIDER === 'authentik' ? <AuthentikLoginScreen /> : <LoginScreen />
  }

  if (profileComplete === null) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.cream }}>
        <ActivityIndicator size="large" color={colors.blue} />
      </View>
    )
  }

  if (!profileComplete) {
    return <OnboardingScreen onComplete={() => setProfileComplete(true)} />
  }

  return (
    <NavigationContainer>
      <Tab.Navigator
        screenOptions={({ route }) => ({
          tabBarIcon: ({ focused }) => <TabIcon label={route.name} focused={focused} />,
          tabBarActiveTintColor: colors.blue,
          tabBarInactiveTintColor: colors.gray400,
          tabBarLabelStyle: { fontSize: 11, fontWeight: '600' },
          tabBarStyle: { paddingTop: 4, height: 84 },
          headerStyle: { backgroundColor: colors.white },
          headerTitleStyle: { fontWeight: '700', color: colors.gray900 },
        })}
      >
        <Tab.Screen name="Today" component={TodayScreen} />
        <Tab.Screen
          name="Chat"
          component={ChatScreen}
          options={{ title: 'D.D.' }}
        />
        <Tab.Screen name="My Stuff" component={MyStuffScreen} />
        <Tab.Screen name="Profile" component={ProfileScreen} />
      </Tab.Navigator>
    </NavigationContainer>
  )
}
