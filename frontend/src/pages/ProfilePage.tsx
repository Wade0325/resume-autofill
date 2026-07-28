import { useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api, type FieldSpec, type Profile } from '../api'
import { SECTIONS, type Section } from '../sections'
import Field from '../components/Field'
import RepeatList from '../components/RepeatList'

export default function ProfilePage() {
  const [fields, setFields] = useState<FieldSpec[]>([])
  const [profile, setProfile] = useState<Profile>({})
  const [active, setActive] = useState('basic')
  const [dirty, setDirty] = useState(false)
  const [pending, setPending] = useState<string | null>(null) // 想切去、但被未儲存變更擋住的主題
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const location = useLocation()

  // 剛從匯入頁跳過來時要高亮被改動的欄位。格式是「欄位代碼#序號」，
  // 學歷／經歷才分得出是第幾筆。這只是提示「剛剛動到這些」，
  // 所以是短暫閃示——不清掉的話藍框會一直卡在畫面上。
  const [highlighted, setHighlighted] = useState<Set<string>>(new Set())

  useEffect(() => {
    const changed: string[] = location.state?.changed ?? []
    if (changed.length === 0) return
    setHighlighted(new Set(changed))
    const timer = setTimeout(() => setHighlighted(new Set()), 5000)
    return () => clearTimeout(timer)
  }, [location.key])

  useEffect(() => {
    Promise.all([api.fields(), api.getProfile()])
      .then(([f, p]) => {
        setFields(f)
        setProfile(p)
      })
      .catch((e) => setError(e.message))
  }, [location.key])

  // 一次只顯示一個主題，被高亮的欄位若不在當前主題就看不見了，
  // 所以匯入完直接跳到第一個有變更的主題。
  useEffect(() => {
    if (highlighted.size === 0) return
    const firstKey = [...highlighted][0].split('#')[0]
    const target = SECTIONS.find((s) => firstKey.startsWith(s.prefix))
    if (target) setActive(target.id)
  }, [highlighted])

  function edit(mutate: (draft: Profile) => void) {
    setProfile((prev) => {
      const draft = structuredClone(prev)
      mutate(draft)
      return draft
    })
    setDirty(true)
    // 開始編輯就代表使用者看到了，提示可以收掉
    if (highlighted.size > 0) setHighlighted(new Set())
  }

  async function save() {
    setSaving(true)
    setError('')
    try {
      await api.saveProfile(profile)
      setDirty(false)
      return true
    } catch (e: any) {
      setError(e.message)
      return false
    } finally {
      setSaving(false)
    }
  }

  function requestSwitch(id: string) {
    if (id === active) return
    if (dirty) {
      setPending(id)
      return
    }
    setActive(id)
  }

  async function saveThenSwitch() {
    if (await save()) {
      setActive(pending!)
      setPending(null)
    }
  }

  async function discardThenSwitch() {
    // 從伺服器重讀，否則切回來時還會看到已被放棄的編輯內容
    const fresh = await api.getProfile().catch(() => profile)
    setProfile(fresh)
    setDirty(false)
    setActive(pending!)
    setPending(null)
  }

  const section = SECTIONS.find((s) => s.id === active)!
  const total = useMemo(() => countFilled(fields, profile), [fields, profile])

  if (error && fields.length === 0) return <ErrorBox message={error} />

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">我的資料</h1>
          <p className="text-sm text-slate-500 mt-1">
            填一次就好，之後所有履歷都從這裡取值。已填 {total.done} / {total.count} 個欄位
          </p>
        </div>
        <div className="flex items-center gap-3">
          {dirty && <span className="text-sm text-amber-600">有未儲存的變更</span>}
          <button
            onClick={save}
            disabled={!dirty || saving}
            className="px-5 py-2 rounded-md bg-sky-600 text-white text-sm font-medium
                       hover:bg-sky-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            {saving ? '儲存中…' : '儲存'}
          </button>
        </div>
      </div>

      {error && <ErrorBox message={error} />}

      <div className="grid grid-cols-[13rem_1fr] gap-8 items-start">
        <nav className="sticky top-6 space-y-1">
          {SECTIONS.map((s) => (
            <SectionButton
              key={s.id}
              section={s}
              activeId={active}
              dirty={dirty}
              hasChanges={[...highlighted].some((h) => h.startsWith(s.prefix))}
              progress={countFilled(fields, profile, s)}
              onClick={() => requestSwitch(s.id)}
            />
          ))}
        </nav>

        <div className="pb-8">
          {section.repeatRoot ? (
            <RepeatList
              title={section.title}
              specs={fields.filter((f) => f.key.startsWith(section.prefix))}
              items={profile[section.repeatRoot] ?? []}
              onChange={(items) => edit((d) => (d[section.repeatRoot!] = items))}
              isHighlighted={(specKey, index) => highlighted.has(`${specKey}#${index}`)}
            />
          ) : (
            <section className="bg-white rounded-lg border border-slate-200 p-6">
              <h2 className="font-semibold text-slate-900 mb-4">{section.title}</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {fields
                  .filter((f) => f.key.startsWith(section.prefix))
                  .map((spec) => (
                    <div
                      key={spec.key}
                      className={
                        highlighted.has(`${spec.key}#0`) ? 'ring-2 ring-sky-400 rounded-md' : ''
                      }
                    >
                      <Field
                        spec={spec}
                        value={readPath(profile, spec.key)}
                        onChange={(v) => edit((d) => writePath(d, spec.key, v))}
                      />
                    </div>
                  ))}
              </div>
            </section>
          )}
        </div>
      </div>

      {pending && (
        <UnsavedDialog
          from={section.title}
          to={SECTIONS.find((s) => s.id === pending)!.title}
          saving={saving}
          onSave={saveThenSwitch}
          onDiscard={discardThenSwitch}
          onCancel={() => setPending(null)}
        />
      )}
    </div>
  )
}

function SectionButton({
  section,
  activeId,
  dirty,
  hasChanges,
  progress,
  onClick,
}: {
  section: Section
  activeId: string
  dirty: boolean
  hasChanges: boolean
  progress: { done: number; count: number }
  onClick: () => void
}) {
  const isActive = section.id === activeId
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 py-2.5 rounded-md text-sm transition flex items-center gap-2 ${
        isActive
          ? 'bg-sky-50 text-sky-800 font-medium border border-sky-200'
          : 'text-slate-600 hover:bg-slate-100 border border-transparent'
      }`}
    >
      <span className="flex-1">{section.title}</span>
      {hasChanges && (
        <span className="w-1.5 h-1.5 rounded-full bg-sky-500" title="匯入時有變更" />
      )}
      {isActive && dirty && <span className="text-xs text-amber-600">未存</span>}
      <span className="text-xs text-slate-400">
        {section.repeatRoot ? `${progress.count} 筆` : `${progress.done}/${progress.count}`}
      </span>
    </button>
  )
}

function UnsavedDialog({
  from,
  to,
  saving,
  onSave,
  onDiscard,
  onCancel,
}: {
  from: string
  to: string
  saving: boolean
  onSave: () => void
  onDiscard: () => void
  onCancel: () => void
}) {
  return (
    <div className="fixed inset-0 bg-slate-900/40 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6">
        <h3 className="font-semibold text-slate-900">「{from}」有未儲存的變更</h3>
        <p className="text-sm text-slate-600 mt-2">
          要先儲存再切換到「{to}」嗎？選擇放棄的話這些變更會消失。
        </p>
        <div className="flex justify-end gap-2 mt-6">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm rounded-md border border-slate-300 text-slate-600 hover:bg-slate-50"
          >
            留在這頁
          </button>
          <button
            onClick={onDiscard}
            className="px-4 py-2 text-sm rounded-md border border-rose-200 text-rose-600 hover:bg-rose-50"
          >
            放棄變更
          </button>
          <button
            onClick={onSave}
            disabled={saving}
            className="px-4 py-2 text-sm rounded-md bg-sky-600 text-white font-medium
                       hover:bg-sky-700 disabled:bg-slate-300"
          >
            {saving ? '儲存中…' : '儲存並切換'}
          </button>
        </div>
      </div>
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

// ---- profile 是巢狀物件，這兩個小工具負責用 "basic.name_zh" 這種路徑存取 ----
function readPath(obj: Profile, path: string): string {
  let cur: any = obj
  for (const part of path.split('.')) {
    if (cur == null || typeof cur !== 'object') return ''
    cur = cur[part]
  }
  return cur == null ? '' : String(cur)
}

function writePath(obj: Profile, path: string, value: string) {
  const parts = path.split('.')
  let cur: any = obj
  for (const part of parts.slice(0, -1)) {
    if (typeof cur[part] !== 'object' || cur[part] === null) cur[part] = {}
    cur = cur[part]
  }
  cur[parts[parts.length - 1]] = value
}

/** 傳 section 就只算該主題，不傳則算全部。多筆資料的區塊回傳筆數（顯示成「N 筆」）。 */
function countFilled(fields: FieldSpec[], profile: Profile, section?: Section) {
  if (section?.repeatRoot) {
    const rows: Record<string, string>[] = profile[section.repeatRoot] ?? []
    return { done: rows.length, count: rows.length }
  }
  const specs = fields.filter(
    (f) => !f.key.includes('[]') && (!section || f.key.startsWith(section.prefix)),
  )
  return {
    done: specs.filter((f) => readPath(profile, f.key).trim() !== '').length,
    count: specs.length,
  }
}
