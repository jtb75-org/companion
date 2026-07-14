/**
 * Tests for the Authentik (self-hosted) login path: the login API call, its
 * status-to-copy error mapping, and session-token storage.
 *
 * These exercise the JS logic directly (no native modules), which is why they
 * are fast and provider-agnostic.
 */
import { authentikLogin, AuthLoginError } from '../src/auth/authApi'
import {
  clearSessionToken,
  getSessionTokenSync,
  loadSessionToken,
  persistSessionToken,
} from '../src/auth/sessionToken'

describe('authentikLogin', () => {
  afterEach(() => {
    // @ts-ignore - reset the mocked fetch between tests
    global.fetch = undefined
  })

  it('sends {username, password, mobile: true} and returns the session token', async () => {
    const fetchMock = jest.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ status: 'ok', session_token: 'sid-123', csrf_token: 'x' }),
      }),
    )
    // @ts-ignore
    global.fetch = fetchMock

    const token = await authentikLogin('member@example.com', 'pw')
    expect(token).toBe('sid-123')

    const init = (fetchMock.mock.calls[0] as unknown[])[1] as { body: string }
    expect(JSON.parse(init.body)).toEqual({
      username: 'member@example.com',
      password: 'pw',
      mobile: true,
    })
  })

  it.each([401, 403, 429])('throws AuthLoginError with status %s', async (status) => {
    // @ts-ignore
    global.fetch = jest.fn(() => Promise.resolve({ ok: false, status }))
    await expect(authentikLogin('u', 'p')).rejects.toMatchObject({
      name: 'AuthLoginError',
      status,
    })
  })

  it('reports a null status on network failure', async () => {
    // @ts-ignore
    global.fetch = jest.fn(() => Promise.reject(new Error('offline')))
    await expect(authentikLogin('u', 'p')).rejects.toBeInstanceOf(AuthLoginError)
    await authentikLogin('u', 'p').catch((e) => expect(e.status).toBeNull())
  })
})

describe('session token storage', () => {
  afterEach(async () => {
    await clearSessionToken()
  })

  it('persists, restores, and clears the token', async () => {
    expect(getSessionTokenSync()).toBeNull()

    await persistSessionToken('sid-abc')
    expect(getSessionTokenSync()).toBe('sid-abc')

    // Simulate an app restart: in-memory cache reloaded from storage.
    const restored = await loadSessionToken()
    expect(restored).toBe('sid-abc')

    await clearSessionToken()
    expect(getSessionTokenSync()).toBeNull()
    expect(await loadSessionToken()).toBeNull()
  })
})
