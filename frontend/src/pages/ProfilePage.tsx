import { useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api, type FieldSpec, type Profile } from '../api'
import Field from '../components/Field'
import RepeatList from '../components/RepeatList'

// 分區順序就是畫面順序。key 前綴對應 backend/core/schema.py 的欄位命名。
const SECTIONS = [
  { prefix: 'basic.', title: '基本資料' },
  { prefix: 'contact.', title: '聯絡方式' },
  { prefix: 'job.', title: '應徵資訊' },
  { prefix: 'skills.', title: '專長技能' },
  { prefix: 'emergency.', title: '緊急聯絡人' },
]

const REPEATS = [
  { prefix: 'education[].', title: '學歷', root: 'education' },
  { prefix: 'experience[].', title: '工作經歷', root: 'experience' },
]

const LONGTEXTS = [
  { key: 'autobiography', title: '自傳' },
  { key: 'motivation', title: '應徵動機' },
]

export default function ProfilePage() {
  const [fields, setFields] = useState<FieldSpec[]>([])
  const [profile, setProfile] = useState<Profile>({})
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const location = useLocation()

  // 剛從匯入頁跳過來時，把被改動的欄位高亮一下，讓使用者確認結果
  const highlighted: string[] = location.state?.changed ?? []

  useEffect(() => {
    Promise.all([api.fields(), api.getProfile()])
      .then(([f, p]) => {
        setFields(f)
        setProfile(p)
      })
      .catch((e) => setError(e.message))
  }, [location.key])

  function edit(mutate: (draft: Profile) => void) {
    setProfile((prev) => {
      const draft = structuredClone(prev)
      mutate(draft)
      return draft
    })
    setDirty(true)
  }

  async function save() {
    setSaving(true)
    setError('')
    try {
      await api.saveProfile(profile)
      setDirty(false)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const filled = useMemo(() => countFilled(fields, profile), [fields, profile])

  if (error && fields.length === 0) {
    return <ErrorBox message={error} />
  }

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">我的資料</h1>
          <p className="text-sm text-slate-500 mt-1">
            填一次就好，之後所有履歷都從這裡取值。已填 {filled.done} / {filled.total} 個欄位
          </p>
        </div>
        <SaveButton dirty={dirty} saving={saving} onClick={save} />
      </div>

      {error && <ErrorBox message={error} />}

      {SECTIONS.map((section) => {
        const specs = fields.filter((f) => f.key.startsWith(section.prefix))
        if (specs.length === 0) return null
        return (
          <section key={section.prefix} className="bg-white rounded-lg border border-slate-200 p-6">
            <h2 className="font-semibold text-slate-900 mb-4">{section.title}</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {specs.map((spec) => (
                <div
                  key={spec.key}
                  className={highlighted.includes(spec.key) ? 'ring-2 ring-sky-400 rounded-md' : ''}
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
        )
      })}

      {REPEATS.map((rep) => (
        <RepeatList
          key={rep.root}
          title={rep.title}
          specs={fields.filter((f) => f.key.startsWith(rep.prefix))}
          items={profile[rep.root] ?? []}
          onChange={(items) => edit((d) => (d[rep.root] = items))}
        />
      ))}

      {LONGTEXTS.map((lt) => {
        const spec = fields.find((f) => f.key === lt.key)
        if (!spec) return null
        return (
          <section key={lt.key} className="bg-white rounded-lg border border-slate-200 p-6">
            <h2 className="font-semibold text-slate-900 mb-4">{lt.title}</h2>
            <Field
              spec={spec}
              value={readPath(profile, spec.key)}
              onChange={(v) => edit((d) => writePath(d, spec.key, v))}
            />
          </section>
        )
      })}

      <div className="flex justify-end pb-8">
        <SaveButton dirty={dirty} saving={saving} onClick={save} />
      </div>
    </div>
  )
}

function SaveButton({
  dirty,
  saving,
  onClick,
}: {
  dirty: boolean
  saving: boolean
  onClick: () => void
}) {
  return (
    <div className="flex items-center gap-3">
      {dirty && <span className="text-sm text-amber-600">有未儲存的變更</span>}
      <button
        onClick={onClick}
        disabled={!dirty || saving}
        className="px-5 py-2 rounded-md bg-sky-600 text-white text-sm font-medium
                   hover:bg-sky-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
      >
        {saving ? '儲存中…' : '儲存'}
      </button>
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

function countFilled(fields: FieldSpec[], profile: Profile) {
  const scalars = fields.filter((f) => !f.key.includes('[]'))
  const done = scalars.filter((f) => readPath(profile, f.key).trim() !== '').length
  return { done, total: scalars.length }
}
