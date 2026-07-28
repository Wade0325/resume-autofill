import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type FieldSpec, type ImportPreview } from '../api'
import Dropzone from '../components/Dropzone'

export default function ImportPage() {
  const [fields, setFields] = useState<FieldSpec[]>([])
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    api.fields().then(setFields).catch((e) => setError(e.message))
  }, [])

  async function upload(file: File) {
    setBusy(true)
    setError('')
    try {
      const result = await api.analyzeImport(file)
      setPreview(result)
      setPicked(new Set(result.rows.filter((r) => r.default_checked).map((r) => r.anchor_id)))
    } catch (e: any) {
      setError(e.requestId ? `${e.message}（追蹤碼 ${e.requestId}）` : e.message)
    } finally {
      setBusy(false)
    }
  }

  async function apply() {
    if (!preview) return
    setBusy(true)
    setError('')
    try {
      await api.applyImport(preview.import_id, [...picked])
      // 帶上 ordinal，我的資料頁才知道是第幾筆學歷／經歷被改到
      const changed = preview.rows
        .filter((r) => picked.has(r.anchor_id))
        .map((r) => `${r.field_key}#${r.ordinal}`)
      navigate('/profile', { state: { changed } })
    } catch (e: any) {
      setError(e.message)
      setBusy(false)
    }
  }

  function toggle(anchorId: string) {
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(anchorId)) next.delete(anchorId)
      else next.add(anchorId)
      return next
    })
  }

  if (!preview) {
    return (
      <div className="space-y-6">
        <Header
          title="匯入履歷"
          desc="上傳一份已經填好的履歷，系統會把裡面的資料抽出來存進「我的資料」。"
        />
        {error && <ErrorBox message={error} />}
        <Dropzone
          title="把已填寫的履歷拖到這裡"
          hint="或點擊選擇檔案"
          busy={busy}
          busyText="解析中，正在辨識每一格是什麼欄位…"
          onFile={upload}
        />
      </div>
    )
  }

  const labelOf = (key: string) => fields.find((f) => f.key === key)?.label ?? key
  const conflicts = preview.rows.filter((r) => r.current.trim())

  return (
    <div className="space-y-6">
      <Header title="匯入履歷" desc={preview.filename} />
      {error && <ErrorBox message={error} />}

      <div className="flex flex-wrap items-center gap-4 text-sm text-slate-600">
        <span>
          抽到 <strong className="text-slate-900 text-base">{preview.rows.length}</strong> 個欄位
        </span>
        {conflicts.length > 0 && (
          <span className="text-amber-700">
            其中 {conflicts.length} 個「我的資料」已經有值，預設不勾選
          </span>
        )}
        <div className="flex gap-2 ml-auto">
          <Chip onClick={() => setPicked(new Set(preview.rows.map((r) => r.anchor_id)))}>
            全選
          </Chip>
          <Chip
            onClick={() =>
              setPicked(
                new Set(preview.rows.filter((r) => !r.current.trim()).map((r) => r.anchor_id)),
              )
            }
          >
            只選空白的
          </Chip>
          <Chip onClick={() => setPicked(new Set())}>全不選</Chip>
        </div>
      </div>

      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600 text-xs">
              <tr>
                <th className="w-12 px-4 py-3"></th>
                <th className="text-left px-4 py-3 font-medium">欄位</th>
                <th className="text-left px-4 py-3 font-medium">我的資料現在的值</th>
                <th className="text-left px-4 py-3 font-medium">從履歷抽到的值</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {preview.rows.map((row) => {
                const checked = picked.has(row.anchor_id)
                const willOverwrite = checked && row.current.trim() !== ''
                return (
                  <tr key={row.anchor_id} className={willOverwrite ? 'bg-amber-50/50' : ''}>
                    <td className="px-4 py-2.5">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(row.anchor_id)}
                        className="w-4 h-4 accent-sky-600"
                      />
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-slate-800">{labelOf(row.field_key)}</span>
                      {row.ordinal > 0 && (
                        <span className="ml-1 text-xs text-slate-400">第 {row.ordinal + 1} 筆</span>
                      )}
                      <span className="block text-xs text-slate-400">表格標籤：{row.label}</span>
                    </td>
                    <td className="px-4 py-2.5">
                      {row.current.trim() ? (
                        <span className={willOverwrite ? 'line-through text-slate-400' : 'text-slate-600'}>
                          {row.current.slice(0, 24)}
                        </span>
                      ) : (
                        <span className="text-slate-300">（空白）</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-slate-900">
                      {row.incoming.slice(0, 30)}
                      {willOverwrite && (
                        <span className="ml-2 text-xs bg-amber-100 text-amber-800 rounded px-1.5 py-0.5">
                          將覆蓋
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex items-center justify-between pb-8">
        <button
          onClick={() => setPreview(null)}
          className="text-sm text-slate-500 hover:text-slate-800"
        >
          ← 換一份檔案
        </button>
        <button
          onClick={apply}
          disabled={busy || picked.size === 0}
          className="px-6 py-2.5 rounded-md bg-sky-600 text-white font-medium
                     hover:bg-sky-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          {busy ? '匯入中…' : `匯入勾選的 ${picked.size} 項`}
        </button>
      </div>
    </div>
  )
}

function Chip({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="text-xs px-3 py-1 rounded-full border border-slate-300 text-slate-600 hover:bg-slate-50"
    >
      {children}
    </button>
  )
}

function Header({ title, desc }: { title: string; desc: string }) {
  return (
    <div>
      <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
      <p className="text-sm text-slate-500 mt-1">{desc}</p>
    </div>
  )
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="bg-rose-50 border border-rose-200 text-rose-700 rounded-md px-4 py-3 text-sm">
      {message}
    </div>
  )
}
