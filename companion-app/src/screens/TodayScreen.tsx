import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, AppState, View, Text, ScrollView, StyleSheet, TouchableOpacity, ActivityIndicator } from 'react-native'
import { useNavigation, useFocusEffect } from '@react-navigation/native'
import messaging from '@react-native-firebase/messaging'
import { api } from '../api/client'
import { colors, brand } from '../theme/colors'
import { ScanButton } from '../components/ScanButton'
import { TodoCheckbox } from '../components/TodoCheckbox'

interface PossibleDuplicate {
  document_id: string
  received_at: string
  classification: string
}

interface PendingReview {
  id: string
  document_id: string | null
  source_description: string
  recommended_action: string
  is_urgent: boolean
  is_past_due: boolean
  is_duplicate: boolean
  possible_duplicate: PossibleDuplicate | null
  card_summary: string | null
  classification: string | null
  proposed_data: Record<string, any>
}

// Map a document classification to a warm, plain noun the member will
// recognize. Kept deliberately small and non-clinical.
function friendlyNoun(classification: string | null | undefined): string {
  switch (classification) {
    case 'bill':
      return 'bill'
    case 'appointment':
      return 'appointment'
    case 'medical_document':
    case 'insurance':
      return 'letter'
    case 'junk_mail':
      return 'mail'
    default:
      return 'item'
  }
}

// Friendly date like "July 1" (no year, no time).
function friendlyDate(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { month: 'long', day: 'numeric' })
}

interface TodayData {
  medications: { id: string; name: string; dosage: string; schedule: string[] }[]
  appointments: { id: string; provider_name: string; appointment_at: string }[]
  bills: { id: string; sender: string; amount: string; due_date: string }[]
  todos: { id: string; title: string; completed_at: string | null }[]
  pendingReviews: PendingReview[]
}

export function TodayScreen() {
  const navigation = useNavigation<any>()
  const [data, setData] = useState<TodayData | null>(null)
  const [loading, setLoading] = useState(true)
  const [greeting, setGreeting] = useState('')
  // Review ids where the member chose "Keep both" — hides the prompt locally
  // for this session without touching the server (the review stays).
  const [keptBoth, setKeptBoth] = useState<Set<string>>(new Set())
  // Review id currently being removed, so we can show a spinner and block
  // double taps.
  const [removingId, setRemovingId] = useState<string | null>(null)

  const handleToggleTodo = async (todoId: string) => {
    try {
      // Optimistic update
      setData((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          todos: prev.todos.map((t) =>
            t.id === todoId ? { ...t, completed_at: new Date().toISOString() } : t
          ),
        }
      })
      await api(`/api/v1/todos/${todoId}/complete`, { method: 'POST' })
    } catch (err) {
      // Revert on failure
      loadData()
    }
  }

  // Member chose to keep both copies — just hide the prompt for this session.
  const handleKeepBoth = (reviewId: string) => {
    setKeptBoth((prev) => {
      const next = new Set(prev)
      next.add(reviewId)
      return next
    })
  }

  // Member wants to remove the newer copy. Confirm first (never auto-delete),
  // then delete the document and refresh the list.
  const handleRemoveDuplicate = (review: PendingReview) => {
    const dup = review.possible_duplicate
    if (!review.document_id || !dup) return
    const noun = friendlyNoun(dup.classification)
    const date = friendlyDate(dup.received_at)

    Alert.alert(
      'Remove this one?',
      `This won't remove your ${noun} from ${date}.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: async () => {
            try {
              setRemovingId(review.id)
              await api(`/api/v1/documents/${review.document_id}`, { method: 'DELETE' })
              await loadData()
            } catch {
              Alert.alert(
                'That didn’t work',
                'We could not remove it just now. Please try again.',
              )
            } finally {
              setRemovingId(null)
            }
          },
        },
      ],
    )
  }

  useEffect(() => {
    const hour = new Date().getHours()
    if (hour < 12) setGreeting('Good morning')
    else if (hour < 17) setGreeting('Good afternoon')
    else setGreeting('Good evening')
  }, [])

  // Refresh data every time the screen comes into focus
  useFocusEffect(
    useCallback(() => {
      loadData()
    }, [])
  )

  // Refresh when a push notification arrives in the foreground
  useEffect(() => {
    const unsubscribe = messaging().onMessage(() => {
      loadData()
    })
    return unsubscribe
  }, [])

  // Refresh when app returns from background
  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => {
      if (state === 'active') loadData()
    })
    return () => sub.remove()
  }, [])

  const loadData = async () => {
    try {
      const [sections, meds, appts, bills, todos, reviews] = await Promise.all([
        api<any>('/api/v1/sections/today').catch(() => null),
        api<any>('/api/v1/medications').catch(() => ({ medications: [] })),
        api<any>('/api/v1/appointments').catch(() => ({ appointments: [] })),
        api<any>('/api/v1/bills').catch(() => ({ bills: [] })),
        api<any>('/api/v1/todos').catch(() => ({ todos: [] })),
        api<any>('/api/v1/reviews/pending').catch(() => ({ reviews: [] })),
      ])
      setData({
        medications: meds?.medications || [],
        appointments: appts?.appointments || [],
        bills: bills?.bills || [],
        todos: todos?.todos || [],
        pendingReviews: reviews?.reviews || [],
      })
    } catch {
      // Fallback
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.blue} />
      </View>
    )
  }

  const name = 'there'
  const activeMeds = data?.medications?.filter((m: any) => m.is_active !== false) || []
  const upcomingAppts = data?.appointments?.slice(0, 3) || []
  const pendingTodos = data?.todos?.filter((t: any) => !t.completed_at)?.slice(0, 5) || []
  const dueBills = data?.bills?.slice(0, 3) || []

  return (
    <View style={{ flex: 1, backgroundColor: colors.cream }}>
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {/* Greeting */}
      <View style={styles.header}>
        <Text style={styles.emoji}>{brand.emoji}</Text>
        <Text style={styles.greeting}>{greeting}, {name}</Text>
        <Text style={styles.subtitle}>Here's your day at a glance</Text>
      </View>

      {/* Mail Section */}
      {(data?.pendingReviews?.length ?? 0) > 0 && (
        <View style={styles.card}>
          <View style={styles.mailHeader}>
            <Text style={styles.mailIcon}>📬</Text>
            <Text style={styles.cardTitle}>MAIL</Text>
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{data!.pendingReviews.length}</Text>
            </View>
          </View>
          {data!.pendingReviews.map((review) => {
            const sender = review.proposed_data?.sender
            const amount = review.proposed_data?.amount_due
            const title = sender || review.card_summary || 'New document'
            const subtitle = amount
              ? `$${amount}`
              : review.classification || 'Document to review'
            const dup = review.possible_duplicate
            const showDupPrompt =
              !!dup && !!review.document_id && !keptBoth.has(review.id)
            const isRemoving = removingId === review.id
            return (
              <View key={review.id}>
                <TouchableOpacity
                  style={[styles.mailRow, review.is_urgent && styles.mailRowUrgent]}
                  onPress={() => navigation.navigate('Chat', { reviewId: review.id })}
                  activeOpacity={0.7}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={styles.mailTitle}>{title}</Text>
                    <Text style={styles.mailSubtitle}>
                      {subtitle} · {review.source_description}
                    </Text>
                  </View>
                  <Text style={styles.mailArrow}>→</Text>
                </TouchableOpacity>
                {showDupPrompt && (
                  <View style={styles.dupPrompt}>
                    <Text style={styles.dupTitle}>Is this the same?</Text>
                    <Text style={styles.dupBody}>
                      This looks a lot like your {friendlyNoun(dup!.classification)} from{' '}
                      {friendlyDate(dup!.received_at)}.
                    </Text>
                    <View style={styles.dupButtons}>
                      <TouchableOpacity
                        style={[styles.dupBtn, styles.dupBtnKeep]}
                        onPress={() => handleKeepBoth(review.id)}
                        disabled={isRemoving}
                        activeOpacity={0.7}
                      >
                        <Text style={styles.dupBtnKeepText}>Keep both</Text>
                      </TouchableOpacity>
                      <TouchableOpacity
                        style={[styles.dupBtn, styles.dupBtnRemove]}
                        onPress={() => handleRemoveDuplicate(review)}
                        disabled={isRemoving}
                        activeOpacity={0.7}
                      >
                        {isRemoving ? (
                          <ActivityIndicator size="small" color={colors.blue} />
                        ) : (
                          <Text style={styles.dupBtnRemoveText}>Remove this one</Text>
                        )}
                      </TouchableOpacity>
                    </View>
                  </View>
                )}
              </View>
            )
          })}
        </View>
      )}

      {/* Medications */}
      {activeMeds.length > 0 && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Medications</Text>
          {activeMeds.map((med: any) => (
            <View key={med.id} style={styles.row}>
              <View style={styles.dot} />
              <View style={{ flex: 1 }}>
                <Text style={styles.rowTitle}>{med.name} {med.dosage}</Text>
                <Text style={styles.rowSub}>{med.frequency}</Text>
              </View>
            </View>
          ))}
        </View>
      )}

      {/* Appointments */}
      {upcomingAppts.length > 0 && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Upcoming Appointments</Text>
          {upcomingAppts.map((appt: any) => (
            <View key={appt.id} style={styles.row}>
              <View style={[styles.dot, { backgroundColor: colors.teal }]} />
              <View style={{ flex: 1 }}>
                <Text style={styles.rowTitle}>{appt.provider_name}</Text>
                <Text style={styles.rowSub}>
                  {new Date(appt.appointment_at).toLocaleDateString()} at{' '}
                  {new Date(appt.appointment_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </Text>
              </View>
            </View>
          ))}
        </View>
      )}

      {/* Todos */}
      {pendingTodos.length > 0 && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>To Do</Text>
          {pendingTodos.map((todo: any) => (
            <View key={todo.id} style={styles.row}>
              <TodoCheckbox
                completed={!!todo.completed_at}
                onPress={() => handleToggleTodo(todo.id)}
                size={20}
              />
              <Text style={[styles.rowTitle, !!todo.completed_at && styles.completedText]}>
                {todo.title}
              </Text>
            </View>
          ))}
        </View>
      )}

      {/* Bills */}
      {dueBills.length > 0 && (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Bills</Text>
          {dueBills.map((bill: any) => (
            <View key={bill.id} style={styles.row}>
              <View style={{ flex: 1 }}>
                <Text style={styles.rowTitle}>{bill.sender}</Text>
                <Text style={styles.rowSub}>
                  ${bill.amount} due {new Date(bill.due_date).toLocaleDateString()}
                </Text>
              </View>
            </View>
          ))}
        </View>
      )}

      {/* Empty state */}
      {!activeMeds.length && !upcomingAppts.length && !pendingTodos.length && !dueBills.length && !(data?.pendingReviews?.length) && (
        <View style={styles.card}>
          <Text style={styles.emptyText}>Nothing on your plate today. Nice!</Text>
        </View>
      )}
    </ScrollView>
    <ScanButton />
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.cream },
  content: { padding: 20, paddingBottom: 40 },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.cream },
  header: { alignItems: 'center', marginBottom: 24 },
  emoji: { fontSize: 36, marginBottom: 4 },
  greeting: { fontSize: 22, fontWeight: '700', color: colors.gray900 },
  subtitle: { fontSize: 14, color: colors.gray500, marginTop: 2 },
  card: {
    backgroundColor: colors.white,
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 4,
    elevation: 2,
  },
  cardTitle: { fontSize: 13, fontWeight: '700', color: colors.gray500, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 12 },
  row: { flexDirection: 'row', alignItems: 'center', paddingVertical: 8, gap: 12 },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.blue },
  checkbox: { width: 18, height: 18, borderRadius: 4, borderWidth: 2, borderColor: colors.gray300 },
  rowTitle: { fontSize: 15, fontWeight: '500', color: colors.gray800 },
  completedText: { textDecorationLine: 'line-through', color: colors.gray400 },
  rowSub: { fontSize: 13, color: colors.gray500, marginTop: 1 },
  emptyText: { fontSize: 15, color: colors.gray400, textAlign: 'center', paddingVertical: 20 },
  mailHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
  mailIcon: { fontSize: 18 },
  badge: { backgroundColor: colors.blue, borderRadius: 10, minWidth: 20, height: 20, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 6 },
  badgeText: { color: colors.white, fontSize: 11, fontWeight: '700' },
  mailRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 12, borderTopWidth: 1, borderTopColor: colors.gray100 || '#f3f4f6' },
  mailRowUrgent: { backgroundColor: '#FDF3E7', marginHorizontal: -16, paddingHorizontal: 16, borderLeftWidth: 3, borderLeftColor: '#D4832A' },
  mailTitle: { fontSize: 15, fontWeight: '600', color: colors.gray800 },
  mailSubtitle: { fontSize: 13, color: colors.gray500, marginTop: 2 },
  mailArrow: { fontSize: 18, color: colors.gray400, marginLeft: 8 },
  dupPrompt: {
    backgroundColor: colors.blueLight,
    borderRadius: 12,
    padding: 14,
    marginTop: 4,
    marginBottom: 4,
  },
  dupTitle: { fontSize: 15, fontWeight: '700', color: colors.gray800 },
  dupBody: { fontSize: 14, color: colors.gray700, marginTop: 4, lineHeight: 20 },
  dupButtons: { flexDirection: 'row', gap: 10, marginTop: 12 },
  dupBtn: {
    flex: 1,
    minHeight: 48,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 12,
  },
  dupBtnKeep: { backgroundColor: colors.blue },
  dupBtnKeepText: { color: colors.white, fontSize: 15, fontWeight: '700' },
  dupBtnRemove: { backgroundColor: colors.white, borderWidth: 1.5, borderColor: colors.gray300 },
  dupBtnRemoveText: { color: colors.blue, fontSize: 15, fontWeight: '700' },
})
