import { useState, useRef, useEffect } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { BRAND_SHORT } from '../branding'

const navSections = [
  {
    label: 'Caregiver',
    links: [
      { to: '/caregiver/alerts', label: 'Alerts' },
      { to: '/caregiver/dashboard', label: 'Dashboard' },
      { to: '/caregiver/activity', label: 'Activity' },
    ],
  },
  {
    label: 'Ops',
    links: [
      { to: '/ops/pipeline', label: 'Pipeline' },
      { to: '/ops/escalations', label: 'Escalations' },
      { to: '/ops/metrics', label: 'Metrics' },
    ],
  },
  {
    label: 'Admin',
    links: [
      { to: '/admin/prompts', label: 'Prompts' },
      { to: '/admin/thresholds', label: 'Thresholds' },
      { to: '/admin/voices', label: 'Voices' },
      { to: '/admin/notifications', label: 'Notifications' },
      { to: '/admin/audit', label: 'Audit' },
      { to: '/admin/people', label: 'People' },
      { to: '/admin/conversations', label: 'Conversations' },
      { to: '/admin/workers', label: 'Workers' },
      { to: '/admin/settings', label: 'Settings' },
    ],
  },
]

function currentSection(pathname: string): string {
  if (pathname.startsWith('/caregiver')) return 'Caregiver'
  if (pathname.startsWith('/ops')) return 'Ops'
  if (pathname.startsWith('/admin')) return 'Admin'
  return 'D.D.'
}

export function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const section = currentSection(location.pathname)
  const { user, logout, role } = useAuth()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  // Close menu on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  return (
    <div className="flex h-screen bg-companion-cream">
      {/* Sidebar */}
      <nav className="w-56 bg-companion-blue text-white flex-shrink-0 overflow-y-auto flex flex-col">
        <div className="p-4 border-b border-companion-blue-mid">
          <h1 className="text-lg font-bold tracking-wide">{BRAND_SHORT}</h1>
        </div>
        <div className="flex-1">
        {navSections
          .filter((sec) => {
            // Caregivers only see the Caregiver section; admins see all
            if (role === 'admin') return true
            return sec.label === 'Caregiver'
          })
          .map((sec) => (
          <div key={sec.label} className="px-3 py-3">
            <h2 className="text-[10px] font-bold uppercase tracking-[0.15em] text-white/40 mb-2 px-2 mt-1">
              {sec.label}
            </h2>
            {sec.links.map((link) => {
              const active = location.pathname === link.to
              return (
                <Link
                  key={link.to}
                  to={link.to}
                  className={`block px-2 py-1.5 rounded text-sm ${
                    active
                      ? 'bg-companion-blue-mid text-white font-medium'
                      : 'text-companion-blue-light hover:text-white hover:bg-companion-blue-mid/50'
                  }`}
                >
                  {link.label}
                </Link>
              )
            })}
          </div>
        ))}
        </div>
        {user && (
          <div className="p-3 border-t border-companion-blue-mid">
            <div className="text-xs text-companion-blue-light truncate px-2 mb-2">
              {user.email}
            </div>
            <button
              onClick={logout}
              className="w-full text-left px-2 py-1.5 rounded text-sm text-companion-blue-light hover:text-white hover:bg-companion-blue-mid/50 transition"
            >
              Sign Out
            </button>
          </div>
        )}
      </nav>

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="bg-white border-b border-gray-200 px-6 py-3 flex-shrink-0 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-companion-blue">{section}</h2>
          {user && (
            <div className="relative" ref={menuRef}>
              <button
                onClick={() => setMenuOpen(!menuOpen)}
                className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-800 transition"
              >
                {user.email}
                <svg className={`w-3 h-3 transition ${menuOpen ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              {menuOpen && (
                <div className="absolute right-0 mt-2 w-56 bg-white rounded-lg shadow-lg border border-gray-200 py-1 z-50">
                  <div className="px-4 py-2 border-b border-gray-100">
                    <p className="text-sm font-medium text-gray-800">{user.email}</p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {role === 'admin' ? 'Administrator' : 'Caregiver'}
                    </p>
                  </div>
                  <button
                    onClick={() => { setMenuOpen(false); navigate('/profile') }}
                    className="w-full text-left px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 flex items-center gap-2"
                  >
                    <span className="text-gray-400">👤</span> My Profile
                  </button>
                  <div className="border-t border-gray-100 mt-1">
                    <button
                      onClick={() => { setMenuOpen(false); logout(); window.location.href = '/login' }}
                      className="w-full text-left px-4 py-2 text-sm text-red-500 hover:bg-red-50"
                    >
                      Sign Out
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </header>
        <main className="flex-1 overflow-y-auto p-6">
          {children}
        </main>
      </div>
    </div>
  )
}
