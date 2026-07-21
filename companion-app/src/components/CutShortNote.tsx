import React from 'react'
import { Text, StyleSheet } from 'react-native'
import { colors } from '../theme/colors'

/**
 * The coarse, non-sensitive category the backend attaches to a cut-short answer.
 * "content" = a safety/content block; "length" = a length/token-budget cut.
 * Anything else (including undefined) falls back to the neutral generic copy.
 */
export type CutReason = 'content' | 'length' | string | undefined

/**
 * Plain-language, calm note shown when D.D.'s answer stopped before it finished.
 *
 * Copy rules (safety-privacy-reviewed): 4th–6th grade reading level, calm and
 * non-urgent, always actionable, and NEVER implies the member did something
 * wrong or that their message was flagged/unsafe. A "content" cut must read
 * exactly as gently as a "length" cut.
 */
export function cutShortNoteText(reason: CutReason): string {
  if (reason === 'length') {
    // The answer hit the length budget mid-sentence — invite a simple continue.
    return 'That answer got long, so I stopped. You can ask me to keep going.'
  }
  // Neutral, non-stigmatizing default for "content" and any unknown reason.
  return 'This answer stopped early. You can try asking again.'
}

/**
 * Whether to show the cut-short note under a chat message. Only assistant turns
 * that were actually cut qualify — user turns and normal, complete answers never
 * show it. Backward-safe: a message with no `cutShort` flag returns false.
 */
export function shouldShowCutShortNote(msg: {
  role: 'user' | 'assistant'
  cutShort?: boolean
}): boolean {
  return msg.role === 'assistant' && msg.cutShort === true
}

/**
 * A soft, muted footnote rendered UNDER an already-shown partial assistant
 * message. Styled like a system hint, not an error banner — additive only; the
 * streamed text above it is unchanged.
 */
export function CutShortNote({ reason }: { reason?: CutReason }) {
  return (
    <Text
      style={styles.note}
      accessibilityRole="text"
      accessibilityLabel={cutShortNoteText(reason)}
    >
      {cutShortNoteText(reason)}
    </Text>
  )
}

const styles = StyleSheet.create({
  // Muted caption: small and quiet, but gray600 on white keeps AA contrast.
  note: {
    marginTop: 8,
    fontSize: 13,
    lineHeight: 18,
    fontStyle: 'italic',
    color: colors.gray600,
  },
})
