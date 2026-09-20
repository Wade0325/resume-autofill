import { Fragment, Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, errorText, fetchBlob, type FieldSpec, type ImportPreview, type ImportRow } from '../api'
import { useBackgroundUpload } from '../useBackgroundUpload'
import { SECTIONS } from '../sections'
import Dropzone from '../components/Dropzone'
import { PageShell, FooterBar, OverwriteBadge } from '../components/common'

// pdf.js 佔了主 bundle 一半以上，等真的要顯示履歷對照時再載
const ResumeDocView = lazy(() => import('../components/ResumeDocView'))

// 勾選狀態另存一份：切到別頁再回來能接續。只存 id 與勾選，列表本身回頭
// 跟後端重拿，免得 sessionStorage 裡放一份會過期的副本。
const KEY_PICKED = 'import.picked'

export default function ImportPage() {
  const [fields, setFields] = useState<FieldSpec[]>([])
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [active, setActive] = useState<string>(SECTIONS[0].id)
  const [hovered, setHovered] = useState<string | null>(null) // 滑到的列，讓履歷上的框連動
  const [applying, setApplying] = useState(false)
  // 剛匯入的筆數與欄位,顯示成功列並讓「查看我的資料」帶出變更標示
  const [applied, setApplied] = useState<{ count: number; changed: string[] } | null>(null)

  // 讀取在後端背景執行，hook 負責上傳、輪詢進度與 sessionStorage 接續
  const { phase, error, setError, upload, track, reset } = useBackgroundUpload({
    storageKey: 'import.id',
    start: async (file, onProgress) => (await api.analyzeImport(file, onProgress)).import_id,
    getState: api.getImport,
    hasResult: !!preview,
    onReady: (st) => {
      setPreview(st.preview)
      // 切頁回來沿用之前的勾選；剛完成的新匯入用預設勾選
      const saved = sessionStorage.getItem(KEY_PICKED)
      if (saved) {
        setPicked(new Set(JSON.parse(saved)))
      } else {
        remember(
          new Set(st.preview.rows.filter((r) => r.default_checked).map((r) => r.row_id)),
        )
      }
      const first = SECTIONS.find((s) =>
        st.preview.rows.some((r) => r.field_key.startsWith(s.prefix)),
      )
      if (first) setActive(first.id)
    },
    onDiscard: () => {
      sessionStorage.removeItem(KEY_PICKED)
      setApplied(null)
    },
  })

  useEffect(() => {
    api.fields().then(setFields).catch((e) => setError(errorText(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 固定住陣列身分：marks 只跟 preview 有關。每次 render 建新陣列的話，
  // 滑過任一列（hovered 變動）都會讓預覽以為 marks 換了而整份重渲染
  const marks = useMemo(
    () => (preview ? preview.rows.map((r) => ({ id: r.row_id, text: r.incoming })) : []),
    [preview],
  )

  function remember(next: Set<string>) {
    setPicked(next)
    sessionStorage.setItem(KEY_PICKED, JSON.stringify([...next]))
  }

  async function apply() {
    if (!preview) return
    setApplying(true)
    setError('')
    try {
      // 寫到第幾筆以後端回報為準：新增的幾筆會往前補，不一定是清單上的序號
      const result = await api.applyImport(preview.import_id, [...picked])
      // 留在原頁繼續:重拉一次預覽,「現有值」欄立刻反映剛寫入的資料;
      // 勾選歸零,漏掉的項目可以再勾再匯,不必重傳重跑一次分析
      const st = await api.getImport(preview.import_id)
      if (st.status === 'ready') setPreview(st.preview)
      remember(new Set())
      setApplied({ count: result.applied, changed: result.changed })
    } catch (e: any) {
      setError(errorText(e))
    } finally {
      setApplying(false)
    }
  }

  async function pasteText(text: string) {
    setError('')
    try {
      track((await api.importText(text)).import_id)
    } catch (e) {
      setError(errorText(e))
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
      <PageShell
        title="匯入履歷"
        desc="上傳一份已經填好的履歷，系統會把裡面的資料抽出來存進「我的資料」。"
        error={error}
      >
        <Dropzone
          title="把已填寫的履歷拖到這裡"
          hint="或點擊選擇檔案"
          phase={phase}
          onFile={upload}
          accept=".pdf,.docx"
          note="接受 104 履歷的 .pdf 與 Word 的 .docx"
        />
        <PasteText phase={phase} onSubmit={pasteText} />
      </PageShell>
    )
  }

  const labelOf = (key: string) => fields.find((f) => f.key === key)?.label ?? key
  const rowsOf = (prefix: string) => preview.rows.filter((r) => r.field_key.startsWith(prefix))
  const shown = rowsOf(SECTIONS.find((s) => s.id === active)!.prefix)

  return (
    <PageShell title="匯入履歷" desc={preview.filename} error={error}>
      {applied && (
        <div className="bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-md px-4 py-3 text-sm">
          已匯入 {applied.count} 項。清單還在，漏掉的項目可以繼續勾選再匯入，或
          <Link
            to="/profile"
            state={{ changed: applied.changed }}
            className="underline hover:text-emerald-900 ml-1"
          >
            查看我的資料
          </Link>
        </div>
      )}
      {preview.note && (
        <div className="bg-amber-50 border border-amber-200 text-amber-800 rounded-md px-4 py-3 text-sm">
          {preview.note}
        </div>
      )}
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
          {preview.has_source ? (
            <Suspense
              fallback={<div className="text-sm text-slate-400 py-8 text-center">履歷預覽載入中…</div>}
            >
              <ResumeDocView
                importId={preview.import_id}
                filename={preview.filename}
                marks={marks}
                hoveredId={hovered}
              />
            </Suspense>
          ) : (
            <PastedText importId={preview.import_id} />
          )}
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
                {shown.map((row, i) => (
                  <Fragment key={row.row_id}>
                    {/* 多筆資料每一筆開頭標出它會補進哪一筆、還是新增一筆 */}
                    {row.entry &&
                      (i === 0 ||
                        shown[i - 1].entry !== row.entry ||
                        shown[i - 1].ordinal !== row.ordinal) && <EntryHeader row={row} />}
                    <Row
                      row={row}
                      label={labelOf(row.field_key)}
                      checked={picked.has(row.row_id)}
                      onToggle={() => toggle(row.row_id)}
                      onHover={setHovered}
                    />
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <FooterBar
        onRestart={() => {
          reset()
          setPreview(null)
          setPicked(new Set())
        }}
        onSubmit={apply}
        disabled={applying || picked.size === 0}
        label={applying ? '匯入中…' : `匯入勾選的 ${picked.size} 項`}
      />
    </PageShell>
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
      <td className="px-4 py-2.5 align-top">
        <span className="text-slate-800">{label}</span>
      </td>
      {/* 值可能是整段自傳或多行工作內容:完整顯示、保留換行,不截斷 */}
      <td className="px-4 py-2.5 align-top whitespace-pre-wrap break-words">
        {row.current.trim() ? (
          <span className={willOverwrite ? 'line-through text-slate-400' : 'text-slate-600'}>
            {row.current}
          </span>
        ) : (
          <span className="text-slate-300">（空白）</span>
        )}
      </td>
      <td className="px-4 py-2.5 align-top whitespace-pre-wrap break-words text-slate-900">
        {row.incoming}
        {willOverwrite && <OverwriteBadge>將覆蓋</OverwriteBadge>}
      </td>
    </tr>
  )
}

// 依名稱（學校、公司…）對上我的資料裡的那一筆就補進去，對不上的新增一筆
function EntryHeader({ row }: { row: ImportRow }) {
  const isNew = row.entry === 'new'
  return (
    <tr className="bg-slate-50/70">
      <td colSpan={4} className="px-4 py-2 text-xs">
        <span
          className={`inline-block rounded px-1.5 py-0.5 font-medium ${
            isNew ? 'bg-emerald-100 text-emerald-800' : 'bg-sky-100 text-sky-800'
          }`}
        >
          {isNew ? '新增一筆' : `補進我的資料第 ${row.ordinal + 1} 筆`}
        </span>
        {row.entry_name && <span className="ml-2 text-slate-600">{row.entry_name}</span>}
      </td>
    </tr>
  )
}

/** 貼上文字匯入：手邊只有網頁版履歷、或從 PDF 複製出來的內容時用這個。 */
function PasteText({
  phase,
  onSubmit,
}: {
  phase: { kind: string }
  onSubmit: (text: string) => void
}) {
  const [text, setText] = useState('')
  const busy = phase.kind !== 'idle'
  return (
    <details className="bg-white border border-slate-200 rounded-lg">
      <summary className="cursor-pointer select-none px-4 py-3 text-sm text-slate-700">
        沒有檔案？貼上文字也可以
        <span className="ml-2 text-xs text-slate-500">
          網頁版履歷、從 PDF 複製出來的內容都行
        </span>
      </summary>
      <div className="px-4 pb-4">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={8}
          aria-label="貼上履歷內容"
          placeholder="把履歷內容貼在這裡…"
          className="w-full text-sm border border-slate-300 rounded-md px-3 py-2
                     focus:outline-none focus:ring-2 focus:ring-sky-500"
        />
        <div className="mt-2 flex items-center gap-3">
          <button
            onClick={() => onSubmit(text)}
            disabled={busy || text.trim().length < 20}
            className="px-4 py-2 rounded-md bg-sky-600 text-white text-sm font-medium
                       hover:bg-sky-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            開始讀取
          </button>
          <span className="text-xs text-slate-500">跟上傳檔案一樣，讀完可以逐欄勾選再匯入</span>
        </div>
      </div>
    </details>
  )
}

/** 貼上的文字沒有原稿可以渲染，就把文字本身列出來對照。 */
function PastedText({ importId }: { importId: string }) {
  const [text, setText] = useState('')
  useEffect(() => {
    fetchBlob(`/imports/${importId}/source`)
      .then((b) => b.text())
      .then(setText)
      .catch(() => setText(''))
  }, [importId])
  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      <div className="px-4 py-2 text-xs text-slate-500 border-b border-slate-100">
        貼上的內容
      </div>
      <pre className="p-4 text-xs text-slate-700 whitespace-pre-wrap break-words max-h-[70vh] overflow-y-auto">
        {text}
      </pre>
    </div>
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

