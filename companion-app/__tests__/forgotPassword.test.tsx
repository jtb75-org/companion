/**
 * Tests for the self-service password-reset request (self-hosted / Authentik):
 * the POST /auth/forgot-password call and its status-to-copy mapping.
 *
 * The load-bearing property here is ANTI-ENUMERATION: the backend answers every
 * request with an identical 200, and the client must not read or branch on the
 * body — so a caller can never learn whether an address has an account. These
 * exercise the JS logic directly (no native modules).
 */
import { forgotPassword, AuthLoginError } from '../src/auth/authApi'
import { authStrings } from '../src/auth/authStrings'

describe('forgotPassword', () => {
  afterEach(() => {
    // @ts-ignore
    global.fetch = undefined
  })

  it('POSTs {email} and resolves on a 2xx (generic body ignored)', async () => {
    const fetchMock = jest.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        // Intentionally generic body — forgotPassword() must not read or branch on it.
        json: () => Promise.resolve({ status: 'ok' }),
      }),
    )
    // @ts-ignore
    global.fetch = fetchMock

    await expect(forgotPassword('m@x.com')).resolves.toBeUndefined()

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, { body: string }]
    expect(url).toContain('/auth/forgot-password')
    expect(JSON.parse(init.body)).toEqual({ email: 'm@x.com' })
  })

  it('resolves identically for an unknown address (anti-enumeration)', async () => {
    // The backend returns the SAME 200 for a real account and an unknown one.
    // Whatever we do on success must therefore be indistinguishable.
    const jsonMock = jest.fn(() => Promise.resolve({ status: 'ok' }))
    // @ts-ignore
    global.fetch = jest.fn(() => Promise.resolve({ ok: true, status: 200, json: jsonMock }))

    await expect(forgotPassword('nobody@nowhere.com')).resolves.toBeUndefined()
    // Never inspected: reading the body is how an enumeration leak would start.
    expect(jsonMock).not.toHaveBeenCalled()
  })

  it('throws AuthLoginError(429) when rate-limited', async () => {
    // @ts-ignore
    global.fetch = jest.fn(() => Promise.resolve({ ok: false, status: 429 }))
    await expect(forgotPassword('m@x.com')).rejects.toMatchObject({
      name: 'AuthLoginError',
      status: 429,
    })
  })

  it('throws AuthLoginError(422) for an email the backend rejects', async () => {
    // @ts-ignore
    global.fetch = jest.fn(() => Promise.resolve({ ok: false, status: 422 }))
    await expect(forgotPassword('bad')).rejects.toMatchObject({
      name: 'AuthLoginError',
      status: 422,
    })
  })

  it('reports a null status on network failure', async () => {
    // @ts-ignore
    global.fetch = jest.fn(() => Promise.reject(new Error('offline')))
    await expect(forgotPassword('m@x.com')).rejects.toBeInstanceOf(AuthLoginError)
    await forgotPassword('m@x.com').catch((e) => expect(e.status).toBeNull())
  })
})

describe('forgot-password copy', () => {
  it('never repeats the address back or confirms an account exists', () => {
    // The confirmation must read the same for a real and an unknown address, so
    // it must stay conditional ("If we have that email") and must not interpolate.
    expect(authStrings.forgotSentBody).toContain('If we have that email')
    expect(authStrings.forgotSentBody).not.toMatch(/\{|\$\{/)
  })
})
