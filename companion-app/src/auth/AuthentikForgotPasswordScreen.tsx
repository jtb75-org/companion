import React from 'react'
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, ActivityIndicator, KeyboardAvoidingView, Platform,
} from 'react-native'
import { AuthLoginError, forgotPassword } from './authApi'
import { authStrings } from './authStrings'
import { colors, brand } from '../theme/colors'

/**
 * "Forgot password?" screen for the self-hosted (Authentik) path. Only rendered
 * when AUTH_PROVIDER === 'authentik' (the login screen it is launched from is
 * itself gated behind that flag). In Firebase mode this screen is never mounted
 * and the app behaves exactly as before.
 *
 * Two phases (mirrors AuthentikSignupScreen):
 *   1. 'form'  — collect an email, then POST /auth/forgot-password.
 *   2. 'sent'  — a calm "check your email" card. The member finishes by tapping
 *                the emailed link, which opens the existing /activate screen in
 *                its reset flavor (?reset=1).
 *
 * ANTI-ENUMERATION is the core requirement here. The backend returns an
 * identical 200 whether or not the address has an account, and this screen must
 * preserve that: EVERY 2xx lands on the same confirmation card, with the same
 * words. We never read the response body, never branch on it, and the copy never
 * repeats the address back or promises delivery. A caller must not be able to
 * tell "account exists" from "no account" by watching this screen.
 *
 * All copy comes from `authStrings`. Nothing sensitive is logged.
 */

// Deliberately loose: catch obvious typos (missing @ / domain) without rejecting
// valid-but-unusual addresses. The backend is the real authority.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

type Phase = 'form' | 'sent'

export function AuthentikForgotPasswordScreen({ onBackToSignIn }: { onBackToSignIn: () => void }) {
  const [phase, setPhase] = React.useState<Phase>('form')
  const [email, setEmail] = React.useState('')
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)

  const handleSend = async () => {
    const trimmedEmail = email.trim()
    if (!trimmedEmail) {
      setError(authStrings.forgotMissingEmail)
      return
    }
    if (!EMAIL_RE.test(trimmedEmail)) {
      setError(authStrings.forgotBadEmail)
      return
    }
    setError('')
    setBusy(true)
    try {
      await forgotPassword(trimmedEmail)
      // Success is generic on purpose: any 2xx means "we are done here", NOT
      // "that account exists". Switch phases without inspecting anything.
      setPhase('sent')
    } catch (e) {
      const status = e instanceof AuthLoginError ? e.status : null
      // 429 gets its own "wait a minute" copy. Everything else (422 bad email,
      // network, unknown) is deliberately merged into one generic message —
      // splitting it further risks leaking account state.
      setError(status === 429 ? authStrings.forgotTooManyTries : authStrings.forgotError)
    } finally {
      setBusy(false)
    }
  }

  if (phase === 'sent') {
    return (
      <View style={styles.container}>
        <View style={styles.card}>
          <Text style={styles.emoji}>{brand.emoji}</Text>
          <Text style={styles.title}>{authStrings.forgotSentTitle}</Text>
          {/* Intentionally does NOT echo the email back: the wording must be
              identical for a real account and an unknown address. */}
          <Text style={styles.subtitle} accessibilityLiveRegion="polite">
            {authStrings.forgotSentBody}
          </Text>
          <TouchableOpacity
            style={styles.button}
            onPress={onBackToSignIn}
            accessibilityRole="button"
            accessibilityLabel={authStrings.forgotBackButton}
          >
            <Text style={styles.buttonText}>{authStrings.forgotBackButton}</Text>
          </TouchableOpacity>
          {/* Didn't get it? Return to the form (the email is kept) to send again.
              Bounded server-side by the per-IP + per-email rate limits. */}
          <TouchableOpacity
            style={styles.linkButton}
            onPress={() => setPhase('form')}
            accessibilityRole="button"
            accessibilityLabel={authStrings.forgotResendLink}
          >
            <Text style={styles.linkText}>{authStrings.forgotResendLink}</Text>
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
        <Text style={styles.title}>{authStrings.forgotTitle}</Text>
        <Text style={styles.subtitle}>{authStrings.forgotSubtitle}</Text>

        <TextInput
          style={styles.input}
          placeholder={authStrings.forgotEmailPlaceholder}
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
          onPress={handleSend}
          disabled={busy}
          accessibilityRole="button"
          accessibilityLabel={authStrings.forgotButton}
        >
          {busy ? (
            <ActivityIndicator color={colors.white} />
          ) : (
            <Text style={styles.buttonText}>{authStrings.forgotButton}</Text>
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
          accessibilityLabel={authStrings.forgotBackButton}
        >
          <Text style={styles.linkText}>{authStrings.forgotBackButton}</Text>
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
