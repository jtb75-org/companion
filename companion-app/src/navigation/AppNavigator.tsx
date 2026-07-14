import React, { useEffect, useState } from 'react'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'
import { NavigationContainer } from '@react-navigation/native'
import { Text, ActivityIndicator, View } from 'react-native'
import { TodayScreen } from '../screens/TodayScreen'
import { ChatScreen } from '../screens/ChatScreen'
import { MyStuffScreen } from '../screens/MyStuffScreen'
import { ProfileScreen } from '../screens/ProfileScreen'
import { LoginScreen } from '../auth/LoginScreen'
import { AuthentikLoginScreen } from '../auth/AuthentikLoginScreen'
import { OnboardingScreen } from '../auth/OnboardingScreen'
import { useAuth } from '../auth/AuthProvider'
import { AUTH_PROVIDER } from '../auth/authConfig'
import { api } from '../api/client'
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
  const { user, isAuthenticated, loading } = useAuth()
  const [profileComplete, setProfileComplete] = useState<boolean | null>(null)
  usePushNotifications(profileComplete === true)

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
