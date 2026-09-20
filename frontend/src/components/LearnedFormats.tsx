import { useEffect, useState } from 'react'
import { api, errorText, type LearnedFormat } from '../api'

const ENGINE: Record<string, string> = { vlm: '看版面', classic: '讀文字' }

/**
 * 學過的格式：同一份表格第二次上傳就靠這些，不必再問模型。
 * 填錯或不想留著就「忘掉」，下次上傳會重新判讀。
 */
export default function LearnedFormats({ onError }: { onError: (message: string) => void }) {
  const [rows, setRows] = useState<LearnedFormat[]>([])

  function load() {
    api
      .learnedFormats()
      .then(setRows)
      .catch((e) => onError(errorText(e)))
  }

  useEffect(load, [])

  function forget(row: LearnedFormat) {
    if (!window.confirm(`忘掉這份格式？下次上傳同一份表格會重新判讀（需要模型，約 1～2 分鐘）。`))
      return
    api
      .forgetFormat(row.fingerprint)
      .then(load)
      .catch((e) => onError(errorText(e)))
  }

  if (rows.length === 0) return null
  return (
    <details className="bg-white border border-slate-200 rounded-lg">
      <summary className="cursor-pointer select-none px-4 py-3 text-sm text-slate-700">
        學過的格式（{rows.length}）
        <span className="ml-2 text-xs text-slate-500">
          同一份表格第二次上傳幾秒完成，靠的就是這些
        </span>
      </summary>
      <ul className="px-4 pb-3 divide-y divide-slate-100">
        {rows.map((row) => (
          <li key={row.fingerprint} className="flex items-center gap-3 py-2.5 text-sm">
            <span className="w-28 text-slate-500 tabular-nums">{day(row.updated_at)}</span>
            <span className="flex-1 text-slate-800 truncate" title={row.source_name}>
              {row.source_name || '（沒有記到檔名）'}
            </span>
            <span className="text-xs text-slate-400 whitespace-nowrap">
              {ENGINE[row.engine] ?? row.engine}・{row.slots} 個位置
            </span>
            <button
              onClick={() => forget(row)}
              className="text-xs px-3 py-1 rounded-md border border-slate-200 text-slate-400
                         hover:bg-rose-50 hover:text-rose-600 hover:border-rose-200"
            >
              忘掉
            </button>
          </li>
        ))}
      </ul>
    </details>
  )
}

function day(iso: string): string {
  return new Date(iso).toLocaleDateString('zh-TW', { month: 'numeric', day: 'numeric' })
}
