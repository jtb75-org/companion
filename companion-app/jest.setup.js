/**
 * Jest setup: mock the native-module packages so the JS logic can be tested
 * under node. Without these, importing App/AuthProvider throws
 * "Cannot use import statement outside a module" because the Firebase /
 * Google-Signin packages ship untransformed ESM.
 *
 * These mocks are test-only and do not affect app runtime behavior.
 */
/* eslint-env jest */

jest.mock('react-native-keychain', () => {
  // Simple in-memory stand-in for the device keychain, keyed by service name.
  const vault = new Map()
  return {
    getGenericPassword: jest.fn((opts) => {
      const entry = vault.get(opts?.service)
      return Promise.resolve(entry ? entry : false)
    }),
    setGenericPassword: jest.fn((username, password, opts) => {
      vault.set(opts?.service, { username, password })
      return Promise.resolve(true)
    }),
    resetGenericPassword: jest.fn((opts) => {
      vault.delete(opts?.service)
      return Promise.resolve(true)
    }),
  }
})

jest.mock('@react-native-firebase/auth', () => {
  const authFn = () => ({
    currentUser: null,
    onAuthStateChanged: (cb) => {
      cb(null)
      return () => {}
    },
    signInWithEmailAndPassword: jest.fn(() => Promise.resolve()),
    createUserWithEmailAndPassword: jest.fn(() => Promise.resolve()),
    signInWithCredential: jest.fn(() => Promise.resolve()),
    signOut: jest.fn(() => Promise.resolve()),
    sendPasswordResetEmail: jest.fn(() => Promise.resolve()),
  })
  authFn.GoogleAuthProvider = { credential: jest.fn(() => ({})) }
  return { __esModule: true, default: authFn }
})

jest.mock('@react-native-firebase/messaging', () => {
  const messagingFn = () => ({
    getToken: jest.fn(() => Promise.resolve('test-fcm-token')),
    requestPermission: jest.fn(() => Promise.resolve(1)),
    onMessage: jest.fn(() => () => {}),
    onTokenRefresh: jest.fn(() => () => {}),
    setBackgroundMessageHandler: jest.fn(),
  })
  return { __esModule: true, default: messagingFn }
})

jest.mock('@react-native-google-signin/google-signin', () => ({
  GoogleSignin: {
    configure: jest.fn(),
    hasPlayServices: jest.fn(() => Promise.resolve(true)),
    signIn: jest.fn(() => Promise.resolve({ data: { idToken: 'test' } })),
  },
}))

jest.mock('react-native-image-picker', () => ({
  launchImageLibrary: jest.fn(() => Promise.resolve({ assets: [] })),
  launchCamera: jest.fn(() => Promise.resolve({ assets: [] })),
}))
