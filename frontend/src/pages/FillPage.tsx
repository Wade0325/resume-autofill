import { Suspense, lazy, useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type FieldSpec, type Plan, type PlanItem, type PreviewOut } from '../api'
import DocPreview from '../components/DocPreview'
import Dropzone, { type UploadPhase } from '../components/Dropzone'

// pdf.js 佔了主 bundle 一半以上，等真的要顯示預覽時再載
const PdfCompare = lazy(() => import('../components/PdfCompare'))

// 切到別頁再切回來時要能接續，不必重傳檔案重跑一次模型
const KEY_JOB = 'fill.jobId'

export default function FillPage() {
  const [fields, setFields] = useState<FieldSpec[]>([])
  const [plan, setPlan] = useState<Plan | null>(null)
  const [preview, setPreview] = useState<PreviewOut | null>(null)
  const [pdfOk, setPdfOk] = useState(true)
  const [pdfMsg, setPdfMsg] = useState('')
  const [previewVersion, setPreviewVersion] = useState(0)
  const [phase, setPhase] = useState<UploadPhase>({ kind: 'idle' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.fields().then(setFields).catch((e) => setError(e.message))
  }, [])

  // 分析在後端背景執行，這裡輪詢進度；切頁再回來會憑 sessionStorage 接上
  const [trackingId, setTrackingId] = useState<string | null>(() =>
    sessionStorage.getItem(KEY_JOB),
  )
  useEffect(() => {
    if (!trackingId || plan) return
    let stopped = false
    let fails = 0
    const started = Date.now()
    const giveUp = () => {
      sessionStorage.removeItem(KEY_JOB)
      setTrackingId(null)
      setPhase({ kind: 'idle' })
    }
    const poll = () => {
      api
        .getJob(trackingId)
        .then((st) => {
          if (stopped) return
          fails = 0
          if (st.status === 'ready') {
            setPlan(st.plan)
            setPhase({ kind: 'idle' })
          } else if (st.status === 'failed') {
            setError(st.error)
            giveUp()
          } else {
            setPhase({
              kind: 'analyzing',
              startedAt: started,
              stage: st.stage,
            })
          }
        })
        .catch(() => {
          // 後端 --reload 重啟時會斷幾秒，別因為一次失敗就放棄
          if (!stopped && ++fails >= 5) giveUp()
        })
    }
    poll()
    const timer = setInterval(poll, 2000)
    return () => {
      stopped = true
      clearInterval(timer)
    }
  }, [trackingId, plan])

  // 預覽抓不到就退回只有清單，不擋主流程
  const jobId = plan?.job_id
  useEffect(() => {
    setPdfOk(true)
    setPreviewVersion(0)
    if (!jobId) {
      setPreview(null)
      return
    }
    api.getPreview(jobId).then(setPreview).catch(() => setPreview(null))
  }, [jobId])

  // 這台機器沒有 LibreOffice：PDF 永遠做不出來，退回結構化對照
  const onPdfUnavailable = useCallback((message: string) => {
    setPdfOk(false)
    setPdfMsg(message)
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
    setError('')
    setPhase({ kind: 'uploading', percent: 0 })
    try {
      const accepted = await api.analyze(file, (percent) =>
        setPhase({ kind: 'uploading', percent }),
      )
      sessionStorage.setItem(KEY_JOB, accepted.job_id)
      setPhase({ kind: 'analyzing', startedAt: Date.now(), stage: '準備中' })
      setTrackingId(accepted.job_id)
    } catch (e: any) {
      setError(e.requestId ? `${e.message}（追蹤碼 ${e.requestId}）` : e.message)
      setPhase({ kind: 'idle' })
    }
  }

  async function remap(slotId: string, fieldKey: string) {
    if (!plan) return
    const result = await run(() =>
      api.fixMappings(plan.job_id, [{ slot_id: slotId, field_key: fieldKey }]),
    )
    if (result) {
      setPlan(result)
      setPreviewVersion((v) => v + 1) // 讓右邊的 PDF 重新產生
    }
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
          phase={phase}
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
        <Stat label="偵測到" value={plan.stats.slots} unit="個位置" />
        <Stat label="將填入" value={plan.stats.fill} unit="格" tone="sky" />
        <Stat label="略過" value={plan.stats.skip} unit="格" />
        {plan.template_cached && (
          <span className="text-xs bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-full px-3 py-1">
            這份格式看過了，直接沿用上次的對映
          </span>
        )}
        {!plan.llm_available && (
          <span className="text-xs bg-amber-50 text-amber-700 border border-amber-200 rounded-full px-3 py-1">
            模型未啟動
          </span>
        )}
      </div>

      {overwrites.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-md px-4 py-3 text-sm text-amber-800">
          這份文件有 {overwrites.length} 格已經有內容，會被「我的資料」覆蓋掉。
          下方表格以刪除線標示原本的值。
        </div>
      )}

      {pdfOk ? (
        <Suspense fallback={<div className="text-sm text-slate-400 py-8 text-center">預覽載入中…</div>}>
          <PdfCompare jobId={plan.job_id} version={previewVersion} onUnavailable={onPdfUnavailable} />
        </Suspense>
      ) : (
        <>
          <div className="text-xs text-slate-500">{pdfMsg}——改用結構化對照顯示。</div>
          {preview && <DocPreview blocks={preview.blocks} items={plan.items} />}
        </>
      )}

      <details open={!pdfOk && !preview} className="group">
        <summary className="cursor-pointer text-sm text-slate-600 hover:text-slate-900 select-none py-1">
          <span className="group-open:hidden">▸</span>
          <span className="hidden group-open:inline">▾</span> 檢視與修正對映清單（{plan.items.length} 個位置）
        </summary>
        <div className="mt-3">
          <PlanTable plan={plan} fields={fields} busy={busy} onRemap={remap} />
        </div>
      </details>

      <div className="flex items-center justify-between pb-8">
        <button
          onClick={() => {
            sessionStorage.removeItem(KEY_JOB)
            setPlan(null)
            setTrackingId(null)
            setPhase({ kind: 'idle' })
          }}
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
  onRemap: (slotId: string, fieldKey: string) => void
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
                key={item.slot_id}
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
  onRemap: (slotId: string, fieldKey: string) => void
}) {
  const skipped = item.status === 'skip'
  // 模型判斷的、或信心不高的，值得使用者優先看一眼
  const needsReview = !skipped && (item.source === 'model' || item.confidence < 0.8)

  return (
    <tr className={skipped ? 'bg-slate-50/60' : needsReview ? 'bg-amber-50/40' : ''}>
      <td className="px-4 py-2.5">
        <span className={skipped ? 'text-slate-400' : 'text-slate-800'}>{item.label || '—'}</span>
        <span className="block text-xs text-slate-400">{item.slot_id}</span>
      </td>

      <td className="px-4 py-2.5">
        <select
          value={item.field_key}
          disabled={busy}
          onChange={(e) => onRemap(item.slot_id, e.target.value)}
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
    rule: '規則',
    learned: '學過',
    cache: '快取',
    model: '模型',
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
