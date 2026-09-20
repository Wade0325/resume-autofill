import { useEffect, useState } from 'react'
import type { FieldSpec, Plan, Profile } from '../api'
import Field from './Field'

/**
 * 「這次應徵」：應徵職務、工作地點這種每間公司都不一樣的欄位。
 * 填在這裡只算這一份工作，不會寫進「我的資料」；沒填的就沿用「我的資料」的值。
 */
export default function ApplyPanel({
  fields,
  plan,
  profile,
  busy,
  onApply,
}: {
  fields: FieldSpec[]
  plan: Plan
  profile: Profile
  busy: boolean
  onApply: (values: Record<string, string>) => void
}) {
  const specs = fields.filter((f) => f.key.startsWith('job.'))
  // 後端沒給就當空的：欄位少一個不該讓整頁掛掉
  const saved = plan.apply ?? {}
  const [draft, setDraft] = useState<Record<string, string>>(saved)

  // 換一份工作（或剛套用完）就以後端那份為準
  useEffect(() => setDraft(plan.apply ?? {}), [plan.job_id, plan.apply])

  if (specs.length === 0) return null
  const dirty = specs.some((s) => (draft[s.key] ?? '') !== (saved[s.key] ?? ''))
  // 這份表格真的問了這些欄位就先展開，免得使用者沒注意到有地方可以填
  const asked = (plan.items ?? []).filter((i) => i.note?.startsWith('這次應徵'))
  const missing = asked.filter((i) => i.status === 'skip').length

  return (
    <details open={asked.length > 0} className="bg-white border border-slate-200 rounded-lg">
      <summary className="cursor-pointer select-none px-4 py-3 text-sm text-slate-700">
        這次應徵
        <span className="ml-2 text-xs text-slate-500">
          每間公司都不一樣的欄位，填在這裡只算這一份，不會寫進「我的資料」
        </span>
        {missing > 0 && (
          <span className="ml-2 text-xs bg-amber-100 text-amber-800 rounded px-1.5 py-0.5">
            這份表格有 {missing} 格還沒填
          </span>
        )}
      </summary>
      <div className="px-4 pb-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {specs.map((spec) => {
            const inherited = spec.per_job ? '' : readPath(profile, spec.key)
            return (
              <div key={spec.key}>
                <Field
                  spec={spec}
                  value={draft[spec.key] ?? ''}
                  onChange={(v) => setDraft((d) => ({ ...d, [spec.key]: v }))}
                />
                {inherited && !(draft[spec.key] ?? '') && (
                  <p className="mt-1 text-xs text-slate-400">沿用我的資料：{inherited}</p>
                )}
              </div>
            )
          })}
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={() => onApply(draft)}
            disabled={!dirty || busy}
            className="px-4 py-2 rounded-md bg-sky-600 text-white text-sm font-medium
                       hover:bg-sky-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            {busy ? '套用中…' : '套用'}
          </button>
          <span className="text-xs text-slate-500">套用後預覽立刻更新，不必重新分析</span>
        </div>
      </div>
    </details>
  )
}

function readPath(obj: Profile, path: string): string {
  let cur: any = obj
  for (const part of path.split('.')) {
    if (cur == null || typeof cur !== 'object') return ''
    cur = cur[part]
  }
  return cur == null ? '' : String(cur)
}
