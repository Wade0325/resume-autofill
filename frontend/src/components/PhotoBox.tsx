import { useRef, useState, type ChangeEvent } from 'react'
import { api, errorText } from '../api'

/**
 * 大頭照：存一張，填履歷時自動貼進表格的照片格（依格寬縮放）。
 * 存的時候會轉成 JPEG 並丟掉 EXIF——手機拍的照片裡帶著機型與拍攝地點。
 */
export default function PhotoBox({ onError }: { onError: (message: string) => void }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [version, setVersion] = useState(0) // 換過照片就讓瀏覽器重抓
  const [has, setHas] = useState(true) // 先當有，載不到再改成沒有
  const [busy, setBusy] = useState(false)

  async function pick(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setBusy(true)
    try {
      await api.uploadPhoto(file)
      setHas(true)
      setVersion((v) => v + 1)
    } catch (err) {
      onError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!window.confirm('移除大頭照？之後填履歷就不會再貼照片。')) return
    try {
      await api.deletePhoto()
      setHas(false)
      setVersion((v) => v + 1)
    } catch (err) {
      onError(errorText(err))
    }
  }

  return (
    <div className="flex items-start gap-4">
      <div className="w-24 h-32 border border-slate-200 rounded-md bg-slate-50 overflow-hidden flex items-center justify-center">
        {has ? (
          <img
            src={api.photoUrl(version)}
            alt="大頭照"
            className="w-full h-full object-cover"
            onError={() => setHas(false)}
          />
        ) : (
          <span className="text-xs text-slate-400 text-center px-2">還沒有照片</span>
        )}
      </div>
      <div className="text-sm">
        <div className="text-slate-700">大頭照</div>
        <p className="text-xs text-slate-500 mt-1 max-w-sm">
          表格上有照片格（「最近半年內二吋半身脫帽照片」這類）就會自動貼上，依格寬縮放。
          存檔時會轉成 JPEG 並清掉 EXIF（拍攝機型、地點）。
        </p>
        <div className="mt-2 flex items-center gap-2">
          <button
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            className="text-xs px-3 py-1.5 rounded-md border border-slate-300 text-slate-600
                       hover:bg-slate-50 disabled:opacity-40"
          >
            {busy ? '上傳中…' : has ? '換一張' : '選擇照片'}
          </button>
          {has && (
            <button
              onClick={remove}
              className="text-xs px-3 py-1.5 rounded-md border border-slate-200 text-slate-400
                         hover:bg-rose-50 hover:text-rose-600 hover:border-rose-200"
            >
              移除
            </button>
          )}
        </div>
      </div>
      <input
        ref={fileRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/bmp"
        onChange={pick}
        className="hidden"
        aria-label="選擇大頭照"
      />
    </div>
  )
}
