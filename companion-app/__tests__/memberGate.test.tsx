/**
 * Tests for the post-login access decision (resolveAppAccess).
 *
 * The decision is pure over an injected `apiFn`, so we drive it with a small
 * fake that maps each path to a response or an ApiError — no fetch, no native
 * modules. Each case maps a backend signal to the intended routing outcome:
 *   member -> proceed, non-member -> gate, invalid -> signOut, transient -> unknown.
 */
import { resolveAppAccess } from '../src/auth/memberGate'
import { ApiError } from '../src/api/client'

/**
 * Build a fake `api()` from a table of path -> handler. A handler returns the
 * JSON body, or throws (e.g. an ApiError) to simulate a non-2xx.
 */
function fakeApi(handlers: Record<string, () => unknown>) {
  const calls: string[] = []
  const fn = async <T,>(path: string): Promise<T> => {
    calls.push(path)
    const handler = handlers[path]
    if (!handler) throw new ApiError(404)
    return handler() as T
  }
  return { fn, calls }
}

describe('resolveAppAccess', () => {
  it('member with a complete profile -> proceed (profileComplete true)', async () => {
    const { fn } = fakeApi({
      '/api/v1/me': () => ({ exists: true, profile_complete: true }),
    })
    await expect(resolveAppAccess(fn)).resolves.toEqual({
      kind: 'member',
      profileComplete: true,
    })
  })

  it('member with an incomplete profile -> proceed (profileComplete false)', async () => {
    const { fn } = fakeApi({
      '/api/v1/me': () => ({ exists: true, profile_complete: false }),
    })
    await expect(resolveAppAccess(fn)).resolves.toEqual({
      kind: 'member',
      profileComplete: false,
    })
  })

  it('member returned as a raw user model -> profileComplete from first+last name', async () => {
    const { fn } = fakeApi({
      '/api/v1/me': () => ({ first_name: 'Mia', last_name: 'Lopez' }),
    })
    await expect(resolveAppAccess(fn)).resolves.toEqual({
      kind: 'member',
      profileComplete: true,
    })
  })

  it('does NOT call /auth/check on the member happy path', async () => {
    const { fn, calls } = fakeApi({
      '/api/v1/me': () => ({ exists: true, profile_complete: true }),
    })
    await resolveAppAccess(fn)
    expect(calls).toEqual(['/api/v1/me'])
  })

  it.each([401, 403])(
    'caregiver on the member app (/me %s, /auth/check 200 caregiver) -> gate',
    async (meStatus) => {
      const { fn, calls } = fakeApi({
        '/api/v1/me': () => {
          throw new ApiError(meStatus)
        },
        '/api/v1/auth/check': () => ({ authorized: true, role: 'caregiver' }),
      })
      await expect(resolveAppAccess(fn)).resolves.toEqual({ kind: 'nonMember' })
      expect(calls).toEqual(['/api/v1/me', '/api/v1/auth/check'])
    },
  )

  it('admin on the member app (/me 401, /auth/check 200 admin) -> gate', async () => {
    const { fn } = fakeApi({
      '/api/v1/me': () => {
        throw new ApiError(401)
      },
      '/api/v1/auth/check': () => ({ authorized: true, role: 'admin' }),
    })
    await expect(resolveAppAccess(fn)).resolves.toEqual({ kind: 'nonMember' })
  })

  it('expired session (/me 401, /auth/check 401) -> invalid (signOut)', async () => {
    const { fn } = fakeApi({
      '/api/v1/me': () => {
        throw new ApiError(401)
      },
      '/api/v1/auth/check': () => {
        throw new ApiError(401)
      },
    })
    await expect(resolveAppAccess(fn)).resolves.toEqual({ kind: 'invalid' })
  })

  it('plain unauthorized member (/me 403, /auth/check 403) -> invalid (signOut)', async () => {
    const { fn } = fakeApi({
      '/api/v1/me': () => {
        throw new ApiError(403)
      },
      '/api/v1/auth/check': () => {
        throw new ApiError(403)
      },
    })
    await expect(resolveAppAccess(fn)).resolves.toEqual({ kind: 'invalid' })
  })

  it('/auth/check 200 with an unexpected role -> invalid (not gated)', async () => {
    const { fn } = fakeApi({
      '/api/v1/me': () => {
        throw new ApiError(401)
      },
      '/api/v1/auth/check': () => ({ authorized: true, role: 'unauthorized' }),
    })
    await expect(resolveAppAccess(fn)).resolves.toEqual({ kind: 'invalid' })
  })

  it('transient /me failure (500) -> unknown (does not probe /auth/check)', async () => {
    const { fn, calls } = fakeApi({
      '/api/v1/me': () => {
        throw new ApiError(500)
      },
    })
    await expect(resolveAppAccess(fn)).resolves.toEqual({ kind: 'unknown' })
    expect(calls).toEqual(['/api/v1/me'])
  })

  it('network error on /me (non-ApiError) -> unknown', async () => {
    const { fn } = fakeApi({
      '/api/v1/me': () => {
        throw new Error('offline')
      },
    })
    await expect(resolveAppAccess(fn)).resolves.toEqual({ kind: 'unknown' })
  })
})
