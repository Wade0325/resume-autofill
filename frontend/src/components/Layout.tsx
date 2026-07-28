import { useCallback, useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { api, type ModelInfo, type ModelsOut } from '../api'

const TABS = [
  { to: '/profile', label: '我的資料', hint: '只需填一次' },
  { to: '/fill', label: '填寫履歷', hint: '空白表格自動填' },
  { to: '/import', label: '匯入履歷', hint: '從舊履歷抓資料' },
  { to: '/logs', label: '日誌', hint: '處理過程紀錄' },
]

export default function Layout() {
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
          <ModelMenu />
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8">
        <Outlet />
      </main>
    </div>
  )
}

/** 短名：清單和膠囊裡不用重複量化後綴 */
function shortName(name: string) {
  return name.replace(/-Q4_K_M$/, '')
}

function ModelMenu() {
  const [info, setInfo] = useState<ModelsOut | null>(null)
  const [offline, setOffline] = useState(true)
  const [open, setOpen] = useState(false)
  const [err, setErr] = useState('')

  const refresh = useCallback(() => {
    api
      .models()
      .then((i) => {
        setInfo(i)
        setOffline(false)
      })
      .catch(() => setOffline(true))
  }, [])

  // 切換或下載進行中就抓快一點，讓進度看起來是活的
  const busy = !!info && (!!info.starting || info.models.some((m) => m.downloading))
  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, open || busy ? 2000 : 10000)
    return () => clearInterval(timer)
  }, [refresh, open, busy])

  const switchTo = (m: ModelInfo) => {
    if (
      !window.confirm(
        `切換到「${shortName(m.name)}」會重啟推論引擎，進行中的解析會中斷。要繼續嗎？`,
      )
    )
      return
    setErr('')
    api.selectModel(m.name).then(refresh).catch((e) => setErr(e.message))
  }

  const download = (m: ModelInfo) => {
    if (!window.confirm(`要下載「${shortName(m.name)}」嗎？檔案約 ${m.size_gb} GB。`)) return
    setErr('')
    api.downloadModel(m.name).then(refresh).catch((e) => setErr(e.message))
  }

  const pill = pillState(info, offline)

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title={pill.title}
        className={`text-xs px-2.5 py-1 rounded-full border whitespace-nowrap transition ${pill.style}`}
      >
        {pill.text} <span className="opacity-60">▾</span>
      </button>

      {open && <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />}
      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 bg-white border border-slate-200 rounded-lg shadow-lg z-30 overflow-hidden">
          <div className="px-4 py-2 text-xs text-slate-500 border-b border-slate-100">
            推論模型（切換會重啟引擎，約需 1～2 分鐘）
          </div>
          {offline || !info ? (
            <p className="text-sm text-slate-400 text-center py-6">後端未連線</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {info.models.map((m) => (
                <li key={m.name} className="px-4 py-3 flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-slate-800">{shortName(m.name)}</div>
                    <div className="text-xs text-slate-400">
                      {m.size_gb} GB{m.note ? `・${m.note}` : ''}
                    </div>
                    {m.error && <div className="text-xs text-rose-600 mt-1">{m.error}</div>}
                  </div>
                  <ModelAction
                    m={m}
                    starting={info.starting}
                    running={info.running}
                    onSwitch={switchTo}
                    onDownload={download}
                  />
                </li>
              ))}
            </ul>
          )}
          {err && (
            <div className="px-4 py-2 text-xs text-rose-600 border-t border-slate-100">{err}</div>
          )}
        </div>
      )}
    </div>
  )
}

function ModelAction({
  m,
  starting,
  running,
  onSwitch,
  onDownload,
}: {
  m: ModelInfo
  starting: string | null
  running: boolean
  onSwitch: (m: ModelInfo) => void
  onDownload: (m: ModelInfo) => void
}) {
  if (starting === m.name)
    return <span className="text-xs text-sky-600 whitespace-nowrap">啟動中…</span>
  if (m.active && running)
    return <span className="text-xs text-emerald-600 whitespace-nowrap">✓ 使用中</span>
  if (m.downloading)
    return <span className="text-xs text-sky-600 whitespace-nowrap">下載中 {m.progress}%</span>
  if (m.downloaded)
    return (
      <button
        onClick={() => onSwitch(m)}
        disabled={!!starting}
        className="text-xs px-3 py-1.5 rounded-md border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-40 whitespace-nowrap"
      >
        {m.active ? '重新啟動' : '切換'}
      </button>
    )
  if (m.downloadable)
    return (
      <button
        onClick={() => onDownload(m)}
        className="text-xs px-3 py-1.5 rounded-md border border-sky-200 text-sky-700 hover:bg-sky-50 whitespace-nowrap"
      >
        下載
      </button>
    )
  return null
}

function pillState(info: ModelsOut | null, offline: boolean) {
  if (offline || !info)
    return {
      style: 'bg-rose-50 text-rose-700 border-rose-200',
      text: '後端未連線',
      title: '請確認 uvicorn 已啟動',
    }
  if (info.starting)
    return {
      style: 'bg-sky-50 text-sky-700 border-sky-200',
      text: `啟動中 · ${shortName(info.starting)}`,
      title: '模型載入中，約需 1～2 分鐘',
    }
  if (info.running)
    return {
      style: 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100',
      text: `就緒 · ${shortName(info.active)}`,
      title: info.active,
    }
  return {
    style: 'bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100',
    text: '模型未啟動',
    title: '點開可選擇並啟動模型',
  }
}
