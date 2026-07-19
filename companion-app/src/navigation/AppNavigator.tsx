import React, { useEffect, useState } from 'react'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'
import { NavigationContainer } from '@react-navigation/native'
import { Text, ActivityIndicator, View, Linking } from 'react-native'
import { TodayScreen } from '../screens/TodayScreen'
import { ChatScreen } from '../screens/ChatScreen'
import { MyStuffScreen } from '../screens/MyStuffScreen'
import { ProfileScreen } from '../screens/ProfileScreen'
import { AuthentikLoginScreen } from '../auth/AuthentikLoginScreen'
import { AuthentikActivateScreen } from '../auth/AuthentikActivateScreen'
import { OnboardingScreen } from '../auth/OnboardingScreen'
import { MemberOnlyScreen } from '../auth/MemberOnlyScreen'
import { useAuth } from '../auth/AuthProvider'
import { ActivationLink, parseActivationLink } from './linking'
import { api } from '../api/client'
import { resolveAppAccess } from '../auth/memberGate'
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
  const { isAuthenticated, loading, signOut } = useAuth()
  const [profileComplete, setProfileComplete] = useState<boolean | null>(null)
  // True when the signed-in person is a caregiver/admin on the member app — we
  // show the calm member-only gate instead of bouncing them to login.
  const [nonMember, setNonMember] = useState(false)
  // Pending account-activation link ({token, reset}) from an inbound /activate
  // deep link.
  const [activationLink, setActivationLink] = useState<ActivationLink | null>(null)
  usePushNotifications(profileComplete === true)

  // Handle the account-activation / password-reset universal / app link:
  //   https://app.mydailydignity.com/activate?token=...[&reset=1]
  // Cold start via getInitialURL, warm via the 'url' event.
  // `reset` only picks the screen's wording — both flavors route the same way.
  useEffect(() => {
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
      setNonMember(false)
      return
    }
    // Resolve who this signed-in person is and route accordingly. The decision
    // lives in resolveAppAccess (pure + unit-tested): it calls /api/v1/me and,
    // on a 401/403, disambiguates "non-member on the wrong app" from "genuinely
    // invalid session" via the role-aware /api/v1/auth/check.
    const checkProfile = async () => {
      setNonMember(false)
      const access = await resolveAppAccess(api)
      switch (access.kind) {
        case 'member':
          setProfileComplete(access.profileComplete)
          break
        case 'nonMember':
          // A caregiver/admin signed in on the member app. Show the calm gate —
          // do NOT signOut() into the login loop (that read as a broken login).
          setNonMember(true)
          break
        case 'invalid':
          // Genuinely invalid/expired session — clear it and return to sign-in.
          // Do NOT fall through to onboarding: complete-profile would 401 too and
          // trap the user.
          await signOut()
          break
        case 'unknown':
          // Non-auth error (network / 5xx): we can't determine the profile; surface
          // it as incomplete so the user isn't stranded on a spinner. (Completing the
          // profile is idempotent; a stale transient error at worst re-shows
          // onboarding once.)
          setProfileComplete(false)
          break
      }
    }
    checkProfile()
    // `isAuthenticated` drives the check (it flips true once the Authentik session
    // token is present). `signOut` is intentionally NOT a dependency: it's only
    // invoked in the 401/403 error path, not a trigger for the profile check, and it's
    // recreated each render — adding it would re-run this effect (and re-hit /me) on
    // every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated])

  if (loading) return null

  if (!isAuthenticated) {
    // A member who tapped their email link lands on "set your password" first —
    // for a first-time invite OR a password reset, which is the same screen with
    // reset-flavored copy. After they set it, AuthProvider signs them in and this
    // branch is left automatically. "Back to Sign In" clears the link.
    if (activationLink) {
      return (
        <AuthentikActivateScreen
          token={activationLink.token}
          reset={activationLink.reset}
          onBackToSignIn={() => setActivationLink(null)}
        />
      )
    }
    return <AuthentikLoginScreen />
  }

  // A caregiver/admin who signed in on the member app: show the calm member-only
  // gate (with a Sign Out) instead of a login-loop bounce. Checked before the
  // spinner because profileComplete stays null on this path.
  if (nonMember) {
    return <MemberOnlyScreen />
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
