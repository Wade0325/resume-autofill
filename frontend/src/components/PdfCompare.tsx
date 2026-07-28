import { useEffect, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

/**
 * 真排版對照：原始文件與套用後的文件都由 LibreOffice 轉成 PDF，
 * 這裡逐頁畫在左右兩欄。兩份 PDF 來自同一份文件，頁面尺寸相同，
 * 放在同一個捲動容器裡自然對齊。
 *
 * version 變了就重抓 filled——使用者改了對映後要看到新結果。
 */
export default function PdfCompare({
  jobId,
  version,
  onUnavailable,
}: {
  jobId: string
  version: number
  onUnavailable: (message: string) => void
}) {
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const leftRef = useRef<HTMLDivElement>(null)
  const rightRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // StrictMode 會把 effect 跑兩次；沒有這個 token，兩輪渲染會把
    // 同一份 PDF 的頁面各塞一次進容器，畫面就重複了
    const token = { cancelled: false }
    setLoading(true)
    setError('')
    Promise.all([
      renderPdf(`/api/jobs/${jobId}/preview.pdf?which=original`, leftRef.current!, token),
      renderPdf(`/api/jobs/${jobId}/preview.pdf?which=filled&v=${version}`, rightRef.current!, token),
    ])
      .catch((e: any) => {
        if (token.cancelled) return
        // 503 = 沒有 LibreOffice，這台機器永遠做不出 PDF，交給上層退回結構化預覽
        if (e.status === 503) onUnavailable(e.message)
        else setError(e.message)
      })
      .finally(() => !token.cancelled && setLoading(false))
    return () => {
      token.cancelled = true
    }
  }, [jobId, version, onUnavailable])

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

async function renderPdf(
  url: string,
  container: HTMLDivElement,
  token: { cancelled: boolean },
) {
  const res = await fetch(url)
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      // 回應不是 JSON，沿用狀態碼
    }
    const err: any = new Error(detail)
    err.status = res.status
    throw err
  }
  const data = await res.arrayBuffer()
  const doc = await pdfjs.getDocument({ data }).promise
  if (token.cancelled) return

  container.replaceChildren()
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i)
    if (token.cancelled) return
    // 2 倍解析度再縮到欄寬，文字才不會糊
    const viewport = page.getViewport({ scale: 2 })
    const canvas = document.createElement('canvas')
    canvas.width = viewport.width
    canvas.height = viewport.height
    canvas.className = 'w-full bg-white border border-slate-300 shadow-sm mb-4'
    container.appendChild(canvas)
    await page.render({ canvas, viewport }).promise
  }
}
