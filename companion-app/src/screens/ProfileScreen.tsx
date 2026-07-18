import React, { useEffect, useState, useCallback } from 'react'
import {
  View, Text, TouchableOpacity, ScrollView, StyleSheet,
  Modal, ActivityIndicator, Alert,
} from 'react-native'
import { useAuth } from '../auth/AuthProvider'
import { api } from '../api/client'
import { InviteCaregiverForm } from '../components/InviteCaregiverForm'
import { colors, brand } from '../theme/colors'

interface Caregiver {
  id: string
  contact_name: string
  contact_email: string
  relationship_type: string
  access_tier: string
  invitation_status: string
  is_active: boolean
}

interface ProfileData {
  preferred_name?: string
  display_name?: string
  first_name?: string
  last_name?: string
  phone?: string
}

const RELATIONSHIP_LABELS: Record<string, string> = {
  family: 'Family',
  case_worker: 'Case Worker',
  support_coordinator: 'Support Coordinator',
  group_home_staff: 'Group Home Staff',
  paid_support: 'Paid Support',
}

const STATUS_COLORS: Record<string, string> = {
  accepted: colors.emerald,
  pending: colors.amber,
  declined: colors.rose,
}

export function ProfileScreen() {
  const { signOut } = useAuth()
  const [profile, setProfile] = useState<ProfileData | null>(null)
  const [caregivers, setCaregivers] = useState<Caregiver[]>([])
  const [loadingCaregivers, setLoadingCaregivers] = useState(true)
  const [showInviteModal, setShowInviteModal] = useState(false)

  const loadCaregivers = useCallback(async () => {
    try {
      const data = await api<{ caregivers: Caregiver[] }>('/api/v1/me/caregivers')
      setCaregivers(data.caregivers || [])
    } catch {
      setCaregivers([])
    } finally {
      setLoadingCaregivers(false)
    }
  }, [])

  useEffect(() => {
    const loadProfile = async () => {
      try {
        const data = await api<ProfileData>('/api/v1/me')
        setProfile(data)
      } catch {
        // ignore
      }
    }
    loadProfile()
    loadCaregivers()
  }, [loadCaregivers])

  const displayName = profile?.preferred_name || profile?.display_name || 'Member'

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* User info */}
      <View style={styles.card}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>
            {displayName[0].toUpperCase()}
          </Text>
        </View>
        <Text style={styles.name}>{displayName}</Text>
        {profile?.phone && <Text style={styles.phone}>{profile.phone}</Text>}
      </View>

      {/* My Caregivers */}
      <View style={styles.card}>
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>My Caregivers</Text>
          <TouchableOpacity
            style={styles.inviteButton}
            onPress={() => setShowInviteModal(true)}
          >
            <Text style={styles.inviteButtonText}>+ Invite</Text>
          </TouchableOpacity>
        </View>

        {loadingCaregivers ? (
          <ActivityIndicator color={colors.blue} style={{ marginVertical: 16 }} />
        ) : caregivers.length === 0 ? (
          <View style={styles.emptyState}>
            <Text style={styles.emptyEmoji}>🤝</Text>
            <Text style={styles.emptyText}>No caregivers yet</Text>
            <Text style={styles.emptySubtext}>
              Invite someone you trust to help support you
            </Text>
            <TouchableOpacity
              style={styles.emptyButton}
              onPress={() => setShowInviteModal(true)}
            >
              <Text style={styles.emptyButtonText}>Invite a Caregiver</Text>
            </TouchableOpacity>
          </View>
        ) : (
          caregivers.map((cg) => (
            <View key={cg.id} style={styles.caregiverRow}>
              <View style={styles.caregiverAvatar}>
                <Text style={styles.caregiverAvatarText}>
                  {cg.contact_name[0].toUpperCase()}
                </Text>
              </View>
              <View style={styles.caregiverInfo}>
                <Text style={styles.caregiverName}>{cg.contact_name}</Text>
                <Text style={styles.caregiverDetail}>
                  {RELATIONSHIP_LABELS[cg.relationship_type] || cg.relationship_type}
                </Text>
                <Text style={styles.caregiverEmail}>{cg.contact_email}</Text>
              </View>
              <View style={[
                styles.statusBadge,
                { backgroundColor: (STATUS_COLORS[cg.invitation_status] || colors.gray400) + '18' },
              ]}>
                <Text style={[
                  styles.statusText,
                  { color: STATUS_COLORS[cg.invitation_status] || colors.gray400 },
                ]}>
                  {cg.invitation_status === 'accepted' ? 'Active' :
                   cg.invitation_status === 'pending' ? 'Pending' : cg.invitation_status}
                </Text>
              </View>
              <TouchableOpacity
                style={styles.removeButton}
                onPress={() => {
                  Alert.alert(
                    'Remove Caregiver',
                    `Remove ${cg.contact_name} as your caregiver?`,
                    [
                      { text: 'Cancel', style: 'cancel' },
                      {
                        text: 'Remove',
                        style: 'destructive',
                        onPress: async () => {
                          try {
                            await api(`/api/v1/contacts/${cg.id}`, {
                              method: 'DELETE',
                            })
                            loadCaregivers()
                          } catch (e: any) {
                            Alert.alert('Error', e.message)
                          }
                        },
                      },
                    ]
                  )
                }}
              >
                <Text style={styles.removeText}>✕</Text>
              </TouchableOpacity>
            </View>
          ))
        )}
      </View>

      {/* Settings */}
      <View style={styles.card}>
        <Text style={[styles.sectionTitle, { alignSelf: 'flex-start', marginBottom: 12 }]}>Settings</Text>
        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>Quiet Hours</Text>
          <Text style={styles.settingValue}>9pm - 8am</Text>
        </View>
        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>Check-in Time</Text>
          <Text style={styles.settingValue}>9:00 AM</Text>
        </View>
        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>Voice</Text>
          <Text style={styles.settingValue}>Warm</Text>
        </View>
      </View>

      {/* Sign out */}
      <TouchableOpacity style={styles.signOutButton} onPress={signOut}>
        <Text style={styles.signOutText}>Sign Out</Text>
      </TouchableOpacity>

      {/* Delete account */}
      <TouchableOpacity
        style={styles.deleteButton}
        onPress={() => {
          Alert.alert(
            'Delete Account',
            'Are you sure you want to delete your account? This will schedule your account for deletion. You can cancel within the grace period.',
            [
              { text: 'Cancel', style: 'cancel' },
              {
                text: 'Delete My Account',
                style: 'destructive',
                onPress: async () => {
                  try {
                    const result = await api<{ deleted?: boolean; deletion_requested?: boolean; scheduled_date?: string }>('/api/v1/me/request-deletion', { method: 'POST' })
                    if (result.deleted) {
                      Alert.alert('Account Deleted', 'Your account has been permanently deleted.', [
                        { text: 'OK', onPress: () => signOut() },
                      ])
                    } else {
                      Alert.alert(
                        'Deletion Scheduled',
                        `Your account is scheduled for deletion on ${result.scheduled_date}. You can cancel this from your profile before then.`,
                        [{ text: 'OK', onPress: () => signOut() }]
                      )
                    }
                  } catch (e: any) {
                    Alert.alert('Error', e.message || 'Failed to delete account')
                  }
                },
              },
            ]
          )
        }}
      >
        <Text style={styles.deleteText}>Delete Account</Text>
      </TouchableOpacity>

      <Text style={styles.version}>{brand.name} v1.0.0</Text>

      {/* Invite Modal */}
      <Modal visible={showInviteModal} animationType="slide" presentationStyle="pageSheet">
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Invite a Caregiver</Text>
            <TouchableOpacity onPress={() => setShowInviteModal(false)}>
              <Text style={styles.modalClose}>Done</Text>
            </TouchableOpacity>
          </View>
          <ScrollView contentContainerStyle={styles.modalContent} keyboardShouldPersistTaps="handled">
            <Text style={styles.modalSubtitle}>
              Send an invitation to someone you trust to help support you.
            </Text>
            <InviteCaregiverForm
              onSuccess={() => {
                loadCaregivers()
              }}
            />
          </ScrollView>
        </View>
      </Modal>
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.cream },
  content: { padding: 20, paddingBottom: 40 },
  card: {
    backgroundColor: colors.white,
    borderRadius: 16,
    padding: 20,
    marginBottom: 12,
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 4,
    elevation: 2,
  },
  avatar: {
    width: 64, height: 64, borderRadius: 32,
    backgroundColor: colors.blue,
    justifyContent: 'center', alignItems: 'center',
    marginBottom: 12,
  },
  avatarText: { fontSize: 24, fontWeight: '700', color: colors.white },
  name: { fontSize: 18, fontWeight: '700', color: colors.gray900 },
  email: { fontSize: 14, color: colors.gray500, marginTop: 2 },
  phone: { fontSize: 14, color: colors.gray400, marginTop: 2 },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    width: '100%',
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 13, fontWeight: '700', color: colors.gray500,
    textTransform: 'uppercase', letterSpacing: 0.5,
  },
  inviteButton: {
    paddingHorizontal: 12, paddingVertical: 6,
    borderRadius: 12, backgroundColor: colors.blueLight,
  },
  inviteButtonText: { fontSize: 13, fontWeight: '600', color: colors.blue },
  emptyState: { alignItems: 'center', paddingVertical: 12 },
  emptyEmoji: { fontSize: 32, marginBottom: 8 },
  emptyText: { fontSize: 15, fontWeight: '600', color: colors.gray700, marginBottom: 4 },
  emptySubtext: { fontSize: 13, color: colors.gray400, textAlign: 'center', marginBottom: 16 },
  emptyButton: {
    paddingHorizontal: 20, paddingVertical: 10,
    borderRadius: 12, backgroundColor: colors.blue,
  },
  emptyButtonText: { fontSize: 14, fontWeight: '600', color: colors.white },
  caregiverRow: {
    flexDirection: 'row', alignItems: 'center',
    width: '100%', paddingVertical: 10,
    borderTopWidth: 1, borderTopColor: colors.gray100,
  },
  caregiverAvatar: {
    width: 40, height: 40, borderRadius: 20,
    backgroundColor: colors.teal, justifyContent: 'center', alignItems: 'center',
    marginRight: 12,
  },
  caregiverAvatarText: { fontSize: 16, fontWeight: '700', color: colors.white },
  caregiverInfo: { flex: 1 },
  caregiverName: { fontSize: 15, fontWeight: '600', color: colors.gray800 },
  caregiverDetail: { fontSize: 12, color: colors.gray500, marginTop: 1 },
  caregiverEmail: { fontSize: 12, color: colors.gray400, marginTop: 1 },
  statusBadge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8 },
  statusText: { fontSize: 11, fontWeight: '600' },
  removeButton: {
    marginLeft: 8, padding: 6,
  },
  removeText: { fontSize: 14, color: colors.gray400 },
  settingRow: {
    flexDirection: 'row', justifyContent: 'space-between',
    width: '100%', paddingVertical: 10,
    borderTopWidth: 1, borderTopColor: colors.gray100,
  },
  settingLabel: { fontSize: 15, color: colors.gray700 },
  settingValue: { fontSize: 15, color: colors.gray500 },
  signOutButton: {
    backgroundColor: colors.white, borderRadius: 12, padding: 14,
    alignItems: 'center', marginTop: 8, borderWidth: 1, borderColor: colors.gray200,
  },
  signOutText: { fontSize: 15, fontWeight: '600', color: colors.rose },
  deleteButton: {
    borderRadius: 12, padding: 14,
    alignItems: 'center', marginTop: 8,
  },
  deleteText: { fontSize: 13, color: colors.gray400 },
  version: { fontSize: 12, color: colors.gray400, textAlign: 'center', marginTop: 20 },
  modalContainer: { flex: 1, backgroundColor: colors.cream },
  modalHeader: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    padding: 20, paddingTop: 60, backgroundColor: colors.white,
    borderBottomWidth: 1, borderBottomColor: colors.gray100,
  },
  modalTitle: { fontSize: 18, fontWeight: '700', color: colors.gray900 },
  modalClose: { fontSize: 16, fontWeight: '600', color: colors.blue },
  modalContent: { padding: 24 },
  modalSubtitle: { fontSize: 14, color: colors.gray500, marginBottom: 20 },
})
