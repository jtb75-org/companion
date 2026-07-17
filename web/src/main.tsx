import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider } from './shared/auth/AuthProvider'
import LoginPage from './shared/auth/LoginPage'
import ProtectedRoute from './shared/auth/ProtectedRoute'
import AccessDenied from './shared/auth/AccessDenied'
import ProfilePage from './shared/auth/ProfilePage'
import { Layout } from './shared/components/Layout'
import { CaregiverLayout } from './caregiver/CaregiverLayout'
import { OpsLayout } from './ops/OpsLayout'
import { AdminLayout } from './admin/AdminLayout'
import AcceptInvitationPage from './shared/invite/AcceptInvitationPage'
import InvitationExpiredPage from './shared/invite/InvitationExpiredPage'
import ActivatePage from './shared/activate/ActivatePage'
import ForgotPasswordPage from './shared/forgot/ForgotPasswordPage'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/forgot-password" element={<ForgotPasswordPage />} />
            <Route path="/invite/accept" element={<AcceptInvitationPage />} />
            <Route path="/activate" element={<ActivatePage />} />
            <Route path="/invite/expired" element={<InvitationExpiredPage />} />
            <Route path="/unauthorized" element={<AccessDenied />} />
            <Route path="/profile" element={<ProtectedRoute><Layout><ProfilePage /></Layout></ProtectedRoute>} />
            <Route path="/caregiver/*" element={<ProtectedRoute><CaregiverLayout /></ProtectedRoute>} />
            <Route path="/ops/*" element={<ProtectedRoute requiredRole="admin"><OpsLayout /></ProtectedRoute>} />
            <Route path="/admin/*" element={<ProtectedRoute requiredRole="admin"><AdminLayout /></ProtectedRoute>} />
            <Route path="/" element={<Navigate to="/login" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)
