import React from 'react'
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, ActivityIndicator, KeyboardAvoidingView, Platform,
} from 'react-native'
import { useAuth } from './AuthProvider'
import {
  AuthLoginError,
  ActivationDetails,
  validateActivationToken,
  setActivationPassword,
} from './authApi'
import { authStrings } from './authStrings'
import { colors, brand } from '../theme/colors'

/**
 * "Set your password" screen, opened from the link in a member's email.
 *
 * Only reachable when AUTH_PROVIDER === 'authentik' (AppNavigator routes here on
 * an inbound /activate deep link). In Firebase mode this screen is never
 * mounted and the app behaves exactly as before.
 *
 * Flow:
 *   1. On mount, check the link (GET /activation/validate). A good link greets
 *      the member by name and shows their (read-only) email + a password field.
 *      A bad/expired link shows a calm "this link did not work" state.
 *   2. On submit, save the password (POST /activation/set-password), then sign
 *      in with the SAME BFF login the login screen uses, so the app lands
 *      authenticated on its home screen.
 *
 * The SAME screen serves two flavors, chosen by the `reset` marker on the inbound
 * link (`/activate?token=…&reset=1`, sent by the forgot-password email):
 *   - reset=false (default) — activation: "Make a password to start using D.D."
 *   - reset=true            — password reset: "Set a new password".
 * This is a COPY-ONLY switch. Both flavors validate and redeem through the exact
 * same endpoints with the same request; `reset` is never sent to the backend and
 * grants nothing. The token alone carries authority.
 *
 * All copy comes from `authStrings`. The password is never logged.
 */

const MIN_PASSWORD_LENGTH = 10

type Phase = 'checking' | 'ready' | 'invalid'

export function AuthentikActivateScreen({
  token,
  reset = false,
  onBackToSignIn,
}: {
  token: string | null
  /** Copy-only: show reset wording instead of first-time activation wording. */
  reset?: boolean
  onBackToSignIn: () => void
}) {
  const { signInWithPassword } = useAuth()
  const [phase, setPhase] = React.useState<Phase>('checking')
  const [details, setDetails] = React.useState<ActivationDetails | null>(null)
  const [password, setPassword] = React.useState('')
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)

  // Check the link once on mount (or whenever the token changes).
  React.useEffect(() => {
    let cancelled = false
    if (!token) {
      setPhase('invalid')
      return
    }
    setPhase('checking')
    validateActivationToken(token)
      .then((data) => {
        if (cancelled) return
        if (data?.valid) {
          setDetails(data)
          setPhase('ready')
        } else {
          setPhase('invalid')
        }
      })
      .catch(() => {
        // 404 (bad/expired) or network — both land on the calm invalid state.
        if (!cancelled) setPhase('invalid')
      })
    return () => {
      cancelled = true
    }
  }, [token])

  const handleCreatePassword = async () => {
    if (!token) {
      setPhase('invalid')
      return
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(authStrings.activateTooShort)
      return
    }
    setError('')
    setBusy(true)
    try {
      const email = await setActivationPassword(token, password)
      // Password saved. Sign in with the same BFF login the login screen uses.
      try {
        await signInWithPassword(email, password)
        // On success AuthProvider flips isAuthenticated and AppNavigator routes
        // into the app. Nothing else to do here.
      } catch {
        // Password IS saved, but the auto sign-in failed. Send them to Sign In.
        setError(authStrings.activateSavedGoSignIn)
      }
    } catch (e) {
      const status = e instanceof AuthLoginError ? e.status : null
      if (status === 400) {
        // Link went bad/expired between the check and the save.
        setPhase('invalid')
      } else if (status === 422 && e instanceof AuthLoginError && e.message) {
        // Password-policy rejection — show the backend's plain "too short / too
        // common / ..." message so the member knows what to change (retryable).
        setError(e.message)
      } else {
        // 502 (IdP) / network / other — retryable.
        setError(authStrings.activateSaveError)
      }
    } finally {
      setBusy(false)
    }
  }

  if (phase === 'checking') {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.blue} />
        <Text style={styles.checkingText}>{authStrings.activateChecking}</Text>
      </View>
    )
  }

  if (phase === 'invalid') {
    return (
      <View style={styles.container}>
        <View style={styles.card}>
          <Text style={styles.emoji}>{brand.emoji}</Text>
          <Text style={styles.title}>{authStrings.activateInvalidTitle}</Text>
          <Text style={styles.subtitle}>
            {reset ? authStrings.activateResetInvalidBody : authStrings.activateInvalidBody}
          </Text>
          <TouchableOpacity
            style={styles.button}
            onPress={onBackToSignIn}
            accessibilityRole="button"
            accessibilityLabel={authStrings.activateBackButton}
          >
            <Text style={styles.buttonText}>{authStrings.activateBackButton}</Text>
          </TouchableOpacity>
        </View>
      </View>
    )
  }

  // phase === 'ready'. The backend falls back name→email when a member has no
  // name, so drop to a bare "Hi" rather than greeting them by their email.
  const greeting =
    details?.name && details.name !== details.email
      ? `${authStrings.activateHello} ${details.name}`
      : authStrings.activateHello

  // The greeting is kept on BOTH flavors. Suppressing the name on reset would
  // protect nothing (this screen already shows the account's email below) and a
  // member who just forgot their password is at a stress moment — recognition
  // helps and costs nothing. Only the prompt + button change for reset.
  const heading = greeting
  const prompt = reset ? authStrings.activateResetPrompt : authStrings.activatePrompt
  const submitLabel = reset
    ? authStrings.activateResetCreateButton
    : authStrings.activateCreateButton

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={styles.card}>
        <Text style={styles.emoji}>{brand.emoji}</Text>
        <Text style={styles.title}>{heading}</Text>
        <Text style={styles.subtitle}>{prompt}</Text>

        {details?.email ? (
          <View style={styles.emailBlock}>
            <Text style={styles.emailLabel}>{authStrings.activateEmailLabel}</Text>
            <Text style={styles.emailValue}>{details.email}</Text>
          </View>
        ) : null}

        <TextInput
          style={styles.input}
          placeholder={authStrings.activateNewPasswordPlaceholder}
          placeholderTextColor={colors.gray400}
          value={password}
          onChangeText={setPassword}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
          textContentType="newPassword"
        />

        <TouchableOpacity
          style={styles.button}
          onPress={handleCreatePassword}
          disabled={busy}
          accessibilityRole="button"
          accessibilityLabel={submitLabel}
        >
          {busy ? (
            <ActivityIndicator color={colors.white} />
          ) : (
            <Text style={styles.buttonText}>{submitLabel}</Text>
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
  checkingText: { color: colors.gray500, fontSize: 14, marginTop: 14 },
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
  emailBlock: { width: '100%', marginBottom: 16 },
  emailLabel: { fontSize: 12, color: colors.gray500, marginBottom: 4 },
  emailValue: { fontSize: 16, color: colors.gray800, fontWeight: '600' },
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
