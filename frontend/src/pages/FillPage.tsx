import { Suspense, lazy, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  errorText,
  fetchBlob,
  type FieldSpec,
  type Plan,
  type PlanItem,
  type Profile,
} from '../api'
import { useBackgroundUpload } from '../useBackgroundUpload'
import ApplyPanel from '../components/ApplyPanel'
import Dropzone from '../components/Dropzone'
import JobHistory from '../components/JobHistory'
import LearnedFormats from '../components/LearnedFormats'
import { PageShell, FooterBar, OverwriteBadge } from '../components/common'

// pdf.js 佔了主 bundle 一半以上，等真的要顯示預覽時再載
const DocxCompare = lazy(() => import('../components/DocxCompare'))

// 值是插進原本的字裡的，不會蓋掉表格印好的內容
const INSERTS = new Set(['checkbox', 'print'])

export default function FillPage() {
  const [fields, setFields] = useState<FieldSpec[]>([])
  const [profile, setProfile] = useState<Profile>({}) // 只用來顯示「沿用我的資料：…」
  const [plan, setPlan] = useState<Plan | null>(null)
  const [previewVersion, setPreviewVersion] = useState(0)
  const [busy, setBusy] = useState(false)

  // 分析在後端背景執行，hook 負責上傳、輪詢進度與 sessionStorage 接續
  const { phase, error, setError, upload, track, reset } = useBackgroundUpload({
    storageKey: 'fill.jobId',
    start: async (file, onProgress) => (await api.analyze(file, onProgress)).job_id,
    getState: api.getJob,
    hasResult: !!plan,
    onReady: (st) => {
      setPlan(st.plan)
      setPreviewVersion(0) // 新的一份檔案，預覽版本從頭來——用 effect 歸零會讓右欄多抓一次
    },
  })

  useEffect(() => {
    api.fields().then(setFields).catch((e) => setError(errorText(e)))
    api.getProfile().then(setProfile).catch(() => setProfile({}))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function run<T>(work: () => Promise<T>): Promise<T | undefined> {
    setBusy(true)
    setError('')
    try {
      return await work()
    } catch (e: any) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function remap(slotId: string, fieldKey: string, ordinal?: number) {
    if (!plan) return
    const result = await run(() =>
      api.fixMappings(plan.job_id, [{ slot_id: slotId, field_key: fieldKey, ordinal }]),
    )
    if (result) {
      setPlan(result)
      setPreviewVersion((v) => v + 1) // 讓右邊重新渲染
    }
  }

  /** 分析中按取消：正在問模型的那一批跑完才會停。 */
  async function cancelNow() {
    const id = sessionStorage.getItem('fill.jobId')
    if (!id) return
    await run(() => api.cancelJob(id))
  }

  /** 學過的對映填錯時：重跑模型，這一次不用學過的格式。 */
  async function reanalyzeNow() {
    if (!plan) return
    if (!window.confirm('重新判讀這一份？這次不用學過的格式，要重跑模型（約 1～2 分鐘）。')) return
    const ok = await run(() => api.reanalyze(plan.job_id))
    if (ok) {
      const id = plan.job_id
      setPlan(null)
      track(id) // 回到分析中的畫面，接著輪詢新結果
    }
  }

  async function typeValue(slotId: string, value: string) {
    if (!plan) return
    const result = await run(() => api.setValue(plan.job_id, slotId, value))
    if (result) {
      setPlan(result)
      setPreviewVersion((v) => v + 1)
    }
  }

  async function applyValues(values: Record<string, string>) {
    if (!plan) return
    const result = await run(() => api.setApply(plan.job_id, values))
    if (result) {
      setPlan(result)
      setPreviewVersion((v) => v + 1)
    }
  }

  async function applyAndDownload() {
    if (!plan) return
    const result = await run(() => api.makeOutput(plan.job_id))
    if (result) window.location.href = api.downloadUrl(plan.job_id)
  }

  /** 列印／存成 PDF：拿不標黃底的那份（內容跟下載的成品一樣），交給瀏覽器印。 */
  async function printResult() {
    if (!plan) return
    await run(async () => {
      const blob = await fetchBlob(
        `/jobs/${plan.job_id}/preview.docx?which=filled&highlight=false&v=${previewVersion}`,
      )
      // docx-preview 有 170 KB，跟預覽共用同一個 chunk，等按了才載
      const { printDocx } = await import('../components/docx')
      await printDocx(blob)
    })
  }

  if (!plan) {
    return (
      <PageShell
        title="填寫履歷"
        desc="上傳公司給的空白履歷表，系統會自動判斷每一格該填什麼。"
        error={error}
      >
        <Dropzone
          title="把空白履歷表拖到這裡"
          hint="或點擊選擇檔案"
          phase={phase}
          onFile={upload}
          onCancel={phase.kind === 'analyzing' ? cancelNow : undefined}
        />
        <EnginePicker />
        <JobHistory onOpen={track} onError={setError} />
        <LearnedFormats onError={setError} />
      </PageShell>
    )
  }

  const overwrites = plan.items.filter(
    (i) => i.status === 'fill' && !INSERTS.has(i.kind) && i.existing.trim(),
  )

  return (
    <PageShell title="填寫履歷" desc={plan.filename} error={error}>
      <div className="flex flex-wrap items-center gap-3">
        <Stat label="偵測到" value={plan.stats.slots} unit="個位置" />
        <Stat label="將填入" value={plan.stats.fill} unit="格" tone="sky" />
        <Stat label="略過" value={plan.stats.skip} unit="格" />
        {plan.template_cached && (
          <span className="text-xs bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-full px-3 py-1">
            這份格式看過了，直接沿用上次的對映
          </span>
        )}
        <button
          onClick={reanalyzeNow}
          disabled={busy}
          title="不用學過的格式，重跑一次模型判讀"
          className="text-xs px-3 py-1 rounded-full border border-slate-300 text-slate-600
                     hover:bg-slate-50 disabled:opacity-40"
        >
          重新判讀
        </button>
        {!plan.llm_available && (
          <span className="text-xs bg-amber-50 text-amber-700 border border-amber-200 rounded-full px-3 py-1">
            模型未啟動
          </span>
        )}
      </div>

      <ApplyPanel
        fields={fields}
        plan={plan}
        profile={profile}
        busy={busy}
        onApply={applyValues}
      />

      {plan.form_fields.length > 0 && (
        <details className="bg-slate-50 border border-slate-200 rounded-md px-4 py-3 text-sm">
          <summary className="cursor-pointer text-slate-700">
            模型讀出這份表格要填 <b>{plan.form_fields.length}</b> 個欄位
          </summary>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {plan.form_fields.map((f) => (
              <span key={f} className="text-xs bg-white border border-slate-200 rounded px-2 py-0.5 text-slate-600">
                {f}
              </span>
            ))}
          </div>
        </details>
      )}

      {overwrites.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-md px-4 py-3 text-sm text-amber-800">
          這份文件有 {overwrites.length} 格已經有內容，會被「我的資料」覆蓋掉。
          下方表格以刪除線標示原本的值。
        </div>
      )}

      <Suspense fallback={<div className="text-sm text-slate-400 py-8 text-center">預覽載入中…</div>}>
        <DocxCompare jobId={plan.job_id} version={previewVersion} />
      </Suspense>

      <details className="group">
        <summary className="cursor-pointer text-sm text-slate-600 hover:text-slate-900 select-none py-1">
          <span className="group-open:hidden">▸</span>
          <span className="hidden group-open:inline">▾</span> 檢視與修正對映清單（{plan.items.length} 個位置）
        </summary>
        <div className="mt-3">
          <PlanTable plan={plan} fields={fields} busy={busy} onRemap={remap} onType={typeValue} />
        </div>
      </details>

      <FooterBar
        onRestart={() => {
          reset()
          setPlan(null)
        }}
        onSubmit={applyAndDownload}
        disabled={busy || plan.stats.fill === 0}
        label={busy ? '處理中…' : `套用並下載（${plan.stats.fill} 格）`}
        secondary={
          <button
            onClick={printResult}
            disabled={busy || plan.stats.fill === 0}
            title="開啟瀏覽器的列印視窗；印表機選「另存為 PDF」或「Microsoft Print to PDF」就會存成 PDF"
            className="px-4 py-2.5 rounded-md border border-slate-300 text-sm text-slate-700
                       hover:bg-slate-50 disabled:text-slate-400 disabled:cursor-not-allowed"
          >
            列印／存成 PDF
          </button>
        }
      />
    </PageShell>
  )
}

const ENGINE_TEXT: Record<string, { name: string; desc: string }> = {
  classic: { name: '讀文字', desc: '照表格印的字與列首欄首判斷，模型看不到圖時用這個' },
  vlm: { name: '看版面', desc: '模型看著版面示意圖逐格判斷，沒看過的排版準得多' },
}

/** 用哪一條路填表。放在上傳畫面：換引擎要重新分析，上傳後才換沒有意義。 */
function EnginePicker() {
  const [engine, setEngine] = useState('')
  const [running, setRunning] = useState(true)
  const [vision, setVision] = useState(true)
  const [options, setOptions] = useState<string[]>([])

  useEffect(() => {
    api
      .getEngine()
      .then((e) => {
        setEngine(e.engine)
        setRunning(e.running)
        setVision(e.vision)
        setOptions(e.engines)
      })
      .catch(() => setOptions([]))
  }, [])

  async function pick(next: string) {
    setEngine(next) // 先動，切換是即時回饋；失敗再讀回後端的值
    try {
      await api.setEngine(next)
    } catch {
      const e = await api.getEngine()
      setEngine(e.engine)
    }
  }

  if (options.length < 2) return null
  return (
    <div className="mt-4 text-sm">
      <div className="text-slate-600 mb-2">判斷方式</div>
      <div className="flex flex-wrap gap-2">
        {options.map((key) => {
          const t = ENGINE_TEXT[key] ?? { name: key, desc: '' }
          const on = engine === key
          return (
            <button
              key={key}
              type="button"
              data-testid={`engine-${key}`}
              aria-pressed={on}
              onClick={() => pick(key)}
              className={`text-left rounded-lg border px-4 py-3 max-w-xs transition ${
                on
                  ? 'border-sky-400 bg-sky-50 text-sky-900'
                  : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
              }`}
            >
              <div className="font-medium">{t.name}</div>
              <div className="text-xs mt-0.5 opacity-80">{t.desc}</div>
            </button>
          )
        })}
      </div>
      {!running && (
        <div className="mt-2 text-xs text-amber-700">
          模型還沒啟動：學過的格式照樣能填；沒學過的要先從右上角啟動模型。
        </div>
      )}
      {!vision && engine === 'vlm' && (
        <div className="mt-2 text-xs text-amber-700">
          目前的模型看不到圖，這次會自動改用「讀文字」。要用看版面得換成帶視覺的模型。
        </div>
      )}
    </div>
  )
}

function PlanTable({
  plan,
  fields,
  busy,
  onRemap,
  onType,
}: {
  plan: Plan
  fields: FieldSpec[]
  busy: boolean
  onRemap: (slotId: string, fieldKey: string, ordinal?: number) => void
  onType: (slotId: string, value: string) => void
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
                entries={plan.entries}
                busy={busy}
                onRemap={onRemap}
                onType={onType}
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
  entries,
  busy,
  onRemap,
  onType,
}: {
  item: PlanItem
  fields: FieldSpec[]
  entries: Record<string, number>
  busy: boolean
  onRemap: (slotId: string, fieldKey: string, ordinal?: number) => void
  onType: (slotId: string, value: string) => void
}) {
  const skipped = item.status === 'skip'
  // 模型判斷的值得使用者優先看一眼；規則與快取都是確定性來源
  const needsReview = !skipped && item.source === 'model'
  // 學歷、經歷這種清單欄位要能指定第幾筆：表格第 3 列對的是第 2 所學校時，
  // 光換欄位沒用。選單列到我的資料實際有的筆數（至少 1 筆，也涵蓋目前選的那一筆）
  const root = item.field_key.includes('[]') ? item.field_key.split('[]')[0] : ''
  const count = root ? Math.max(entries[root] ?? 0, item.ordinal + 1, 1) : 0

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
        {root && (
          <select
            value={item.ordinal}
            disabled={busy}
            aria-label="第幾筆"
            onChange={(e) => onRemap(item.slot_id, item.field_key, Number(e.target.value))}
            className="ml-2 text-sm border border-slate-300 rounded px-2 py-1
                       focus:outline-none focus:ring-2 focus:ring-sky-500"
          >
            {Array.from({ length: count }, (_, i) => (
              <option key={i} value={i}>
                第 {i + 1} 筆
              </option>
            ))}
          </select>
        )}
      </td>

      <td className="px-4 py-2.5">
        {/* 勾選題的 existing 是選項清單（「□男 □女」）、印字位置的是表格印好的
            提示（「自　年　月」），值是插進去的，兩種都不會抹掉原本的字 */}
        {!INSERTS.has(item.kind) && item.existing.trim() ? (
          <span>
            <span className="line-through text-slate-400">{item.existing.slice(0, 24)}</span>
            {!skipped && <OverwriteBadge>將被覆蓋</OverwriteBadge>}
          </span>
        ) : (
          <span className="text-slate-300">—</span>
        )}
      </td>

      <td className="px-4 py-2.5">
        <ValueCell item={item} busy={busy} onType={onType} />
        {skipped && !item.value && <SkipReason note={item.note} />}
      </td>

      <td className="px-4 py-2.5 text-xs text-slate-500">
        {skipped ? '—' : sourceLabel(item.source)}
      </td>
    </tr>
  )
}

/**
 * 要填的值：直接在這裡打字就改這一格（後端存在這份工作底下，不會動到「我的資料」）。
 * 清空就改回自動判斷的值。
 */
function ValueCell({
  item,
  busy,
  onType,
}: {
  item: PlanItem
  busy: boolean
  onType: (slotId: string, value: string) => void
}) {
  const [text, setText] = useState(item.value)
  // 後端回新的計畫（改了欄位、改了這次應徵）就以它為準
  useEffect(() => setText(item.value), [item.slot_id, item.value])
  const typed = item.source === 'typed'

  return (
    <div className="flex items-center gap-1">
      {/* 同一份表格常有好幾格印著一樣的欄名，aria-label 帶上位置代碼才分得出是哪一格 */}
      <input
        value={text}
        disabled={busy}
        aria-label={`要填的值：${item.label || '這一格'}（${item.slot_id}）`}
        placeholder="（不填）"
        onChange={(e) => setText(e.target.value)}
        onBlur={() => text !== item.value && onType(item.slot_id, text)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          if (e.key === 'Escape') setText(item.value)
        }}
        className={`w-40 text-sm rounded px-2 py-1 border focus:outline-none focus:ring-2
                    focus:ring-sky-500 ${
                      typed
                        ? 'border-sky-300 bg-sky-50 text-slate-900'
                        : 'border-transparent hover:border-slate-300 text-slate-900'
                    }`}
      />
      {typed && (
        <button
          onClick={() => onType(item.slot_id, '')}
          disabled={busy}
          title="改回自動判斷的值"
          className="text-xs text-slate-400 hover:text-slate-700 px-1"
        >
          ↺
        </button>
      )}
    </div>
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
    cache: '快取',
    model: '模型',
    manual: '手動',
    apply: '這次應徵',
    typed: '手動填寫',
  }
  return names[source] ?? source
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

