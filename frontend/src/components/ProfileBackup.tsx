import { useRef, useState, type ChangeEvent, type ReactNode } from 'react'
import { api, errorText, type Profile, type ProfileVersion } from '../api'

// 版本是「被什麼換掉之前」的樣子
const REASON: Record<string, string> = {
  save: '儲存前',
  import: '匯入履歷前',
  restore: '還原版本前',
  file: '從檔案還原前',
}

type Ask = { title: string; body: string; confirm: string; run: () => Promise<void> }

/**
 * 我的資料的備份與版本：匯出成檔案、從檔案還原、還原到之前的版本。
 * 還原前後端會先把目前的內容留成一個版本，所以每一步都退得回去。
 */
export default function ProfileBackup({
  dirty,
  onRestored,
  onError,
}: {
  dirty: boolean
  onRestored: (profile: Profile, notice: string) => void
  onError: (message: string) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const exportRef = useRef<HTMLAnchorElement>(null)
  const [versions, setVersions] = useState<ProfileVersion[] | null>(null) // null＝版本紀錄沒開
  const [ask, setAsk] = useState<Ask | null>(null)
  const [busy, setBusy] = useState(false)
  // 還原會蓋掉畫面上還沒存的修改，要先講清楚
  const unsaved = dirty ? '畫面上還沒儲存的修改會一起捨棄。' : ''

  function exportFile() {
    if (!dirty) {
      exportRef.current?.click()
      return
    }
    setAsk({
      title: '還有未儲存的變更',
      body: '匯出的是上次儲存的內容，還沒儲存的修改不會在檔案裡。',
      confirm: '照樣匯出',
      run: async () => exportRef.current?.click(),
    })
  }

  async function openVersions() {
    try {
      setVersions(await api.profileVersions())
    } catch (e) {
      onError(errorText(e))
    }
  }

  async function pickFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = '' // 同一個檔案再選一次也要觸發
    if (!file) return
    let data: unknown
    try {
      data = JSON.parse(await file.text())
    } catch {
      onError(`「${file.name}」不是我的資料的備份檔`)
      return
    }
    setAsk({
      title: `用「${file.name}」取代目前的我的資料？`,
      body: `目前的內容會先留一個版本，之後可以從「版本紀錄」還原回來。${unsaved}`,
      confirm: '還原',
      run: async () => onRestored(await api.restoreProfileFile(data), `已從「${file.name}」還原`),
    })
  }

  function pickVersion(v: ProfileVersion) {
    setAsk({
      title: `還原到 ${when(v.created_at)}（${REASON[v.reason] ?? v.reason}）的資料？`,
      body: `目前的內容會先留一個版本，改變主意可以再還原回來。${unsaved}`,
      confirm: '還原',
      run: async () => {
        onRestored(await api.restoreVersion(v.id), `已還原到 ${when(v.created_at)} 的版本`)
        setVersions(null)
      },
    })
  }

  async function confirmAsk() {
    if (!ask) return
    setBusy(true)
    try {
      await ask.run()
    } catch (e) {
      onError(errorText(e))
    } finally {
      setBusy(false)
      setAsk(null)
    }
  }

  return (
    <>
      <div className="flex items-center gap-1 text-sm">
        <ToolButton onClick={exportFile}>匯出備份</ToolButton>
        <ToolButton onClick={() => fileRef.current?.click()}>從檔案還原</ToolButton>
        <ToolButton onClick={openVersions}>版本紀錄</ToolButton>
      </div>
      <a ref={exportRef} href={api.exportProfileUrl} download className="hidden" aria-hidden="true" />
      <input
        ref={fileRef}
        type="file"
        accept=".json,application/json"
        onChange={pickFile}
        className="hidden"
        aria-label="選擇備份檔"
      />

      {versions && (
        <Modal>
          <h3 className="font-semibold text-slate-900">版本紀錄</h3>
          <p className="text-sm text-slate-500 mt-1">
            每次儲存、匯入履歷或還原之前，都會先留下當時的資料（最多 30 份）。
          </p>
          {versions.length === 0 ? (
            <p className="text-sm text-slate-400 text-center py-8">還沒有留下任何版本</p>
          ) : (
            <ul className="mt-4 divide-y divide-slate-100 max-h-96 overflow-y-auto">
              {versions.map((v, i) => (
                <li key={v.id} className="flex items-center gap-3 py-2.5 text-sm">
                  <span className="w-36 text-slate-800 tabular-nums">{when(v.created_at)}</span>
                  <span className="flex-1 text-slate-600">
                    {REASON[v.reason] ?? v.reason}
                    {i === 0 && (
                      <span className="ml-2 text-xs bg-sky-100 text-sky-800 rounded px-1.5 py-0.5">
                        上一版
                      </span>
                    )}
                  </span>
                  <span className="text-xs text-slate-400">
                    {v.changed === 0 ? '跟現在一樣' : `跟現在差 ${v.changed} 欄`}
                  </span>
                  <button
                    onClick={() => pickVersion(v)}
                    aria-label={`還原 ${when(v.created_at)} 的版本`}
                    className="px-3 py-1 rounded-md border border-slate-300 text-slate-600 hover:bg-slate-50"
                  >
                    還原
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex justify-end mt-6">
            <button
              onClick={() => setVersions(null)}
              className="px-4 py-2 text-sm rounded-md border border-slate-300 text-slate-600 hover:bg-slate-50"
            >
              關閉
            </button>
          </div>
        </Modal>
      )}

      {ask && (
        <Modal>
          <h3 className="font-semibold text-slate-900">{ask.title}</h3>
          <p className="text-sm text-slate-600 mt-2">{ask.body}</p>
          <div className="flex justify-end gap-2 mt-6">
            <button
              onClick={() => setAsk(null)}
              className="px-4 py-2 text-sm rounded-md border border-slate-300 text-slate-600 hover:bg-slate-50"
            >
              取消
            </button>
            <button
              onClick={confirmAsk}
              disabled={busy}
              className="px-4 py-2 text-sm rounded-md bg-sky-600 text-white font-medium
                         hover:bg-sky-700 disabled:bg-slate-300"
            >
              {busy ? '處理中…' : ask.confirm}
            </button>
          </div>
        </Modal>
      )}
    </>
  )
}

/** 月/日 時:分；不是今年的才帶年份。 */
function when(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString('zh-TW', {
    year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function ToolButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="px-3 py-1.5 rounded-md text-slate-600 hover:bg-slate-100 hover:text-slate-900"
    >
      {children}
    </button>
  )
}

function Modal({ children }: { children: ReactNode }) {
  return (
    <div className="fixed inset-0 bg-slate-900/40 flex items-center justify-center p-4 z-50">
      <div role="dialog" aria-modal="true" className="bg-white rounded-lg shadow-xl max-w-lg w-full p-6">
        {children}
      </div>
    </div>
  )
}
