import React from 'react'
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, ActivityIndicator, KeyboardAvoidingView, Platform,
} from 'react-native'
import { useAuth } from './AuthProvider'
import { AuthLoginError } from './authApi'
import { authStrings } from './authStrings'
import { colors, brand } from '../theme/colors'

/**
 * Username/password login for the self-hosted (Authentik) path.
 *
 * Only rendered when AUTH_PROVIDER === 'authentik'. In Firebase mode this
 * screen is never mounted and the app behaves exactly as before.
 *
 * All user-facing copy comes from `authStrings` so it can be reviewed in one
 * place. Errors are mapped from the HTTP status to calm, plain-language text.
 */
export function AuthentikLoginScreen() {
  const { signInWithPassword, loading } = useAuth()
  const [username, setUsername] = React.useState('')
  const [password, setPassword] = React.useState('')
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)

  const messageForStatus = (status: number | null): string => {
    switch (status) {
      case 401:
        return authStrings.errorBadCredentials
      case 403:
        return authStrings.errorNotAllowed
      case 429:
        return authStrings.errorTooManyTries
      default:
        return authStrings.errorGeneric
    }
  }

  const handleSignIn = async () => {
    if (!username.trim() || !password.trim()) {
      setError(authStrings.missingFields)
      return
    }
    setError('')
    setBusy(true)
    try {
      await signInWithPassword(username.trim(), password)
      // On success the AuthProvider flips isAuthenticated, and AppNavigator
      // routes into the app exactly like a successful Firebase login.
    } catch (e) {
      const status = e instanceof AuthLoginError ? e.status : null
      setError(messageForStatus(status))
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.blue} />
      </View>
    )
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={styles.card}>
        <Text style={styles.emoji}>{brand.emoji}</Text>
        <Text style={styles.title}>{authStrings.title}</Text>
        <Text style={styles.subtitle}>{authStrings.subtitle}</Text>

        <TextInput
          style={styles.input}
          placeholder={authStrings.usernamePlaceholder}
          placeholderTextColor={colors.gray400}
          value={username}
          onChangeText={setUsername}
          autoCapitalize="none"
          autoCorrect={false}
          textContentType="username"
        />
        <TextInput
          style={styles.input}
          placeholder={authStrings.passwordPlaceholder}
          placeholderTextColor={colors.gray400}
          value={password}
          onChangeText={setPassword}
          autoCapitalize="none"
          secureTextEntry
          textContentType="password"
        />

        <TouchableOpacity
          style={styles.button}
          onPress={handleSignIn}
          disabled={busy}
          accessibilityRole="button"
          accessibilityLabel={authStrings.signInButton}
        >
          {busy ? (
            <ActivityIndicator color={colors.white} />
          ) : (
            <Text style={styles.buttonText}>{authStrings.signInButton}</Text>
          )}
        </TouchableOpacity>

        {error ? (
          <Text style={styles.error} accessibilityLiveRegion="polite">
            {error}
          </Text>
        ) : null}
      </View>
    </KeyboardAvoidingView>
  )
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: colors.cream,
    padding: 24,
  },
  center: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: colors.cream,
  },
  card: {
    backgroundColor: colors.white,
    borderRadius: 20,
    padding: 32,
    width: '100%',
    maxWidth: 360,
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.08,
    shadowRadius: 12,
    elevation: 4,
  },
  emoji: { fontSize: 48, marginBottom: 8 },
  title: { fontSize: 24, fontWeight: '700', color: colors.blue, marginBottom: 4 },
  subtitle: { fontSize: 14, color: colors.gray500, marginBottom: 24, textAlign: 'center' },
  input: {
    width: '100%',
    borderWidth: 2,
    borderColor: colors.gray200,
    borderRadius: 12,
    paddingVertical: 14,
    paddingHorizontal: 16,
    fontSize: 16,
    color: colors.gray800,
    marginBottom: 12,
  },
  button: {
    backgroundColor: colors.blue,
    borderRadius: 12,
    paddingVertical: 16,
    width: '100%',
    alignItems: 'center',
    marginTop: 4,
  },
  buttonText: { color: colors.white, fontSize: 17, fontWeight: '600' },
  error: { color: colors.rose, fontSize: 14, marginTop: 14, textAlign: 'center', lineHeight: 20 },
})
