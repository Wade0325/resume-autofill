import { useCallback, useEffect, useState } from 'react'
import { api, type LogEntry } from '../api'

const LEVELS = [
  { id: '', label: '全部' },
  { id: 'WARNING', label: '警告以上' },
  { id: 'ERROR', label: '只看錯誤' },
]

export default function LogPage() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [level, setLevel] = useState('')
  const [live, setLive] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    api
      .logs({ level })
      .then((rows) => {
        setEntries(rows)
        setError('')
      })
      .catch((e) => setError(e.message))
  }, [level])

  useEffect(() => {
    load()
    if (!live) return
    // 分頁在背景時看不到畫面，打了也是白打；切回來下一輪自然補上
    const timer = setInterval(() => !document.hidden && load(), 3000)
    return () => clearInterval(timer)
  }, [load, live])

  const clear = () => {
    if (!window.confirm('確定要清除日誌？畫面會清空，舊紀錄仍保留在備份檔裡。')) return
    api
      .clearLogs()
      .then(load)
      .catch((e) => setError(e.message))
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">日誌</h1>
        <p className="text-sm text-slate-500 mt-1">你在這裡做過的操作與結果。</p>
      </div>

      {error && (
        <div className="bg-rose-50 border border-rose-200 text-rose-700 rounded-md px-4 py-3 text-sm">
          {error}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1">
          {LEVELS.map((l) => (
            <button
              key={l.id}
              onClick={() => setLevel(l.id)}
              className={`text-xs px-3 py-1.5 rounded-md border transition ${
                level === l.id
                  ? 'bg-sky-50 border-sky-200 text-sky-800 font-medium'
                  : 'border-slate-300 text-slate-600 hover:bg-slate-50'
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-600 ml-auto">
          <input
            type="checkbox"
            checked={live}
            onChange={(e) => setLive(e.target.checked)}
            className="w-4 h-4 accent-sky-600"
          />
          每 3 秒自動更新
        </label>
        <button
          onClick={load}
          className="text-xs px-3 py-1.5 rounded-md border border-slate-300 text-slate-600 hover:bg-slate-50"
        >
          立即重新整理
        </button>
        <button
          onClick={clear}
          className="text-xs px-3 py-1.5 rounded-md border border-rose-200 text-rose-600 hover:bg-rose-50"
        >
          清除日誌
        </button>
      </div>

      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        {entries.length > 0 && (
          <div className="px-4 py-2 text-xs text-slate-500 border-b border-slate-100">
            最新 {entries.length} 筆，由新到舊
          </div>
        )}
        {entries.length === 0 ? (
          <p className="text-sm text-slate-400 text-center py-12">沒有符合條件的紀錄</p>
        ) : (
          <div className="overflow-auto max-h-[70vh]">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-600 text-xs sticky top-0 z-10">
                <tr>
                  <th className="text-left px-4 py-3 font-medium whitespace-nowrap">時間</th>
                  <th className="text-left px-4 py-3 font-medium">等級</th>
                  <th className="text-left px-4 py-3 font-medium">訊息</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {entries.map((e, i) => (
                  <tr key={i} className={rowTone(e.level)}>
                    <td className="px-4 py-2 text-slate-500 whitespace-nowrap tabular-nums">
                      {e.time}
                    </td>
                    <td className="px-4 py-2">
                      <span className={`text-xs px-1.5 py-0.5 rounded ${levelTone(e.level)}`}>
                        {e.level}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-slate-800 whitespace-pre-wrap break-all">
                      {e.message}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function levelTone(level: string) {
  if (level === 'ERROR') return 'bg-rose-100 text-rose-700'
  if (level === 'WARNING') return 'bg-amber-100 text-amber-800'
  return 'bg-slate-100 text-slate-600'
}

function rowTone(level: string) {
  if (level === 'ERROR') return 'bg-rose-50/50'
  if (level === 'WARNING') return 'bg-amber-50/40'
  return ''
}
