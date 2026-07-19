import React, { useState } from 'react'
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, ActivityIndicator,
} from 'react-native'
import { colors } from '../theme/colors'
import { api } from '../api/client'

const RELATIONSHIP_OPTIONS = [
  { value: 'family', label: 'Family Member' },
  { value: 'case_worker', label: 'Case Worker' },
  { value: 'support_coordinator', label: 'Support Coordinator' },
  { value: 'group_home_staff', label: 'Group Home Staff' },
  { value: 'paid_support', label: 'Paid Support' },
]

interface Props {
  onSuccess?: () => void
  onSkip?: () => void
  showSkip?: boolean
}

export function InviteCaregiverForm({ onSuccess, onSkip, showSkip = false }: Props) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [relationship, setRelationship] = useState('family')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [sent, setSent] = useState(false)

  const handleInvite = async () => {
    if (!name.trim()) { setError('Name is required'); return }
    if (!email.trim()) { setError('Email is required'); return }
    setError('')
    setBusy(true)
    try {
      await api('/api/v1/invitations', {
        method: 'POST',
        body: JSON.stringify({
          contact_name: name.trim(),
          email: email.trim().toLowerCase(),
          relationship_type: relationship,
          access_tier: 'tier_2',
        }),
      })
      setSent(true)
      onSuccess?.()
    } catch (e: any) {
      setError(e.message || 'Failed to send invitation')
    } finally {
      setBusy(false)
    }
  }

  if (sent) {
    return (
      <View style={styles.sentContainer}>
        <Text style={styles.sentEmoji}>✉️</Text>
        <Text style={styles.sentTitle}>Invitation Sent!</Text>
        <Text style={styles.sentText}>
          We sent an email to {email}. They'll be able to connect with you once they accept.
        </Text>
        <TouchableOpacity
          style={styles.anotherButton}
          onPress={() => { setSent(false); setName(''); setEmail(''); }}
        >
          <Text style={styles.anotherText}>Invite Another</Text>
        </TouchableOpacity>
        {showSkip && (
          <TouchableOpacity style={styles.skipButton} onPress={onSkip}>
            <Text style={styles.skipText}>Continue</Text>
          </TouchableOpacity>
        )}
      </View>
    )
  }

  return (
    <View style={styles.root}>
      <TextInput
        style={styles.input}
        placeholder="Caregiver's name"
        placeholderTextColor={colors.gray400}
        value={name}
        onChangeText={setName}
        autoCapitalize="words"
      />
      <TextInput
        style={styles.input}
        placeholder="Caregiver's email"
        placeholderTextColor={colors.gray400}
        value={email}
        onChangeText={setEmail}
        autoCapitalize="none"
        keyboardType="email-address"
      />

      <Text style={styles.label}>Relationship</Text>
      <View style={styles.pillRow}>
        {RELATIONSHIP_OPTIONS.map((opt) => (
          <TouchableOpacity
            key={opt.value}
            style={[styles.pill, relationship === opt.value && styles.pillActive]}
            onPress={() => setRelationship(opt.value)}
          >
            <Text style={[styles.pillText, relationship === opt.value && styles.pillTextActive]}>
              {opt.label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <TouchableOpacity style={styles.button} onPress={handleInvite} disabled={busy}>
        {busy ? (
          <ActivityIndicator color={colors.white} />
        ) : (
          <Text style={styles.buttonText}>Send Invitation</Text>
        )}
      </TouchableOpacity>

      {showSkip && (
        <TouchableOpacity style={styles.skipButton} onPress={onSkip}>
          <Text style={styles.skipText}>Skip for now</Text>
        </TouchableOpacity>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root: {
    alignSelf: 'stretch',
  },
  input: {
    width: '100%',
    borderWidth: 2,
    borderColor: colors.gray200,
    borderRadius: 12,
    paddingVertical: 12,
    paddingHorizontal: 16,
    fontSize: 15,
    color: colors.gray800,
    marginBottom: 10,
  },
  label: {
    fontSize: 13,
    fontWeight: '600',
    color: colors.gray600,
    marginBottom: 8,
    marginTop: 4,
  },
  pillRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    marginBottom: 16,
  },
  pill: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    borderWidth: 1.5,
    borderColor: colors.gray200,
    backgroundColor: colors.white,
  },
  pillActive: {
    borderColor: colors.blue,
    backgroundColor: colors.blueLight,
  },
  pillText: {
    fontSize: 13,
    color: colors.gray500,
  },
  pillTextActive: {
    color: colors.blue,
    fontWeight: '600',
  },
  button: {
    backgroundColor: colors.blue,
    borderRadius: 12,
    paddingVertical: 14,
    width: '100%',
    alignItems: 'center',
    marginTop: 4,
  },
  buttonText: { color: colors.white, fontSize: 16, fontWeight: '600' },
  error: { color: colors.rose, fontSize: 13, marginBottom: 8, textAlign: 'center' },
  skipButton: { marginTop: 16, alignItems: 'center' },
  skipText: { fontSize: 14, color: colors.gray400 },
  sentContainer: { alignItems: 'center', paddingVertical: 8 },
  sentEmoji: { fontSize: 40, marginBottom: 8 },
  sentTitle: { fontSize: 18, fontWeight: '700', color: colors.blue, marginBottom: 4 },
  sentText: { fontSize: 14, color: colors.gray500, textAlign: 'center', marginBottom: 16 },
  anotherButton: { marginBottom: 8 },
  anotherText: { fontSize: 14, color: colors.blue, fontWeight: '600' },
})
