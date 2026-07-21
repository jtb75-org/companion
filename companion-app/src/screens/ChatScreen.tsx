import React, { useEffect, useRef, useState, useCallback } from 'react'
import {
  View, Text, TextInput, TouchableOpacity, FlatList, Alert,
  StyleSheet, KeyboardAvoidingView, Platform, ActivityIndicator,
} from 'react-native'
import { useRoute } from '@react-navigation/native'
import { NativeModules } from 'react-native'
import { api } from '../api/client'
import { CutShortNote, CutReason, shouldShowCutShortNote } from '../components/CutShortNote'

const { AudioPlayerModule, AudioRecorderModule } = NativeModules
import { colors, brand } from '../theme/colors'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  audioData?: string // base64 MP3
  // Set when the backend signals the answer was cut off mid-stream (a content
  // or length cut). Additive + backward-safe: absent/false renders as before.
  cutShort?: boolean
  cutReason?: CutReason
}

export function ChatScreen() {
  const route = useRoute<any>()
  const reviewId = route.params?.reviewId as string | undefined
  const morningBriefing = route.params?.briefing as string | undefined
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const [sending, setSending] = useState(false)
  const [starting, setStarting] = useState(true)
  const [recording, setRecording] = useState(false)
  const [playing, setPlaying] = useState<string | null>(null)
  const flatListRef = useRef<FlatList>(null)

  useEffect(() => {
    startSession()
    return () => {
      AudioPlayerModule?.stopAudio().catch(() => {})
    }
  }, [reviewId])

  const startSession = async () => {
    let trigger = 'user_initiated'
    if (reviewId) trigger = 'document_review'
    else if (morningBriefing) trigger = 'morning_checkin'

    let initialText = `Hi! I'm ${brand.short}. What would you like to do today?`
    if (reviewId) {
      initialText = `Let me pull up that document for you...`
    } else if (morningBriefing) {
      initialText = morningBriefing
    }

    setMessages([{ id: '0', role: 'assistant', content: initialText }])
    setStarting(false)

    try {
      const res = await api<{ session_id: string; greeting: string; audio_data?: string }>(
        '/api/v1/conversation/start',
        { method: 'POST', body: JSON.stringify({ initial_context: trigger }) },
      )
      setSessionId(res.session_id)
      sessionIdRef.current = res.session_id
      if (res.greeting && !morningBriefing) {
        setMessages([{
          id: '0',
          role: 'assistant',
          content: res.greeting,
          audioData: res.audio_data || undefined,
        }])
        // Auto-play greeting audio
        if (res.audio_data) {
          playAudio(res.audio_data, '0')
        }
      }
    } catch {
      console.log('[ChatScreen] Failed to start session')
    }
  }

  const playAudio = async (base64Audio: string, messageId: string) => {
    try {
      setPlaying(messageId)
      await AudioPlayerModule.playBase64Audio(base64Audio)
      // Auto-reset playing state after estimated duration
      // (native module doesn't provide playback completion callback)
      const estimatedDuration = Math.max(2000, base64Audio.length / 50)
      setTimeout(() => setPlaying(null), estimatedDuration)
    } catch (err: any) {
      console.log('[ChatScreen] Audio playback error:', err)
      Alert.alert('Audio Error', String(err?.message || err))
      setPlaying(null)
    }
  }

  const startRecording = async () => {
    try {
      setRecording(true)
      await AudioRecorderModule.startRecording()
    } catch (err: any) {
      console.log('[ChatScreen] Recording start error:', err)
      Alert.alert('Mic Error', String(err?.message || err))
      setRecording(false)
    }
  }

  const stopRecordingAndSend = async () => {
    try {
      const audioBase64 = await AudioRecorderModule.stopRecording()
      setRecording(false)

      if (!audioBase64 || !sessionIdRef.current) return

      const userMsg: Message = {
        id: Date.now().toString(),
        role: 'user',
        content: '\ud83c\udfa4 Voice message',
      }
      setMessages((prev) => [...prev, userMsg])
      setSending(true)

      const res = await api<{
        response: string
        audio_data?: string
        cut_short?: boolean
        cut_reason?: CutReason
      }>(
        '/api/v1/conversation/message',
        {
          method: 'POST',
          body: JSON.stringify({
            text: '',
            audio_data: audioBase64,
          }),
        },
      )

      const assistantMsg: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: res.response,
        audioData: res.audio_data || undefined,
        cutShort: res.cut_short === true,
        cutReason: res.cut_reason,
      }
      setMessages((prev) => [...prev, assistantMsg])

      if (res.audio_data) {
        playAudio(res.audio_data, assistantMsg.id)
      }
    } catch (err: any) {
      console.log('[ChatScreen] Voice send error:', err)
      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          role: 'assistant',
          content: "Sorry, I couldn't hear that clearly. Could you try again?",
        },
      ])
    } finally {
      setRecording(false)
      setSending(false)
    }
  }

  const sendMessage = useCallback(async () => {
    if (!input.trim() || sending) return
    if (!sessionIdRef.current) {
      await startSession()
      if (!sessionIdRef.current) return
    }
    const text = input.trim()
    setInput('')

    const userMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: text,
    }
    setMessages((prev) => [...prev, userMsg])
    setSending(true)

    try {
      const res = await api<{
        response: string
        audio_data?: string
        cut_short?: boolean
        cut_reason?: CutReason
      }>(
        '/api/v1/conversation/message',
        {
          method: 'POST',
          body: JSON.stringify({ text }),
        },
      )
      const assistantMsg: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: res.response,
        audioData: res.audio_data || undefined,
        cutShort: res.cut_short === true,
        cutReason: res.cut_reason,
      }
      setMessages((prev) => [...prev, assistantMsg])

      // Auto-play response audio
      if (res.audio_data) {
        playAudio(res.audio_data, assistantMsg.id)
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          role: 'assistant',
          content: "Sorry, I couldn't process that. Try again?",
        },
      ])
    } finally {
      setSending(false)
    }
  }, [input, sessionId, sending])

  if (starting) {
    return (
      <View style={styles.center}>
        <Text style={styles.emoji}>{brand.emoji}</Text>
        <ActivityIndicator size="large" color={colors.blue} />
        <Text style={styles.connectingText}>Connecting to {brand.short}...</Text>
      </View>
    )
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={90}
    >
      <FlatList
        ref={flatListRef}
        data={messages}
        keyExtractor={(m) => m.id}
        contentContainerStyle={styles.messageList}
        onContentSizeChange={() => flatListRef.current?.scrollToEnd({ animated: true })}
        renderItem={({ item }) => (
          <View style={[styles.bubble, item.role === 'user' ? styles.userBubble : styles.assistantBubble]}>
            {item.role === 'assistant' && (
              <View style={styles.botHeader}>
                <Text style={styles.botName}>{brand.short}</Text>
                {item.audioData && (
                  <TouchableOpacity
                    onPress={() => playAudio(item.audioData!, item.id)}
                    disabled={playing === item.id}
                  >
                    <Text style={styles.playButton}>
                      {playing === item.id ? '...' : '\ud83d\udd0a'}
                    </Text>
                  </TouchableOpacity>
                )}
              </View>
            )}
            <Text style={[styles.bubbleText, item.role === 'user' && styles.userText]}>{item.content}</Text>
            {shouldShowCutShortNote(item) && (
              <CutShortNote reason={item.cutReason} />
            )}
          </View>
        )}
      />

      {sending && (
        <View style={styles.typingRow}>
          <ActivityIndicator size="small" color={colors.gray400} />
          <Text style={styles.typingText}>{brand.short} is thinking...</Text>
        </View>
      )}

      <View style={styles.inputRow}>
        {/* Mic button */}
        <TouchableOpacity
          style={[styles.micButton, recording && styles.micRecording]}
          onPressIn={startRecording}
          onPressOut={stopRecordingAndSend}
          disabled={sending}
        >
          <Text style={styles.micIcon}>{recording ? '\u23f9' : '\ud83c\udfa4'}</Text>
        </TouchableOpacity>

        <TextInput
          style={styles.input}
          value={input}
          onChangeText={setInput}
          placeholder={`Ask ${brand.short} anything...`}
          placeholderTextColor={colors.gray400}
          returnKeyType="send"
          onSubmitEditing={sendMessage}
          editable={!sending && !recording}
        />
        <TouchableOpacity
          style={[styles.sendButton, (!input.trim() || sending) && styles.sendDisabled]}
          onPress={sendMessage}
          disabled={!input.trim() || sending}
        >
          <Text style={styles.sendText}>Send</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.cream },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.cream, gap: 12 },
  emoji: { fontSize: 36 },
  connectingText: { fontSize: 14, color: colors.gray500 },
  messageList: { padding: 16, paddingBottom: 8 },
  bubble: { maxWidth: '80%', borderRadius: 16, padding: 12, marginBottom: 8 },
  userBubble: { alignSelf: 'flex-end', backgroundColor: colors.blue },
  assistantBubble: { alignSelf: 'flex-start', backgroundColor: colors.white, shadowColor: '#000', shadowOffset: { width: 0, height: 1 }, shadowOpacity: 0.05, shadowRadius: 4, elevation: 1 },
  botHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  botName: { fontSize: 11, fontWeight: '700', color: colors.blue },
  playButton: { fontSize: 16, padding: 2 },
  bubbleText: { fontSize: 15, lineHeight: 22, color: colors.gray800 },
  userText: { color: colors.white },
  typingRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 20, paddingBottom: 4 },
  typingText: { fontSize: 13, color: colors.gray400 },
  inputRow: { flexDirection: 'row', padding: 12, paddingBottom: Platform.OS === 'ios' ? 28 : 12, backgroundColor: colors.white, borderTopWidth: 1, borderTopColor: colors.gray200, gap: 8, alignItems: 'center' },
  micButton: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.gray100, justifyContent: 'center', alignItems: 'center' },
  micRecording: { backgroundColor: colors.rose + '22', borderWidth: 2, borderColor: colors.rose },
  micIcon: { fontSize: 18 },
  input: { flex: 1, backgroundColor: colors.gray50, borderRadius: 20, paddingHorizontal: 16, paddingVertical: 10, fontSize: 15, color: colors.gray800 },
  sendButton: { backgroundColor: colors.blue, borderRadius: 20, paddingHorizontal: 20, justifyContent: 'center' },
  sendDisabled: { opacity: 0.5 },
  sendText: { color: colors.white, fontWeight: '600', fontSize: 15 },
})
