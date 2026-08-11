import { useEffect, useRef, useState } from 'react'
import { errorText, fetchBlob } from '../api'
import { renderDocxInto } from './docx'

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

  // 左欄原稿只跟 jobId 有關；改對映（version++）只該重抓右欄，
  // 不必連原稿也重新下載重渲染
  useEffect(() => {
    // StrictMode 會把 effect 跑兩次；沒有這個 token，兩輪渲染會各塞一份進容器
    const token = { cancelled: false }
    render(`/jobs/${jobId}/preview.docx?which=original`, leftRef.current!, token)
      .catch((e: Error) => !token.cancelled && setError(errorText(e)))
    return () => {
      token.cancelled = true
    }
  }, [jobId])

  useEffect(() => {
    const token = { cancelled: false }
    setLoading(true)
    setError('')
    render(`/jobs/${jobId}/preview.docx?which=filled&v=${version}`, rightRef.current!, token)
      .catch((e: Error) => !token.cancelled && setError(errorText(e)))
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
  const blob = await fetchBlob(url)
  if (token.cancelled) return
  await renderDocxInto(blob, host, token)
}
