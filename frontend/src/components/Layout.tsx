import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { api, type Health } from '../api'

const TABS = [
  { to: '/profile', label: '我的資料', hint: '只需填一次' },
  { to: '/fill', label: '填寫履歷', hint: '空白表格自動填' },
  { to: '/import', label: '匯入履歷', hint: '從舊履歷抓資料' },
  { to: '/logs', label: '日誌', hint: '處理過程紀錄' },
]

export default function Layout() {
  const [health, setHealth] = useState<Health | null>(null)

  useEffect(() => {
    const poll = () => api.health().then(setHealth).catch(() => setHealth(null))
    poll()
    const timer = setInterval(poll, 15000)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className="min-h-screen">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-6 flex items-center gap-8">
          <span className="font-semibold text-slate-900 py-4">履歷自動填寫</span>
          <nav className="flex gap-1 flex-1">
            {TABS.map((t) => (
              <NavLink
                key={t.to}
                to={t.to}
                className={({ isActive }) =>
                  `px-4 py-4 text-sm border-b-2 -mb-px transition ${
                    isActive
                      ? 'border-sky-600 text-sky-700 font-medium'
                      : 'border-transparent text-slate-500 hover:text-slate-800'
                  }`
                }
              >
                {t.label}
                <span className="block text-xs text-slate-400 font-normal">{t.hint}</span>
              </NavLink>
            ))}
          </nav>
          <EngineStatus health={health} />
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8">
        <Outlet />
      </main>
    </div>
  )
}

function EngineStatus({ health }: { health: Health | null }) {
  if (!health) {
    return <Pill color="rose" text="後端未連線" title="請確認 uvicorn 已啟動" />
  }
  if (!health.llm.available) {
    return (
      <Pill
        color="amber"
        text="模型未啟動"
        title={`${health.llm.host} 連不上，目前只有規則比對可用`}
      />
    )
  }
  return <Pill color="emerald" text="就緒" title={health.llm.model} />
}

function Pill({ color, text, title }: { color: string; text: string; title: string }) {
  const styles: Record<string, string> = {
    rose: 'bg-rose-50 text-rose-700 border-rose-200',
    amber: 'bg-amber-50 text-amber-700 border-amber-200',
    emerald: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  }
  return (
    <span
      title={title}
      className={`text-xs px-2.5 py-1 rounded-full border whitespace-nowrap ${styles[color]}`}
    >
      {text}
    </span>
  )
}
