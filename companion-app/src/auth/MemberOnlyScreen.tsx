import React from 'react'
import {
  View, Text, TouchableOpacity, StyleSheet, ActivityIndicator,
} from 'react-native'
import { useAuth } from './AuthProvider'
import { authStrings } from './authStrings'
import { colors } from '../theme/colors'

/**
 * The member-only gate.
 *
 * This is the MEMBER app, but sign-in admits every cohort — so a family helper
 * (caregiver) or an admin can log in successfully and then have no member
 * profile to show. Rather than bounce them silently back to the login screen
 * (which reads as a broken login), we land them here: a calm, blame-free screen
 * that explains this app is for members and points them at the web page where
 * helpers actually do their thing.
 *
 * All copy lives in `authStrings` so the safety-privacy-reviewer can sign off in
 * one place. Styling matches the login / onboarding screens (same card, colors,
 * spacing) so it reads as part of the app, not an error.
 */
export function MemberOnlyScreen() {
  const { signOut } = useAuth()
  const [busy, setBusy] = React.useState(false)

  const handleSignOut = async () => {
    setBusy(true)
    try {
      await signOut()
    } finally {
      // If sign-out threw, let them try again rather than trapping the spinner.
      setBusy(false)
    }
  }

  return (
    <View style={styles.container}>
      <View style={styles.card}>
        <Text style={styles.emoji}>💙</Text>
        <Text style={styles.title}>{authStrings.gateTitle}</Text>
        <Text style={styles.body}>{authStrings.gateBody}</Text>

        <Text style={styles.webPrompt}>{authStrings.gateWebPrompt}</Text>
        <Text style={styles.webAddress} accessibilityRole="text">
          {authStrings.gateWebAddress}
        </Text>

        <TouchableOpacity
          style={styles.button}
          onPress={handleSignOut}
          disabled={busy}
          accessibilityRole="button"
          accessibilityLabel={authStrings.gateSignOutButton}
        >
          {busy ? (
            <ActivityIndicator color={colors.white} />
          ) : (
            <Text style={styles.buttonText}>{authStrings.gateSignOutButton}</Text>
          )}
        </TouchableOpacity>
      </View>
    </View>
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
  title: {
    fontSize: 24,
    fontWeight: '700',
    color: colors.blue,
    marginBottom: 12,
    textAlign: 'center',
  },
  body: {
    fontSize: 16,
    color: colors.gray700,
    textAlign: 'center',
    lineHeight: 24,
    marginBottom: 24,
  },
  webPrompt: {
    fontSize: 15,
    color: colors.gray600,
    textAlign: 'center',
    lineHeight: 22,
    marginBottom: 6,
  },
  webAddress: {
    fontSize: 18,
    fontWeight: '700',
    color: colors.blue,
    textAlign: 'center',
    marginBottom: 28,
  },
  button: {
    backgroundColor: colors.blue,
    borderRadius: 12,
    paddingVertical: 16,
    width: '100%',
    alignItems: 'center',
  },
  buttonText: { color: colors.white, fontSize: 17, fontWeight: '600' },
})
