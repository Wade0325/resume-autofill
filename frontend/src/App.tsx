import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import ProfilePage from './pages/ProfilePage'
import FillPage from './pages/FillPage'
import ImportPage from './pages/ImportPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Navigate to="/profile" replace />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/fill" element={<FillPage />} />
        <Route path="/import" element={<ImportPage />} />
      </Route>
    </Routes>
  )
}
