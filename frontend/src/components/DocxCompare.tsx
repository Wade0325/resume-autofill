import { useEffect, useRef, useState } from 'react'
import { renderAsync } from 'docx-preview'

/**
 * 左右對照：左邊上傳的原稿，右邊套用「我的資料」後的樣子。
 * 兩份都是同一份 .docx，直接在瀏覽器渲染，版面自然對齊；黃底由文件本身帶著。
 *
 * version 變了就重抓右邊——使用者改了對映後要看到新結果。
 */
export default function DocxCompare({ jobId, version }: { jobId: string; version: number }) {
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const leftRef = useRef<HTMLDivElement>(null)
  const rightRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // StrictMode 會把 effect 跑兩次；沒有這個 token，兩輪渲染會各塞一份進容器
    const token = { cancelled: false }
    setLoading(true)
    setError('')
    Promise.all([
      render(`/api/jobs/${jobId}/preview.docx?which=original`, leftRef.current!, token),
      render(`/api/jobs/${jobId}/preview.docx?which=filled&v=${version}`, rightRef.current!, token),
    ])
      .catch((e: Error) => !token.cancelled && setError(e.message))
      .finally(() => !token.cancelled && setLoading(false))
    return () => {
      token.cancelled = true
    }
  }, [jobId, version])

  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      <div className="flex text-xs font-medium text-slate-600 bg-slate-50 border-b border-slate-200">
        <div className="w-1/2 px-4 py-2 border-r border-slate-200">原始文件</div>
        <div className="w-1/2 px-4 py-2">
          填寫後預覽
          <span className="ml-2 font-normal text-slate-400">黃底＝填入的值</span>
          {loading && <span className="ml-2 font-normal text-sky-600">產生中…</span>}
        </div>
      </div>

      {error && (
        <div className="px-4 py-3 text-sm text-rose-700 bg-rose-50 border-b border-rose-200">
          {error}
        </div>
      )}

      <div className="overflow-auto max-h-[80vh] bg-slate-100 p-4">
        <div className="flex gap-4 items-start">
          <div ref={leftRef} className="w-1/2 min-w-0" />
          <div ref={rightRef} className="w-1/2 min-w-0" />
        </div>
      </div>
    </div>
  )
}

async function render(url: string, host: HTMLDivElement, token: { cancelled: boolean }) {
  const res = await fetch(url)
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      // 回應不是 JSON，沿用狀態碼
    }
    throw new Error(detail)
  }
  const blob = await res.blob()
  if (token.cancelled) return
  host.replaceChildren()
  host.style.zoom = '1'
  const width = host.clientWidth
  await renderAsync(blob, host, undefined, { className: 'docx', inWrapper: false })
  if (token.cancelled) {
    host.replaceChildren()
    return
  }
  // 文件用實際紙張寬度渲染，縮到欄寬才塞得下。用 zoom 而非 transform：
  // transform 不會重排，容器高度會停在縮放前的尺寸，底下留一大片空白
  const page = host.querySelector('section')
  if (page) host.style.zoom = String(width / page.offsetWidth)
}
