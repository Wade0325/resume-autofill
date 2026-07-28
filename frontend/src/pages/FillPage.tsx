import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type FieldSpec, type Plan, type PlanItem } from '../api'
import Dropzone from '../components/Dropzone'

export default function FillPage() {
  const [fields, setFields] = useState<FieldSpec[]>([])
  const [plan, setPlan] = useState<Plan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.fields().then(setFields).catch((e) => setError(e.message))
  }, [])

  async function run<T>(work: () => Promise<T>): Promise<T | undefined> {
    setBusy(true)
    setError('')
    try {
      return await work()
    } catch (e: any) {
      setError(e.requestId ? `${e.message}（追蹤碼 ${e.requestId}）` : e.message)
    } finally {
      setBusy(false)
    }
  }

  async function upload(file: File) {
    const result = await run(() => api.analyze(file))
    if (result) setPlan(result)
  }

  async function remap(anchorId: string, fieldKey: string) {
    if (!plan) return
    const result = await run(() =>
      api.fixMappings(plan.job_id, [{ anchor_id: anchorId, field_key: fieldKey }]),
    )
    if (result) setPlan(result)
  }

  async function applyAndDownload() {
    if (!plan) return
    const result = await run(() => api.makeOutput(plan.job_id))
    if (result) window.location.href = api.downloadUrl(plan.job_id)
  }

  if (!plan) {
    return (
      <div className="space-y-6">
        <Header
          title="填寫履歷"
          desc="上傳公司給的空白履歷表，系統會自動判斷每一格該填什麼。"
        />
        {error && <ErrorBox message={error} />}
        <Dropzone
          title="把空白履歷表拖到這裡"
          hint="或點擊選擇檔案"
          busy={busy}
          busyText="解析中，第一次遇到的格式需要呼叫模型，請稍候…"
          onFile={upload}
        />
      </div>
    )
  }

  const overwrites = plan.items.filter(
    (i) => i.status === 'fill' && i.kind !== 'checkbox' && i.existing.trim(),
  )

  return (
    <div className="space-y-6">
      <Header title="填寫履歷" desc={plan.filename} />
      {error && <ErrorBox message={error} />}

      <div className="flex flex-wrap items-center gap-3">
        <Stat label="偵測到" value={plan.stats.anchors} unit="個位置" />
        <Stat label="將填入" value={plan.stats.fill} unit="格" tone="sky" />
        <Stat label="略過" value={plan.stats.skip} unit="格" />
        {plan.template_cached && (
          <span className="text-xs bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-full px-3 py-1">
            這份格式看過了，直接沿用上次的對映
          </span>
        )}
        {!plan.llm_available && (
          <span className="text-xs bg-amber-50 text-amber-700 border border-amber-200 rounded-full px-3 py-1">
            模型未啟動，僅規則比對
          </span>
        )}
      </div>

      {overwrites.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-md px-4 py-3 text-sm text-amber-800">
          這份文件有 {overwrites.length} 格已經有內容，會被「我的資料」覆蓋掉。
          下方表格以刪除線標示原本的值。
        </div>
      )}

      <PlanTable plan={plan} fields={fields} busy={busy} onRemap={remap} />

      <div className="flex items-center justify-between pb-8">
        <button
          onClick={() => setPlan(null)}
          className="text-sm text-slate-500 hover:text-slate-800"
        >
          ← 換一份檔案
        </button>
        <button
          onClick={applyAndDownload}
          disabled={busy || plan.stats.fill === 0}
          className="px-6 py-2.5 rounded-md bg-sky-600 text-white font-medium
                     hover:bg-sky-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          {busy ? '處理中…' : `套用並下載（${plan.stats.fill} 格）`}
        </button>
      </div>
    </div>
  )
}

function PlanTable({
  plan,
  fields,
  busy,
  onRemap,
}: {
  plan: Plan
  fields: FieldSpec[]
  busy: boolean
  onRemap: (anchorId: string, fieldKey: string) => void
}) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600 text-xs">
            <tr>
              <th className="text-left px-4 py-3 font-medium">表格上的標籤</th>
              <th className="text-left px-4 py-3 font-medium">對應到我的欄位</th>
              <th className="text-left px-4 py-3 font-medium">文件原本的值</th>
              <th className="text-left px-4 py-3 font-medium">將填入</th>
              <th className="text-left px-4 py-3 font-medium">來源</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {plan.items.map((item) => (
              <Row
                key={item.anchor_id}
                item={item}
                fields={fields}
                busy={busy}
                onRemap={onRemap}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Row({
  item,
  fields,
  busy,
  onRemap,
}: {
  item: PlanItem
  fields: FieldSpec[]
  busy: boolean
  onRemap: (anchorId: string, fieldKey: string) => void
}) {
  const skipped = item.status === 'skip'
  // 模型判斷的、或信心不高的，值得使用者優先看一眼
  const needsReview = !skipped && (item.source === 'llm' || item.confidence < 0.8)

  return (
    <tr className={skipped ? 'bg-slate-50/60' : needsReview ? 'bg-amber-50/40' : ''}>
      <td className="px-4 py-2.5">
        <span className={skipped ? 'text-slate-400' : 'text-slate-800'}>{item.label || '—'}</span>
        <span className="block text-xs text-slate-400">{item.anchor_id}</span>
      </td>

      <td className="px-4 py-2.5">
        <select
          value={item.field_key}
          disabled={busy}
          onChange={(e) => onRemap(item.anchor_id, e.target.value)}
          className="text-sm border border-slate-300 rounded px-2 py-1 max-w-56
                     focus:outline-none focus:ring-2 focus:ring-sky-500"
        >
          <option value="__SKIP__">（不填這格）</option>
          <option value="__UNKNOWN__">（找不到對應）</option>
          {fields.map((f) => (
            <option key={f.key} value={f.key}>
              {f.label}
            </option>
          ))}
        </select>
      </td>

      <td className="px-4 py-2.5">
        {/* 勾選題的 existing 是選項清單（「□男 □女」）而不是既有答案，
            打勾也不會抹掉它，所以不該顯示成「將被覆蓋」 */}
        {item.kind !== 'checkbox' && item.existing.trim() ? (
          <span>
            <span className="line-through text-slate-400">{item.existing.slice(0, 24)}</span>
            {!skipped && (
              <span className="ml-2 text-xs bg-amber-100 text-amber-800 rounded px-1.5 py-0.5">
                將被覆蓋
              </span>
            )}
          </span>
        ) : (
          <span className="text-slate-300">—</span>
        )}
      </td>

      <td className="px-4 py-2.5">
        {skipped ? (
          <SkipReason note={item.note} />
        ) : (
          <span className="text-slate-900">{item.value.slice(0, 30)}</span>
        )}
      </td>

      <td className="px-4 py-2.5 text-xs text-slate-500">
        {skipped ? '—' : `${sourceLabel(item.source)} ${item.confidence.toFixed(2)}`}
      </td>
    </tr>
  )
}

function SkipReason({ note }: { note: string }) {
  if (note.includes('此欄位為空')) {
    return (
      <span className="text-xs text-slate-500">
        {note}．
        <Link to="/profile" className="text-sky-600 hover:underline">
          去填寫
        </Link>
      </span>
    )
  }
  return <span className="text-xs text-slate-500">{note}</span>
}

function sourceLabel(source: string) {
  const names: Record<string, string> = {
    cache: '快取',
    rule: '規則',
    fuzzy: '近似',
    llm: '模型',
    manual: '手動',
  }
  return names[source] ?? source
}

function Header({ title, desc }: { title: string; desc: string }) {
  return (
    <div>
      <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
      <p className="text-sm text-slate-500 mt-1">{desc}</p>
    </div>
  )
}

function Stat({
  label,
  value,
  unit,
  tone,
}: {
  label: string
  value: number
  unit: string
  tone?: string
}) {
  return (
    <span className="text-sm text-slate-600">
      {label}{' '}
      <strong className={tone === 'sky' ? 'text-sky-700 text-base' : 'text-slate-900 text-base'}>
        {value}
      </strong>{' '}
      {unit}
    </span>
  )
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="bg-rose-50 border border-rose-200 text-rose-700 rounded-md px-4 py-3 text-sm">
      {message}
    </div>
  )
}
