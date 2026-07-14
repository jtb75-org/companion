/**
 * Tests for the account-activation deep-link flow (self-hosted / Authentik):
 * the /activate link parser, and the validate + set-password API calls with
 * their status-to-copy mapping. These exercise the JS logic directly (no
 * native modules).
 */
import { parseActivationToken } from '../src/navigation/linking'
import {
  validateActivationToken,
  setActivationPassword,
  AuthLoginError,
} from '../src/auth/authApi'

describe('parseActivationToken', () => {
  it('extracts the token from a good activation link', () => {
    expect(
      parseActivationToken('https://app.mydailydignity.com/activate?token=abc123'),
    ).toBe('abc123')
  })

  it('url-decodes the token', () => {
    expect(
      parseActivationToken('https://app.mydailydignity.com/activate?token=a%2Bb%3Dc'),
    ).toBe('a+b=c')
  })

  it('picks token out of multiple query params', () => {
    expect(
      parseActivationToken('https://app.mydailydignity.com/activate?x=1&token=t9&y=2'),
    ).toBe('t9')
  })

  it.each([
    null,
    undefined,
    '',
    'https://app.mydailydignity.com/activate', // no token
    'https://app.mydailydignity.com/other?token=t', // wrong path
    'https://evil.example.com/activate?token=t', // wrong host
    'https://app.mydailydignity.com/activate?token=', // empty token
    'not-a-url',
    // Lookalike host: our host as a SUBDOMAIN/PREFIX of an attacker domain must be
    // rejected by the EXACT host check (a substring match would have accepted it).
    'https://app.mydailydignity.com.evil.example/activate?token=t',
    'https://evil.example.com/app.mydailydignity.com/activate?token=t',
    // Lookalike path: /activate only as a substring/prefix of a different path.
    'https://app.mydailydignity.com/activateXYZ?token=t',
    'https://app.mydailydignity.com/not/activate?token=t',
    // Host embedded in the query, not the authority.
    'https://evil.example.com/x?to=app.mydailydignity.com/activate&token=t',
  ])('returns null for a bad, unrelated, or lookalike link: %s', (input) => {
    expect(parseActivationToken(input as string | null)).toBeNull()
  })
})

describe('validateActivationToken', () => {
  afterEach(() => {
    // @ts-ignore
    global.fetch = undefined
  })

  it('returns {valid, email, name} on 200', async () => {
    // @ts-ignore
    global.fetch = jest.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ valid: true, email: 'm@x.com', name: 'Mia' }),
      }),
    )
    const details = await validateActivationToken('tok')
    expect(details).toEqual({ valid: true, email: 'm@x.com', name: 'Mia' })
  })

  it('throws AuthLoginError(404) for an expired/unknown link', async () => {
    // @ts-ignore
    global.fetch = jest.fn(() => Promise.resolve({ ok: false, status: 404 }))
    await expect(validateActivationToken('tok')).rejects.toMatchObject({
      name: 'AuthLoginError',
      status: 404,
    })
  })

  it('reports a null status on network failure', async () => {
    // @ts-ignore
    global.fetch = jest.fn(() => Promise.reject(new Error('offline')))
    await expect(validateActivationToken('tok')).rejects.toBeInstanceOf(AuthLoginError)
  })
})

describe('setActivationPassword', () => {
  afterEach(() => {
    // @ts-ignore
    global.fetch = undefined
  })

  it('sends {token, password} and returns the email on success', async () => {
    const fetchMock = jest.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ok: true, email: 'm@x.com' }),
      }),
    )
    // @ts-ignore
    global.fetch = fetchMock

    const email = await setActivationPassword('tok', 'sup3rsecret')
    expect(email).toBe('m@x.com')

    const init = (fetchMock.mock.calls[0] as unknown[])[1] as { body: string }
    expect(JSON.parse(init.body)).toEqual({ token: 'tok', password: 'sup3rsecret' })
  })

  it.each([400, 502])('throws AuthLoginError with status %s', async (status) => {
    // @ts-ignore
    global.fetch = jest.fn(() => Promise.resolve({ ok: false, status }))
    await expect(setActivationPassword('tok', 'pw')).rejects.toMatchObject({
      name: 'AuthLoginError',
      status,
    })
  })
})
