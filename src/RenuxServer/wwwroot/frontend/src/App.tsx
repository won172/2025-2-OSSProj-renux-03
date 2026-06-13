import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import RequireRole from './components/auth/RequireRole'

const HomePage = lazy(() => import('./pages/home/HomePage'))
const SignInPage = lazy(() => import('./pages/auth/SignInPage'))
const SignUpPage = lazy(() => import('./pages/auth/SignUpPage'))
const ChatPage = lazy(() => import('./pages/chat/ChatPage'))
const SettingsPage = lazy(() => import('./pages/settings/SettingsPage'))
const UniversityAdminPage = lazy(() => import('./pages/admin/UniversityAdminPage'))
const DepartmentAdminPage = lazy(() => import('./pages/admin/DepartmentAdminPage'))
const ChatLogPage = lazy(() => import('./pages/admin/ChatLogPage'))

function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <Suspense fallback={<div className="route-loading" role="status">화면을 불러오는 중입니다...</div>}>
          <Routes>
            <Route path="/" element={<HomePage />} />
          <Route path="/auth/in" element={<SignInPage />} />
          <Route path="/auth/up" element={<SignUpPage />} />
            <Route path="/chat/:chatId" element={<ChatPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route
              path="/admin/university"
              element={
                <RequireRole allow={['UNIVERSITY_COUNCIL']}>
                  <UniversityAdminPage />
                </RequireRole>
              }
            />
            <Route
              path="/admin/department"
              element={
                <RequireRole allow={['DEPARTMENT_COUNCIL', 'UNIVERSITY_COUNCIL']}>
                  <DepartmentAdminPage />
                </RequireRole>
              }
            />
            <Route
              path="/admin/logs"
              element={
                <RequireRole allow={['UNIVERSITY_COUNCIL']}>
                  <ChatLogPage />
                </RequireRole>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </div>
    </BrowserRouter>
  )
}

export default App
