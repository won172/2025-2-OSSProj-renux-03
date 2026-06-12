import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import HomePage from './pages/home/HomePage'
import SignInPage from './pages/auth/SignInPage'
import ChatPage from './pages/chat/ChatPage'
import SettingsPage from './pages/settings/SettingsPage'
import UniversityAdminPage from './pages/admin/UniversityAdminPage'
import DepartmentAdminPage from './pages/admin/DepartmentAdminPage'
import ChatLogPage from './pages/admin/ChatLogPage'
import RequireRole from './components/auth/RequireRole'

function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/auth/in" element={<SignInPage />} />
          <Route path="/auth/up" element={<Navigate to="/auth/in" replace />} />
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
      </div>
    </BrowserRouter>
  )
}

export default App
