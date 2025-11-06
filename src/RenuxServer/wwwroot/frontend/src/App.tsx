import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import HomePage from './pages/home/HomePage'
import SignInPage from './pages/auth/SignInPage'
import SignUpPage from './pages/auth/SignUpPage'
import ChatPage from './pages/chat/ChatPage'
import SettingsPage from './pages/settings/SettingsPage'

function App() {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/auth/in" element={<SignInPage />} />
          <Route path="/auth/up" element={<SignUpPage />} />
          <Route path="/chat/:chatId" element={<ChatPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}

export default App
