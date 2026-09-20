import { useEffect, useState } from 'react'
import { api, errorText, type JobHistory as Row } from '../api'

const STATUS: Record<string, { text: string; style: string }> = {
  analyzed: { text: '已完成分析', style: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  processing: { text: '分析中', style: 'bg-sky-50 text-sky-700 border-sky-200' },
  failed: { text: '失敗', style: 'bg-rose-50 text-rose-700 border-rose-200' },
}

/**
 * 填寫紀錄：最近填過哪幾份。上傳的檔案保留 24 小時，期間內可以重新開啟或下載，
 * 過期的連同紀錄一起清掉（履歷是個資，不該無限期留著）。
 */
export default function JobHistory({
  onOpen,
  onError,
}: {
  onOpen: (jobId: string) => void
  onError: (message: string) => void
}) {
  const [rows, setRows] = useState<Row[]>([])

  useEffect(() => {
    api
      .jobHistory()
      .then(setRows)
      .catch((e) => onError(errorText(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (rows.length === 0) return null
  return (
    <section className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      <div className="px-4 py-3 text-sm text-slate-700 border-b border-slate-100">
        最近填過的
        <span className="ml-2 text-xs text-slate-500">
          上傳的檔案保留 24 小時，期間內可以重新開啟或下載
        </span>
      </div>
      <ul className="divide-y divide-slate-100">
        {rows.map((row) => {
          const tag = STATUS[row.status] ?? { text: row.status, style: 'bg-slate-50 text-slate-600 border-slate-200' }
          return (
            <li key={row.job_id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
              <span className="w-28 text-slate-500 tabular-nums">{when(row.created_at)}</span>
              <span className="flex-1 text-slate-800 truncate" title={row.filename}>
                {row.filename}
              </span>
              <span className={`text-xs rounded-full border px-2 py-0.5 whitespace-nowrap ${tag.style}`}>
                {tag.text}
              </span>
              {row.status === 'failed' && row.error && (
                <span className="text-xs text-slate-400 truncate max-w-56" title={row.error}>
                  {row.error}
                </span>
              )}
              {row.status !== 'failed' && (
                <button
                  onClick={() => onOpen(row.job_id)}
                  className="text-xs px-3 py-1 rounded-md border border-slate-300 text-slate-600 hover:bg-slate-50"
                >
                  開啟
                </button>
              )}
              {row.downloadable && (
                <a
                  href={api.downloadUrl(row.job_id)}
                  className="text-xs px-3 py-1 rounded-md border border-sky-200 text-sky-700 hover:bg-sky-50"
                >
                  下載
                </a>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

/** 今天的只顯示時間，其他日子帶上月日。 */
function when(iso: string): string {
  const d = new Date(iso)
  const today = new Date().toDateString() === d.toDateString()
  return d.toLocaleString('zh-TW', {
    month: today ? undefined : 'numeric',
    day: today ? undefined : 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}
