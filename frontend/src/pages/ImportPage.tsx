import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type FieldSpec, type ImportPreview, type ImportRow } from '../api'
import { SECTIONS } from '../sections'
import Dropzone, { type UploadPhase } from '../components/Dropzone'
import { Header, ErrorBox } from '../components/common'

// pdf.js 佔了主 bundle 一半以上，等真的要顯示履歷對照時再載
const ResumeDocView = lazy(() => import('../components/ResumeDocView'))

// 切到別頁再切回來時要能接續。只存 id 與勾選狀態，列表本身回頭跟後端重拿，
// 免得 sessionStorage 裡放一份會過期的副本。
const KEY_ID = 'import.id'
const KEY_PICKED = 'import.picked'

export default function ImportPage() {
  const [fields, setFields] = useState<FieldSpec[]>([])
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [active, setActive] = useState<string>(SECTIONS[0].id)
  const [phase, setPhase] = useState<UploadPhase>({ kind: 'idle' })
  const [hovered, setHovered] = useState<string | null>(null) // 滑到的列，讓履歷上的框連動
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    api.fields().then(setFields).catch((e) => setError(e.message))
  }, [])

  // 讀取在後端背景執行，這裡輪詢進度；切頁再回來會憑 sessionStorage 接上
  const [trackingId, setTrackingId] = useState<string | null>(() =>
    sessionStorage.getItem(KEY_ID),
  )
  useEffect(() => {
    if (!trackingId || preview) return
    let stopped = false
    let fails = 0
    const started = Date.now()
    const giveUp = () => {
      clearSaved()
      setTrackingId(null)
      setPhase({ kind: 'idle' })
    }
    const poll = () => {
      api
        .getImport(trackingId)
        .then((st) => {
          if (stopped) return
          fails = 0
          if (st.status === 'ready') {
            setPreview(st.preview)
            setPhase({ kind: 'idle' })
            // 切頁回來沿用之前的勾選；剛完成的新匯入用預設勾選
            const saved = sessionStorage.getItem(KEY_PICKED)
            if (saved) {
              setPicked(new Set(JSON.parse(saved)))
            } else {
              remember(
                new Set(
                  st.preview.rows.filter((r) => r.default_checked).map((r) => r.row_id),
                ),
              )
            }
            const first = SECTIONS.find((s) =>
              st.preview.rows.some((r) => r.field_key.startsWith(s.prefix)),
            )
            if (first) setActive(first.id)
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
  }, [trackingId, preview])

  function clearSaved() {
    sessionStorage.removeItem(KEY_ID)
    sessionStorage.removeItem(KEY_PICKED)
  }

  function remember(next: Set<string>) {
    setPicked(next)
    sessionStorage.setItem(KEY_PICKED, JSON.stringify([...next]))
  }

  async function upload(file: File) {
    setError('')
    setPhase({ kind: 'uploading', percent: 0 })
    try {
      const accepted = await api.analyzeImport(file, (percent) =>
        setPhase({ kind: 'uploading', percent }),
      )
      sessionStorage.setItem(KEY_ID, accepted.import_id)
      sessionStorage.removeItem(KEY_PICKED) // 新的一次匯入，舊勾選不適用
      setPhase({ kind: 'analyzing', startedAt: Date.now(), stage: '準備中' })
      setTrackingId(accepted.import_id)
    } catch (e: any) {
      setError(e.requestId ? `${e.message}（追蹤碼 ${e.requestId}）` : e.message)
      setPhase({ kind: 'idle' })
    }
  }

  async function apply() {
    if (!preview) return
    setApplying(true)
    setError('')
    try {
      await api.applyImport(preview.import_id, [...picked])
      const changed = preview.rows
        .filter((r) => picked.has(r.row_id))
        .map((r) => `${r.field_key}#${r.ordinal}`)
      clearSaved()
      navigate('/profile', { state: { changed } })
    } catch (e: any) {
      setError(e.message)
      setApplying(false)
    }
  }

  function toggle(rowId: string) {
    const next = new Set(picked)
    if (next.has(rowId)) next.delete(rowId)
    else next.add(rowId)
    remember(next)
  }

  // 表頭勾選框：只作用在畫面上這個主題的列，其他主題的勾選不受影響
  function toggleAll(rows: ImportRow[], select: boolean) {
    const next = new Set(picked)
    rows.forEach((r) => (select ? next.add(r.row_id) : next.delete(r.row_id)))
    remember(next)
  }

  if (!preview) {
    return (
      <div className="space-y-6">
        <Header title="匯入履歷" desc="上傳一份已經填好的履歷，系統會把裡面的資料抽出來存進「我的資料」。" />
        {error && <ErrorBox message={error} />}
        <Dropzone
          title="把已填寫的履歷拖到這裡"
          hint="或點擊選擇檔案"
          phase={phase}
          onFile={upload}
        />
      </div>
    )
  }

  const labelOf = (key: string) => fields.find((f) => f.key === key)?.label ?? key
  const rowsOf = (prefix: string) => preview.rows.filter((r) => r.field_key.startsWith(prefix))
  const shown = rowsOf(SECTIONS.find((s) => s.id === active)!.prefix)

  return (
    <div className="space-y-6">
      <Header title="匯入履歷" desc={preview.filename} />
      {error && <ErrorBox message={error} />}

      <div className="flex flex-wrap items-center gap-4 text-sm text-slate-600">
        <span>
          抽到 <strong className="text-slate-900 text-base">{preview.rows.length}</strong> 個欄位，
          已勾選 <strong className="text-sky-700 text-base">{picked.size}</strong> 個
        </span>
      </div>

      <div className="grid grid-cols-[10rem_minmax(0,6fr)_minmax(0,5fr)] gap-5 items-start">
        <nav className="sticky top-6 space-y-1">
          {SECTIONS.map((s) => {
            const rows = rowsOf(s.prefix)
            const chosen = rows.filter((r) => picked.has(r.row_id)).length
            return (
              <button
                key={s.id}
                onClick={() => setActive(s.id)}
                disabled={rows.length === 0}
                className={`w-full text-left px-4 py-2.5 rounded-md text-sm transition flex items-center gap-2 ${
                  s.id === active
                    ? 'bg-sky-50 text-sky-800 font-medium border border-sky-200'
                    : 'text-slate-600 hover:bg-slate-100 border border-transparent'
                } disabled:text-slate-300 disabled:hover:bg-transparent`}
              >
                <span className="flex-1">{s.title}</span>
                <span className="text-xs text-slate-400">
                  {rows.length === 0 ? '—' : `${chosen}/${rows.length}`}
                </span>
              </button>
            )
          })}
        </nav>

        <div className="sticky top-6">
          <Suspense
            fallback={<div className="text-sm text-slate-400 py-8 text-center">履歷預覽載入中…</div>}
          >
            <ResumeDocView
              importId={preview.import_id}
              marks={preview.rows.map((r) => ({ id: r.row_id, text: r.incoming }))}
              hoveredId={hovered}
            />
          </Suspense>
        </div>

        <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
          {shown.length === 0 ? (
            <p className="text-sm text-slate-400 text-center py-12">這個主題沒有抽到資料</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-600 text-xs">
                <tr>
                  <th className="w-12 px-4 py-3">
                    <SelectAllBox
                      rows={shown}
                      picked={picked}
                      onToggle={(select) => toggleAll(shown, select)}
                    />
                  </th>
                  <th className="text-left px-4 py-3 font-medium">欄位</th>
                  <th className="text-left px-4 py-3 font-medium">我的資料現在的值</th>
                  <th className="text-left px-4 py-3 font-medium">從履歷抽到的值</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {shown.map((row) => (
                  <Row
                    key={row.row_id}
                    row={row}
                    label={labelOf(row.field_key)}
                    checked={picked.has(row.row_id)}
                    onToggle={() => toggle(row.row_id)}
                    onHover={setHovered}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between pb-8">
        <button
          onClick={() => {
            clearSaved()
            setPreview(null)
            setPicked(new Set())
            setTrackingId(null)
          }}
          className="text-sm text-slate-500 hover:text-slate-800"
        >
          ← 換一份檔案
        </button>
        <button
          onClick={apply}
          disabled={applying || picked.size === 0}
          className="px-6 py-2.5 rounded-md bg-sky-600 text-white font-medium
                     hover:bg-sky-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          {applying ? '匯入中…' : `匯入勾選的 ${picked.size} 項`}
        </button>
      </div>
    </div>
  )
}

function Row({
  row,
  label,
  checked,
  onToggle,
  onHover,
}: {
  row: ImportRow
  label: string
  checked: boolean
  onToggle: () => void
  onHover: (rowId: string | null) => void
}) {
  const willOverwrite = checked && row.current.trim() !== ''
  return (
    <tr
      className={`${willOverwrite ? 'bg-amber-50/50' : ''} hover:bg-sky-50/60`}
      onMouseEnter={() => onHover(row.row_id)}
      onMouseLeave={() => onHover(null)}
    >
      <td className="px-4 py-2.5">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="w-4 h-4 accent-sky-600"
        />
      </td>
      <td className="px-4 py-2.5">
        <span className="text-slate-800">{label}</span>
        {row.ordinal > 0 && (
          <span className="ml-1 text-xs text-slate-400">第 {row.ordinal + 1} 筆</span>
        )}
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
}

function SelectAllBox({
  rows,
  picked,
  onToggle,
}: {
  rows: ImportRow[]
  picked: Set<string>
  onToggle: (select: boolean) => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  const chosen = rows.filter((r) => picked.has(r.row_id)).length
  const all = rows.length > 0 && chosen === rows.length

  // indeterminate 只能用 JS 設，沒有對應的 HTML 屬性
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = chosen > 0 && !all
  }, [chosen, all])

  return (
    <input
      ref={ref}
      type="checkbox"
      checked={all}
      disabled={rows.length === 0}
      onChange={() => onToggle(!all)}
      title={all ? '取消全選這個主題' : '全選這個主題'}
      className="w-4 h-4 accent-sky-600 disabled:cursor-not-allowed"
    />
  )
}

