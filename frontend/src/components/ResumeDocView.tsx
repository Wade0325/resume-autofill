import { useEffect, useMemo, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import type { PDFPageProxy } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

/**
 * 匯入頁中間欄：上傳履歷的真排版（LibreOffice 轉 PDF、pdf.js 渲染），
 * 並把抽到的每個值框出來。
 *
 * 定位不經過模型：reader 抽出的值本來就強制逐字出現在原文，
 * 所以拿 pdf.js 的文字層做子字串搜尋就能找到值的座標。
 * 滑過右欄的列 → hoveredId 變化 → 對應框加粗並捲到可見處。
 */

export type Mark = { id: string; text: string }

type TextItem = { str: string; x: number; y: number; w: number; h: number }
type PageData = { page: PDFPageProxy; width: number; height: number; items: TextItem[] }
type Box = { page: number; left: number; top: number; width: number; height: number } // 皆為頁面比例

const SCALE = 2 // 2 倍解析度再縮到欄寬，文字才不會糊

export default function ResumeDocView({
  importId,
  marks,
  hoveredId,
}: {
  importId: string
  marks: Mark[]
  hoveredId: string | null
}) {
  const [pages, setPages] = useState<PageData[]>([])
  const [loading, setLoading] = useState(true)
  const [unavailable, setUnavailable] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // StrictMode 會把 effect 跑兩次，用 token 丟棄前一輪的結果
    const token = { cancelled: false }
    setLoading(true)
    setUnavailable('')
    ;(async () => {
      const res = await fetch(`/api/imports/${importId}/preview.pdf`)
      if (!res.ok) {
        let detail = `HTTP ${res.status}`
        try {
          detail = (await res.json()).detail ?? detail
        } catch {
          // 回應不是 JSON，沿用狀態碼
        }
        throw new Error(detail)
      }
      const data = await res.arrayBuffer()
      const doc = await pdfjs.getDocument({ data }).promise
      const out: PageData[] = []
      for (let i = 1; i <= doc.numPages; i++) {
        const page = await doc.getPage(i)
        const viewport = page.getViewport({ scale: SCALE })
        const content = await page.getTextContent()
        const items: TextItem[] = []
        for (const it of content.items as any[]) {
          if (!it.str?.trim()) continue
          const tx = pdfjs.Util.transform(viewport.transform, it.transform)
          const fh = Math.hypot(tx[2], tx[3])
          items.push({ str: it.str, x: tx[4], y: tx[5] - fh, w: it.width * SCALE, h: fh })
        }
        out.push({ page, width: viewport.width, height: viewport.height, items })
      }
      if (token.cancelled) return
      setPages(out)
      setLoading(false)
    })().catch((e: any) => {
      if (token.cancelled) return
      setUnavailable(e.message)
      setLoading(false)
    })
    return () => {
      token.cancelled = true
    }
  }, [importId])

  const boxMap = useMemo(() => findBoxes(pages, marks), [pages, marks])

  // 滑到的列若已定位，把它的框捲進可視範圍
  useEffect(() => {
    if (!hoveredId || !boxMap[hoveredId]) return
    scrollRef.current
      ?.querySelector(`[data-mark="${CSS.escape(hoveredId)}"]`)
      ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [hoveredId, boxMap])

  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      <div className="px-4 py-2 text-xs font-medium text-slate-600 bg-slate-50 border-b border-slate-200">
        上傳的履歷
        <span className="ml-2 font-normal text-slate-400">框線＝抽到的值；滑過右側欄位會標示位置</span>
        {loading && <span className="ml-2 font-normal text-sky-600">產生中…</span>}
      </div>

      {unavailable ? (
        <p className="text-sm text-slate-400 text-center py-12 px-6">
          無法顯示履歷預覽：{unavailable}
        </p>
      ) : (
        <div ref={scrollRef} className="overflow-auto max-h-[78vh] bg-slate-100 p-3 space-y-3">
          {pages.map((pg, idx) => (
            <PageView key={idx} data={pg} pageIndex={idx} boxMap={boxMap} hoveredId={hoveredId} />
          ))}
        </div>
      )}
    </div>
  )
}

function PageView({
  data,
  pageIndex,
  boxMap,
  hoveredId,
}: {
  data: PageData
  pageIndex: number
  boxMap: Record<string, Box[]>
  hoveredId: string | null
}) {
  return (
    <div className="relative">
      <PageCanvas page={data.page} />
      {Object.entries(boxMap).map(([id, boxes]) =>
        boxes
          .filter((b) => b.page === pageIndex)
          .map((b, i) => (
            <div
              key={`${id}.${i}`}
              data-mark={id}
              className={`absolute pointer-events-none rounded-sm transition-colors ${
                id === hoveredId
                  ? 'border-2 border-sky-500 bg-sky-400/25'
                  : 'border border-amber-400/70 bg-amber-300/15'
              }`}
              style={{
                left: `${(b.left - 0.003) * 100}%`,
                top: `${(b.top - 0.004) * 100}%`,
                width: `${(b.width + 0.006) * 100}%`,
                height: `${(b.height + 0.008) * 100}%`,
              }}
            />
          )),
      )}
    </div>
  )
}

function PageCanvas({ page }: { page: PDFPageProxy }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const viewport = page.getViewport({ scale: SCALE })
    const canvas = ref.current!
    canvas.width = viewport.width
    canvas.height = viewport.height
    const task = page.render({ canvas, viewport })
    task.promise.catch(() => {}) // cancel 會 reject，不需處理
    return () => task.cancel()
  }, [page])
  return <canvas ref={ref} className="w-full bg-white border border-slate-300 shadow-sm" />
}

const SQUASH_RE = /[\s　]+/g

function findBoxes(pages: PageData[], marks: Mark[]): Record<string, Box[]> {
  if (pages.length === 0) return {}

  let hay = ''
  const owner: { p: number; i: number }[] = [] // hay 每個字元來自哪一頁的哪個片段
  pages.forEach((pg, p) =>
    pg.items.forEach((it, i) => {
      const sq = it.str.replace(SQUASH_RE, '')
      hay += sq
      for (let k = 0; k < sq.length; k++) owner.push({ p, i })
    }),
  )

  const out: Record<string, Box[]> = {}
  for (const m of marks) {
    const needle = m.text.replace(SQUASH_RE, '')
    if (!needle) continue
    const at = hay.indexOf(needle) // 同值多次出現時取第一處，足夠指出位置
    if (at < 0) continue

    const seen = new Set<string>()
    const boxes: Box[] = []
    for (let k = at; k < at + needle.length; k++) {
      const { p, i } = owner[k]
      const key = `${p}.${i}`
      if (seen.has(key)) continue
      seen.add(key)
      const pg = pages[p]
      const it = pg.items[i]
      boxes.push({
        page: p,
        left: it.x / pg.width,
        top: it.y / pg.height,
        width: it.w / pg.width,
        height: it.h / pg.height,
      })
    }
    out[m.id] = mergeLines(boxes)
  }
  return out
}

/** 同一行被切成多個片段時合併成一個框，避免一個值畫出好幾條細框。 */
function mergeLines(boxes: Box[]): Box[] {
  const merged: Box[] = []
  for (const b of [...boxes].sort((a, z) => a.page - z.page || a.top - z.top || a.left - z.left)) {
    const last = merged[merged.length - 1]
    const sameLine =
      last &&
      last.page === b.page &&
      Math.abs(last.top - b.top) < Math.max(last.height, b.height) * 0.6
    if (sameLine) {
      const right = Math.max(last.left + last.width, b.left + b.width)
      const bottom = Math.max(last.top + last.height, b.top + b.height)
      last.left = Math.min(last.left, b.left)
      last.top = Math.min(last.top, b.top)
      last.width = right - last.left
      last.height = bottom - last.top
    } else {
      merged.push({ ...b })
    }
  }
  return merged
}
