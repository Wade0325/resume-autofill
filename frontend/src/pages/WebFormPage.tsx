import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  errorText,
  type WebFormField,
  type WebFormItem,
  type WebFormPick,
  type WebFormResult,
  type WebFormState,
} from '../api'
import { PageShell } from '../components/common'

/**
 * 網頁填寫：把我的資料補進求職平台的個人檔案（目前只有 Cake）。
 * 跟「填寫履歷」是兩個功能：這裡不碰 docx，也不用模型。
 *
 * 流程：開瀏覽器 → 使用者自己在那個視窗登入 → 讀出平台上還沒有的幾筆 → 使用者確認 → 一筆一筆存。
 */

const SITE = 'cake'
const BUSY = new Set(['login', 'reading', 'running'])

const INPUT_CLASS =
  'w-full rounded-md border border-slate-300 px-3 py-2 text-sm ' +
  'focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-sky-500 ' +
  'disabled:bg-slate-100 disabled:text-slate-400'

const YM = /(\d{4})\s*[年/\-.]\s*(\d{1,2})/
const Y = /(\d{4})/

/** 還缺哪幾欄。跟後端的 cake.problems 同一套規則，後端送出前還會再驗一次。 */
export function missingOf(fields: WebFormField[]): string[] {
  const out: string[] = []
  for (const f of fields) {
    const v = (f.value || '').trim()
    if (f.alt && f.alt_on) continue
    if (f.kind === 'ym' || f.kind === 'y') {
      if (!v && !f.required) continue
      const m = v.match(YM)
      const hasYear = !!(m || v.match(Y))
      const month = m ? Number(m[2]) : 0
      if (!hasYear || (f.kind === 'ym' && !(month >= 1 && month <= 12))) out.push(f.label)
    } else if (f.kind === 'select') {
      if ((f.required || v) && !(f.options ?? []).includes(v)) out.push(f.label)
    } else if (f.required && !v) {
      out.push(f.label)
    }
  }
  return out
}

export default function WebFormPage() {
  const [state, setState] = useState<WebFormState | null>(null)
  const [error, setError] = useState('')
  const [fields, setFields] = useState<Record<string, WebFormField[]>>({})
  const [picked, setPicked] = useState<Record<string, boolean>>({})
  const lastStage = useRef('')

  const apply = useCallback((s: WebFormState) => {
    setState(s)
    // 一份新的清單（剛讀完，或存完重新讀）：改過的值與勾選從頭來過
    if (s.stage === 'ready' && lastStage.current !== 'ready') {
      setFields(Object.fromEntries(s.items.map((i) => [i.id, i.fields.map((f) => ({ ...f }))])))
      setPicked(Object.fromEntries(s.items.map((i) => [i.id, !i.exists && i.missing.length === 0])))
    }
    lastStage.current = s.stage
  }, [])

  useEffect(() => {
    api.webform(SITE).then(apply).catch((e) => setError(errorText(e)))
  }, [apply])

  // 等登入、讀取、存的時候每秒問一次進度
  const busy = state ? BUSY.has(state.stage) : false
  useEffect(() => {
    if (!busy) return
    const timer = window.setInterval(() => {
      api.webform(SITE).then(apply).catch(() => {})
    }, 1000)
    return () => window.clearInterval(timer)
  }, [busy, apply])

  const act = async (fn: () => Promise<WebFormState>) => {
    setError('')
    try {
      apply(await fn())
    } catch (e) {
      setError(errorText(e))
    }
  }

  const label = state?.site.label ?? 'Cake'
  const items = state?.items ?? []
  const fieldsOf = (item: WebFormItem) => fields[item.id] ?? item.fields
  const fresh = items.filter((i) => !i.exists)
  const chosen = fresh.filter((i) => picked[i.id] && missingOf(fieldsOf(i)).length === 0)

  const setField = (item: WebFormItem, key: string, patch: Partial<WebFormField>) => {
    const before = fieldsOf(item)
    const after = before.map((f) => (f.key === key ? { ...f, ...patch } : f))
    setFields((prev) => ({ ...prev, [item.id]: after }))
    // 補齊最後一個缺的欄位時順手勾起來，不用再多按一下
    if (missingOf(before).length > 0 && missingOf(after).length === 0) {
      setPicked((prev) => ({ ...prev, [item.id]: true }))
    }
  }

  const run = () => {
    if (!window.confirm(`會在 ${label} 上新增 ${chosen.length} 筆資料，確定嗎？`)) return
    const picks: WebFormPick[] = chosen.map((i) => {
      const fs = fieldsOf(i)
      return {
        id: i.id,
        values: Object.fromEntries(fs.map((f) => [f.key, f.value])),
        alts: Object.fromEntries(fs.filter((f) => f.alt).map((f) => [f.key, !!f.alt_on])),
      }
    })
    act(() => api.webformRun(SITE, picks))
  }

  const closeButton = (
    <button
      onClick={() => act(() => api.webformClose(SITE))}
      className="text-sm text-slate-500 hover:text-slate-800"
    >
      關閉瀏覽器
    </button>
  )

  return (
    <PageShell
      title="網頁填寫"
      desc="把我的資料補進求職平台的個人檔案。目前支援 Cake。"
      error={error}
    >
      {state === null ? null : state.stage === 'closed' ? (
        <>
          <section className="bg-white rounded-lg border border-slate-200 p-6 space-y-4">
            <div>
              <h2 className="font-semibold text-slate-900">{label} 個人檔案</h2>
              <p className="text-sm text-slate-600 mt-1">
                把 {label} 上還沒有的工作經驗、學歷與證照補上去。已經在上面的不會重複新增，也不會被改動。
              </p>
            </div>
            <ul className="text-sm text-slate-500 list-disc pl-5 space-y-1">
              <li>會開一個獨立的瀏覽器視窗，你自己在裡面登入，密碼不會經過這個程式</li>
              <li>登入一次之後會記住，下次不必再登入</li>
              <li>存之前會先列出清單給你確認，只送出清單上的內容</li>
            </ul>
            {state.message && <p className="text-sm text-amber-700">{state.message}</p>}
            <button
              onClick={() => act(() => api.webformOpen(SITE))}
              className="px-5 py-2.5 rounded-md bg-sky-600 text-white font-medium hover:bg-sky-700"
            >
              開啟瀏覽器
            </button>
          </section>
          <Results results={state.results} label={label} />
        </>
      ) : state.stage === 'login' || state.stage === 'reading' ? (
        <section className="bg-white rounded-lg border border-slate-200 p-6 flex items-center gap-3">
          <span className="h-4 w-4 rounded-full border-2 border-sky-600 border-t-transparent animate-spin" />
          <span className="text-sm text-slate-700 flex-1">{state.message}</span>
          {closeButton}
        </section>
      ) : state.stage === 'running' ? (
        <>
          <Progress state={state} />
          <Results results={state.results} label={label} />
        </>
      ) : (
        <>
          <Results results={state.results} label={label} />
          {state.message && (
            <p className="text-sm text-amber-700 bg-amber-50 rounded-md px-4 py-3">{state.message}</p>
          )}
          {state.notes.map((n) => (
            <p key={n} className="text-sm text-slate-600">
              {n}
            </p>
          ))}
          {!state.message && fresh.length === 0 && (
            <p className="bg-white rounded-lg border border-slate-200 p-6 text-sm text-slate-600">
              {label} 上已經有你所有的工作經驗、學歷與證照，沒有要補的。
            </p>
          )}
          {fresh.length > 0 &&
            sectionsOf(items).map(([section, list]) => (
              <section key={section} className="space-y-3">
                <h2 className="font-semibold text-slate-900">{section}</h2>
                {list.map((item) => (
                  <ItemCard
                    key={item.id}
                    item={item}
                    label={label}
                    fields={fieldsOf(item)}
                    picked={!!picked[item.id]}
                    onPick={(on) => setPicked((prev) => ({ ...prev, [item.id]: on }))}
                    onField={(key, patch) => setField(item, key, patch)}
                  />
                ))}
              </section>
            ))}
          <div className="flex items-center justify-between gap-3 pb-8">
            {closeButton}
            <div className="flex items-center gap-3">
              <span className="text-xs text-slate-400">只會送出上面列出的欄位</span>
              <button
                onClick={() => act(() => api.webformRefresh(SITE))}
                className="px-4 py-2.5 rounded-md border border-slate-300 text-sm text-slate-700 hover:bg-slate-50"
              >
                重新讀取
              </button>
              <button
                onClick={run}
                disabled={chosen.length === 0 || !state.browser_open}
                className="px-6 py-2.5 rounded-md bg-sky-600 text-white font-medium
                           hover:bg-sky-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
              >
                存進 {label}（{chosen.length} 筆）
              </button>
            </div>
          </div>
          {!state.browser_open && (
            <p className="text-sm text-amber-700">
              瀏覽器視窗已經關掉了，按「重新讀取」會再開一次。
            </p>
          )}
        </>
      )}
    </PageShell>
  )
}

/** 依區塊分組，保持後端給的順序。 */
function sectionsOf(items: WebFormItem[]): [string, WebFormItem[]][] {
  const out = new Map<string, WebFormItem[]>()
  for (const item of items) out.set(item.section, [...(out.get(item.section) ?? []), item])
  return [...out.entries()]
}

function ItemCard({
  item,
  label,
  fields,
  picked,
  onPick,
  onField,
}: {
  item: WebFormItem
  label: string
  fields: WebFormField[]
  picked: boolean
  onPick: (on: boolean) => void
  onField: (key: string, patch: Partial<WebFormField>) => void
}) {
  if (item.exists) {
    return (
      <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-500">
        {item.title}
        <span className="ml-2 text-xs">已經在 {label} 上，略過</span>
      </div>
    )
  }
  const missing = missingOf(fields)
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4" data-item={item.id}>
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          aria-label={`新增 ${item.title}`}
          disabled={missing.length > 0}
          checked={picked && missing.length === 0}
          onChange={(e) => onPick(e.target.checked)}
        />
        <span className="font-medium text-slate-900">{item.title}</span>
        {missing.length > 0 && (
          <span className="text-xs bg-amber-100 text-amber-800 rounded px-1.5 py-0.5">
            還缺：{missing.join('、')}
          </span>
        )}
      </label>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
        {fields.map((f) => (
          <FieldInput
            key={f.key}
            field={f}
            bad={missing.includes(f.label)}
            onChange={(patch) => onField(f.key, patch)}
          />
        ))}
      </div>
    </div>
  )
}

function FieldInput({
  field,
  bad,
  onChange,
}: {
  field: WebFormField
  bad: boolean
  onChange: (patch: Partial<WebFormField>) => void
}) {
  const off = !!(field.alt && field.alt_on)
  const cls = `${INPUT_CLASS} ${bad ? 'border-amber-400 bg-amber-50' : ''}`
  let input
  if (field.kind === 'select') {
    input = (
      <select
        aria-label={field.label}
        className={cls}
        value={field.value}
        onChange={(e) => onChange({ value: e.target.value })}
      >
        <option value="">請選擇</option>
        {(field.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    )
  } else if (field.kind === 'textarea') {
    input = (
      <textarea
        aria-label={field.label}
        className={cls}
        rows={3}
        value={field.value}
        onChange={(e) => onChange({ value: e.target.value })}
      />
    )
  } else {
    input = (
      <input
        aria-label={field.label}
        className={cls}
        value={off ? '' : field.value}
        disabled={off}
        placeholder={field.kind === 'ym' ? '例如 2020/05' : field.kind === 'y' ? '例如 2020' : ''}
        onChange={(e) => onChange({ value: e.target.value })}
      />
    )
  }
  return (
    <div className={field.kind === 'textarea' ? 'md:col-span-2' : ''}>
      <span className="text-sm text-slate-700">
        {field.label}
        {field.required && <span className="text-rose-500"> *</span>}
      </span>
      <div className="mt-1 flex items-center gap-3">
        {input}
        {field.alt && (
          <label className="flex items-center gap-1 text-sm text-slate-700 whitespace-nowrap">
            <input
              type="checkbox"
              checked={!!field.alt_on}
              onChange={(e) => onChange({ alt_on: e.target.checked })}
            />
            {field.alt}
          </label>
        )}
      </div>
    </div>
  )
}

function Progress({ state }: { state: WebFormState }) {
  const { done, total, current } = state.progress
  const pct = total ? Math.round((done / total) * 100) : 0
  return (
    <section className="bg-white rounded-lg border border-slate-200 p-6 space-y-3">
      <div className="flex justify-between text-sm text-slate-700">
        <span>
          正在存進 {state.site.label}：第 {Math.min(done + 1, total)}／{total} 筆
        </span>
        <span className="text-slate-500">{current}</span>
      </div>
      <div className="h-2 rounded bg-slate-100 overflow-hidden">
        <div className="h-2 bg-sky-600 transition-all" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-xs text-slate-400">可以看著瀏覽器視窗裡一筆一筆填進去，過程中請不要操作那個視窗。</p>
    </section>
  )
}

function Results({ results, label }: { results: WebFormResult[]; label: string }) {
  if (results.length === 0) return null
  const ok = results.filter((r) => r.ok)
  const bad = results.filter((r) => !r.ok)
  return (
    <section
      className={`rounded-lg border px-4 py-3 text-sm space-y-1 ${
        bad.length ? 'border-amber-200 bg-amber-50' : 'border-emerald-200 bg-emerald-50'
      }`}
    >
      <p className="font-medium text-slate-800">
        存進 {label} {ok.length} 筆{bad.length > 0 && `，${bad.length} 筆沒存成`}
      </p>
      {bad.map((r) => (
        <p key={r.id} className="text-amber-800">
          {r.section}「{r.title}」：{r.why}
        </p>
      ))}
    </section>
  )
}
