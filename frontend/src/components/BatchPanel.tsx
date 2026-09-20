import { useEffect, useRef, useState } from 'react'
import { api, errorText, type BatchOutput, type BatchStatus } from '../api'

const STATUS: Record<string, { text: string; style: string }> = {
  analyzed: { text: '分析完成', style: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  processing: { text: '進行中', style: 'bg-sky-50 text-sky-700 border-sky-200' },
  failed: { text: '失敗', style: 'bg-rose-50 text-rose-700 border-rose-200' },
  missing: { text: '不見了', style: 'bg-slate-50 text-slate-600 border-slate-200' },
}

/**
 * 一次丟好幾份時的總覽：每一份各自分析（後端排隊，一次只有一份在問模型），
 * 這裡只負責看進度、逐份檢視、以及全部好了之後一起套用下載。
 *
 * 每一份都是獨立的工作，點「檢視」就是切到平常的單份畫面，能改對映、改值、
 * 列印，跟單獨上傳完全一樣。
 */
export default function BatchPanel({
  jobIds,
  onOpen,
  onRestart,
  onError,
}: {
  jobIds: string[]
  onOpen: (jobId: string) => void
  onRestart: () => void
  onError: (message: string) => void
}) {
  const [rows, setRows] = useState<BatchStatus[]>([])
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState<BatchOutput[] | null>(null)
  // 全部跑完就不必再輪詢了；用 ref 才不會每次 render 都重設計時器
  const doneRef = useRef(false)

  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const next = await api.batchStatus(jobIds)
        if (!alive) return
        setRows(next)
        doneRef.current = next.every((r) => r.status !== 'processing')
      } catch (e) {
        if (alive) onError(errorText(e))
      }
    }
    poll()
    const timer = setInterval(() => {
      if (!doneRef.current) poll()
    }, 2000)
    return () => {
      alive = false
      clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobIds.join(',')])

  const running = rows.filter((r) => r.status === 'processing').length
  const ready = rows.filter((r) => r.status === 'analyzed')
  const failed = rows.filter((r) => r.status === 'failed' || r.status === 'missing')

  async function applyAll() {
    setBusy(true)
    setResults(null)
    try {
      const res = await api.batchOutput(ready.map((r) => r.job_id))
      setResults(res)
      const ok = res.filter((r) => r.ok).map((r) => r.job_id)
      if (ok.length) window.location.href = api.batchZipUrl(ok)
      setRows(await api.batchStatus(jobIds))
    } catch (e) {
      onError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-4">
      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-3">
          <span className="text-sm text-slate-800 font-medium">這一批共 {jobIds.length} 份</span>
          <span className="text-xs text-slate-500">
            {running > 0
              ? `還有 ${running} 份在處理——一次只有一份在問模型，其他排隊等，可以先去做別的事`
              : `分析完成 ${ready.length} 份${failed.length ? `，失敗 ${failed.length} 份` : ''}`}
          </span>
        </div>
        <ul className="divide-y divide-slate-100">
          {rows.map((row, i) => {
            const tag = STATUS[row.status] ?? STATUS.missing
            return (
              <li key={row.job_id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <span className="w-6 text-slate-400 tabular-nums">{i + 1}</span>
                <span className="flex-1 text-slate-800 truncate" title={row.filename}>
                  {row.filename || row.job_id}
                </span>
                {row.status === 'processing' && row.stage && (
                  <span className="text-xs text-slate-500 truncate max-w-64">{row.stage}</span>
                )}
                {row.status === 'analyzed' && row.fill > 0 && (
                  <span className="text-xs text-slate-500 whitespace-nowrap">
                    會填 {row.fill} 格
                  </span>
                )}
                {(row.status === 'failed' || row.status === 'missing') && row.error && (
                  <span className="text-xs text-rose-600 truncate max-w-72" title={row.error}>
                    {row.error}
                  </span>
                )}
                <span
                  className={`text-xs rounded-full border px-2 py-0.5 whitespace-nowrap ${tag.style}`}
                >
                  {tag.text}
                </span>
                {row.status === 'analyzed' && (
                  <button
                    onClick={() => onOpen(row.job_id)}
                    className="text-xs px-3 py-1 rounded-md border border-slate-300
                               text-slate-600 hover:bg-slate-50 whitespace-nowrap"
                  >
                    檢視
                  </button>
                )}
                {row.downloadable && (
                  <a
                    href={api.downloadUrl(row.job_id)}
                    className="text-xs px-3 py-1 rounded-md border border-sky-200
                               text-sky-700 hover:bg-sky-50 whitespace-nowrap"
                  >
                    下載
                  </a>
                )}
              </li>
            )
          })}
        </ul>
      </div>

      {results && results.some((r) => !r.ok) && (
        <div className="px-4 py-3 text-sm text-amber-800 bg-amber-50 border border-amber-200
                        rounded-lg">
          有 {results.filter((r) => !r.ok).length} 份沒有產生出來：
          {results
            .filter((r) => !r.ok)
            .map((r) => `${r.filename}（${r.error}）`)
            .join('、')}
        </div>
      )}

      <div className="flex items-center justify-between gap-3 pb-8">
        <button onClick={onRestart} className="text-sm text-slate-500 hover:text-slate-800">
          ← 換一批檔案
        </button>
        <button
          onClick={applyAll}
          disabled={busy || ready.length === 0}
          className="px-6 py-2.5 rounded-md bg-sky-600 text-white font-medium
                     hover:bg-sky-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          {busy ? '處理中…' : `全部套用並下載（${ready.length} 份）`}
        </button>
      </div>
    </section>
  )
}
