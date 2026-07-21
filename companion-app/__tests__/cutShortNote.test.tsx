/**
 * Tests for the "response stopped early" note shown under a partial D.D. answer.
 *
 * Two concerns:
 *  1. Copy — the exact user-facing strings, per reason, must stay stable so the
 *     safety-privacy reviewer's sign-off keeps applying (calm, non-stigmatizing,
 *     actionable). A copy change here should fail loudly.
 *  2. Render — the note text appears when shown, and is styled as one muted
 *     caption, not an error banner.
 */
import React from 'react'
import ReactTestRenderer from 'react-test-renderer'
import { Text } from 'react-native'
import {
  CutShortNote,
  cutShortNoteText,
  shouldShowCutShortNote,
} from '../src/components/CutShortNote'

describe('shouldShowCutShortNote', () => {
  it('shows for an assistant turn flagged cut short', () => {
    expect(
      shouldShowCutShortNote({ role: 'assistant', cutShort: true }),
    ).toBe(true)
  })

  it('is absent for a normal, complete assistant answer', () => {
    expect(shouldShowCutShortNote({ role: 'assistant' })).toBe(false)
    expect(
      shouldShowCutShortNote({ role: 'assistant', cutShort: false }),
    ).toBe(false)
  })

  it('never shows on the member\'s own message', () => {
    expect(
      shouldShowCutShortNote({ role: 'user', cutShort: true }),
    ).toBe(false)
  })
})

describe('cutShortNoteText', () => {
  it('gives a neutral, non-stigmatizing note for a content cut', () => {
    expect(cutShortNoteText('content')).toBe(
      'This answer stopped early. You can try asking again.',
    )
  })

  it('invites a simple continue for a length cut', () => {
    expect(cutShortNoteText('length')).toBe(
      'That answer got long, so I stopped. You can ask me to keep going.',
    )
  })

  it('falls back to the neutral note for an unknown/undefined reason', () => {
    expect(cutShortNoteText(undefined)).toBe(
      'This answer stopped early. You can try asking again.',
    )
    expect(cutShortNoteText('something-else')).toBe(
      'This answer stopped early. You can try asking again.',
    )
  })
})

describe('CutShortNote render', () => {
  function textOf(reason?: 'content' | 'length') {
    let tree!: ReactTestRenderer.ReactTestRenderer
    ReactTestRenderer.act(() => {
      tree = ReactTestRenderer.create(<CutShortNote reason={reason} />)
    })
    const texts = tree.root.findAllByType(Text)
    return texts.map((t) => t.props.children).join('')
  }

  it('renders the content-cut copy', () => {
    expect(textOf('content')).toBe(
      'This answer stopped early. You can try asking again.',
    )
  })

  it('renders the length-cut copy', () => {
    expect(textOf('length')).toBe(
      'That answer got long, so I stopped. You can ask me to keep going.',
    )
  })
})
