import React from 'react'
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, ActivityIndicator, KeyboardAvoidingView, Platform,
} from 'react-native'
import { AuthLoginError, signup } from './authApi'
import { authStrings } from './authStrings'
import { colors, brand } from '../theme/colors'

/**
 * Member self-signup ("Create account") screen for the self-hosted (Authentik)
 * path. Only rendered when AUTH_PROVIDER === 'authentik' (the login screen it is
 * launched from is itself gated behind that flag). In Firebase mode this screen
 * is never mounted and the app behaves exactly as before.
 *
 * Two phases:
 *   1. 'form'  — collect a name + email, then POST /auth/signup.
 *   2. 'sent'  — a calm "check your email" card. The member finishes by tapping
 *                the emailed link, which opens the existing /activate screen.
 *
 * This screen's job ENDS at "we've sent you an email": it never collects a
 * password or tries to sign the member in. The signup response is intentionally
 * generic (anti-enumeration), so any 2xx is treated as success.
 *
 * All copy comes from `authStrings`. Nothing sensitive is logged.
 */

// Deliberately loose: catch obvious typos (missing @ / domain) without rejecting
// valid-but-unusual addresses. The backend is the real authority.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

type Phase = 'form' | 'sent'

export function AuthentikSignupScreen({ onBackToSignIn }: { onBackToSignIn: () => void }) {
  const [phase, setPhase] = React.useState<Phase>('form')
  const [name, setName] = React.useState('')
  const [email, setEmail] = React.useState('')
  const [sentEmail, setSentEmail] = React.useState('')
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)

  const handleCreate = async () => {
    const trimmedName = name.trim()
    const trimmedEmail = email.trim()
    if (!trimmedName) {
      setError(authStrings.signupMissingName)
      return
    }
    if (!trimmedEmail) {
      setError(authStrings.signupMissingEmail)
      return
    }
    if (!EMAIL_RE.test(trimmedEmail)) {
      setError(authStrings.signupBadEmail)
      return
    }
    setError('')
    setBusy(true)
    try {
      await signup(trimmedEmail, trimmedName)
      // Success is generic on purpose. Remember the email for the confirmation
      // card, then switch phases.
      setSentEmail(trimmedEmail)
      setPhase('sent')
    } catch (e) {
      const status = e instanceof AuthLoginError ? e.status : null
      setError(status === 429 ? authStrings.signupTooManyTries : authStrings.signupError)
    } finally {
      setBusy(false)
    }
  }

  if (phase === 'sent') {
    return (
      <View style={styles.container}>
        <View style={styles.card}>
          <Text style={styles.emoji}>{brand.emoji}</Text>
          <Text style={styles.title}>{authStrings.signupSentTitle}</Text>
          <Text style={styles.subtitle} accessibilityLiveRegion="polite">
            {authStrings.signupSentBodyPrefix} {sentEmail}. {authStrings.signupSentBodySuffix}
          </Text>
          <TouchableOpacity
            style={styles.button}
            onPress={onBackToSignIn}
            accessibilityRole="button"
            accessibilityLabel={authStrings.signupBackButton}
          >
            <Text style={styles.buttonText}>{authStrings.signupBackButton}</Text>
          </TouchableOpacity>
          {/* Didn't get it? Return to the form (email/name are kept) to send again.
              Bounded server-side by the per-IP + per-email rate limits. */}
          <TouchableOpacity
            style={styles.linkButton}
            onPress={() => setPhase('form')}
            accessibilityRole="button"
            accessibilityLabel={authStrings.signupResendLink}
          >
            <Text style={styles.linkText}>{authStrings.signupResendLink}</Text>
          </TouchableOpacity>
        </View>
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
        <Text style={styles.title}>{authStrings.signupTitle}</Text>
        <Text style={styles.subtitle}>{authStrings.signupSubtitle}</Text>

        <TextInput
          style={styles.input}
          placeholder={authStrings.signupNamePlaceholder}
          placeholderTextColor={colors.gray400}
          value={name}
          onChangeText={setName}
          autoCapitalize="words"
          autoCorrect={false}
          textContentType="name"
        />
        <TextInput
          style={styles.input}
          placeholder={authStrings.signupEmailPlaceholder}
          placeholderTextColor={colors.gray400}
          value={email}
          onChangeText={setEmail}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          textContentType="emailAddress"
        />

        <TouchableOpacity
          style={styles.button}
          onPress={handleCreate}
          disabled={busy}
          accessibilityRole="button"
          accessibilityLabel={authStrings.signupButton}
        >
          {busy ? (
            <ActivityIndicator color={colors.white} />
          ) : (
            <Text style={styles.buttonText}>{authStrings.signupButton}</Text>
          )}
        </TouchableOpacity>

        {error ? (
          <Text style={styles.error} accessibilityLiveRegion="polite">
            {error}
          </Text>
        ) : null}

        <TouchableOpacity
          style={styles.linkButton}
          onPress={onBackToSignIn}
          disabled={busy}
          accessibilityRole="button"
          accessibilityLabel={authStrings.signupBackButton}
        >
          <Text style={styles.linkText}>{authStrings.signupBackButton}</Text>
        </TouchableOpacity>
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
  title: { fontSize: 24, fontWeight: '700', color: colors.blue, marginBottom: 4, textAlign: 'center' },
  subtitle: { fontSize: 14, color: colors.gray500, marginBottom: 24, textAlign: 'center', lineHeight: 20 },
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
  linkButton: { marginTop: 18, paddingVertical: 4 },
  linkText: { color: colors.blue, fontSize: 15, fontWeight: '600' },
})
