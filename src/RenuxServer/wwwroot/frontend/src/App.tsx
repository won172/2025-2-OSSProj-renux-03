import { lazy, Suspense } from 'react'
import { createBrowserRouter, createRoutesFromElements, Navigate, Outlet, Route, RouterProvider } from 'react-router-dom'
import ErrorBoundary from './components/ErrorBoundary'
import { importWithRecovery } from './moduleRecovery'

const HomePage = lazy(() => importWithRecovery(() => import('./pages/home/HomePage')))
const SignInPage = lazy(() => importWithRecovery(() => import('./pages/auth/SignInPage')))
const SignUpPage = lazy(() => importWithRecovery(() => import('./pages/auth/SignUpPage')))
const SettingsPage = lazy(() => importWithRecovery(() => import('./pages/settings/SettingsPage')))
const PrivacyPolicyPage = lazy(() => importWithRecovery(() => import('./pages/legal/PrivacyPolicyPage')))

const AdminLayout = lazy(() => importWithRecovery(() => import('./components/admin/AdminLayout')))
const AdminIndex = lazy(() => importWithRecovery(() => import('./components/admin/AdminIndex')))
const UniversityOnly = lazy(() => importWithRecovery(() => import('./components/admin/UniversityOnly')))
const DashboardPage = lazy(() => importWithRecovery(() => import('./pages/admin/DashboardPage')))
const ReviewPage = lazy(() => importWithRecovery(() => import('./pages/admin/ReviewPage')))
const ContentPage = lazy(() => importWithRecovery(() => import('./pages/admin/ContentPage')))
const ChatLogPage = lazy(() => importWithRecovery(() => import('./pages/admin/ChatLogPage')))
const FeedbackPage = lazy(() => importWithRecovery(() => import('./pages/admin/FeedbackPage')))
const UsersPage = lazy(() => importWithRecovery(() => import('./pages/admin/UsersPage')))
const SystemPage = lazy(() => importWithRecovery(() => import('./pages/admin/SystemPage')))
const DepartmentAdminPage = lazy(() => importWithRecovery(() => import('./pages/admin/DepartmentAdminPage')))

function AppShell() {
  return <div className="app-shell">
    <ErrorBoundary>
      <Suspense fallback={<div className="route-loading" role="status">화면을 불러오는 중입니다...</div>}>
        <Outlet />
      </Suspense>
    </ErrorBoundary>
  </div>
}

// A data router supplies navigation blocking for unsaved settings, including browser Back.
const router = createBrowserRouter(createRoutesFromElements(
  <Route element={<AppShell />}>
    <Route path="/" element={<HomePage />} />
    <Route path="/auth/in" element={<SignInPage />} />
    <Route path="/auth/up" element={<SignUpPage />} />
    <Route path="/chat/:chatId" element={<HomePage />} />
    <Route path="/settings" element={<SettingsPage />} />
    <Route path="/privacy" element={<PrivacyPolicyPage />} />

    {/* 이전 URL 북마크 보존 — 재설계로 경로가 바뀌었지만 링크는 계속 동작해야 한다. */}
    <Route path="/admin/university" element={<Navigate to="/admin/dashboard" replace />} />

    {/*
      관리자 콘솔. 셸(AdminLayout)이 역할 확인·사이드바·대기 건수 배지를 담당하고,
      각 업무는 하위 라우트로 분리한다. 셸이 이미 서버 역할을 확인하므로
      하위 가드(UniversityOnly)는 추가 요청 없이 그 결과를 재사용한다.
      세부 권한은 서버 API에서 독립적으로 다시 검증된다.
    */}
    <Route path="/admin" element={<AdminLayout />}>
      <Route index element={<AdminIndex />} />
      <Route path="dashboard" element={<UniversityOnly><DashboardPage /></UniversityOnly>} />
      <Route path="review" element={<UniversityOnly><ReviewPage /></UniversityOnly>} />
      <Route path="content" element={<UniversityOnly><ContentPage /></UniversityOnly>} />
      <Route path="logs" element={<UniversityOnly><ChatLogPage /></UniversityOnly>} />
      <Route path="feedback" element={<UniversityOnly><FeedbackPage /></UniversityOnly>} />
      <Route path="users" element={<UniversityOnly><UsersPage /></UniversityOnly>} />
      <Route path="system" element={<UniversityOnly><SystemPage /></UniversityOnly>} />
      <Route path="department" element={<DepartmentAdminPage />} />
      <Route path="*" element={<Navigate to="/admin/dashboard" replace />} />
    </Route>

    <Route path="*" element={<Navigate to="/" replace />} />
  </Route>,
))

function App() {
  return <RouterProvider router={router} />
}

export default App
