import { Navigate, createBrowserRouter } from 'react-router-dom'
import Layout from './components/Layout'
import ProfilePage from './pages/ProfilePage'
import FillPage from './pages/FillPage'
import ImportPage from './pages/ImportPage'
import LogPage from './pages/LogPage'
import WebFormPage from './pages/WebFormPage'

// 用 data router（不是 <BrowserRouter>）：「我的資料」有未存的變更時，
// 要靠 useBlocker 攔住換頁——點上方分頁、按瀏覽器上一頁都算
export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: '/', element: <Navigate to="/profile" replace /> },
      { path: '/profile', element: <ProfilePage /> },
      { path: '/fill', element: <FillPage /> },
      { path: '/import', element: <ImportPage /> },
      { path: '/webform', element: <WebFormPage /> },
      { path: '/logs', element: <LogPage /> },
      // 打錯的網址回到首頁，不要掉進 React Router 內建的錯誤畫面
      { path: '*', element: <Navigate to="/profile" replace /> },
    ],
  },
])
