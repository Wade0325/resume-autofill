import { useEffect, useMemo, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import type { PDFPageProxy } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { errorText, fetchBlob } from '../api'
import { renderDocxInto } from './docx'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

/**
 * 匯入頁中間欄：上傳履歷的真排版，並把抽到的每個值框出來。
 * PDF 用 pdf.js，Word 用 docx-preview——兩種都在瀏覽器渲染，不經過轉檔。
 *
 * 定位不經過模型：reader 抽出的值本來就強制逐字出現在原文，
 * 子字串搜尋就找得到座標。滑過右欄的列 → 對應框加粗並捲到可見處。
 */

export type Mark = { id: string; text: string }

type TextItem = { str: string; x: number; y: number; w: number; h: number }
type PageData = { page: PDFPageProxy; width: number; height: number; items: TextItem[] }
type Box = { page: number; left: number; top: number; width: number; height: number } // 皆為頁面比例

const SCALE = 2 // 2 倍解析度再縮到欄寬，文字才不會糊

export default function ResumeDocView({
  importId,
  filename,
  marks,
  hoveredId,
}: {
  importId: string
  filename: string
  marks: Mark[]
  hoveredId: string | null
}) {
  const isPdf = filename.toLowerCase().endsWith('.pdf')
  const [pages, setPages] = useState<PageData[]>([])
  const [loading, setLoading] = useState(true)
  const [unavailable, setUnavailable] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // StrictMode 會把 effect 跑兩次，用 token 丟棄前一輪的結果
    const token = { cancelled: false }
    setLoading(true)
    setUnavailable('')
    if (!isPdf) {
      setLoading(false)
      return
    }
    ;(async () => {
      const blob = await fetchBlob(`/imports/${importId}/source`)
      const data = await blob.arrayBuffer()
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
      setUnavailable(errorText(e))
      setLoading(false)
    })
    return () => {
      token.cancelled = true
    }
  }, [importId, isPdf])

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
          {isPdf ? (
            pages.map((pg, idx) => (
              <PageView key={idx} data={pg} pageIndex={idx} boxMap={boxMap} hoveredId={hoveredId} />
            ))
          ) : (
            <DocxView importId={importId} marks={marks} hoveredId={hoveredId} onDone={setLoading} />
          )}
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

/** Word 履歷：docx-preview 渲染，抽到的值用 Range 量出位置畫框。 */
function DocxView({
  importId,
  marks,
  hoveredId,
  onDone,
}: {
  importId: string
  marks: Mark[]
  hoveredId: string | null
  onDone: (loading: boolean) => void
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [boxes, setBoxes] = useState<Record<string, Box[]>>({})
  const [rendered, setRendered] = useState(0) // 每完成一次渲染 +1，通知畫框 effect

  // 抓檔＋渲染只跟 importId 有關。marks 變動只需要重新畫框，
  // 不能連整份文件都重新下載重渲染
  useEffect(() => {
    const token = { cancelled: false }
    onDone(true)
    ;(async () => {
      const blob = await fetchBlob(`/imports/${importId}/source`)
      const host = hostRef.current!
      if (!(await renderDocxInto(blob, host, token))) return
      setRendered((n) => n + 1)
      onDone(false)
    })().catch(() => !token.cancelled && onDone(false))
    return () => {
      token.cancelled = true
    }
  }, [importId, onDone])

  useEffect(() => {
    if (!rendered) return
    setBoxes(locate(hostRef.current!, marks))
  }, [rendered, marks])

  return (
    <div className="relative">
      <div ref={hostRef} className="bg-white border border-slate-300 shadow-sm" />
      {Object.entries(boxes).map(([id, list]) =>
        list.map((b, i) => (
          <div
            key={`${id}.${i}`}
            data-mark={id}
            className={`absolute pointer-events-none rounded-sm transition-colors ${
              id === hoveredId
                ? 'border-2 border-sky-500 bg-sky-400/25'
                : 'border border-amber-400/70 bg-amber-300/15'
            }`}
            style={{
              left: `${b.left * 100}%`,
              top: `${b.top * 100}%`,
              width: `${b.width * 100}%`,
              height: `${b.height * 100}%`,
            }}
          />
        )),
      )}
    </div>
  )
}

/** 走過所有文字節點攤成一串，命中的字元用 Range 量位置——getClientRects 會自動分行。 */
function locate(host: HTMLElement, marks: Mark[]): Record<string, Box[]> {
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT)
  let hay = ''
  const owner: { node: Text; offset: number }[] = []
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = (n as Text).data
    for (let i = 0; i < text.length; i++) {
      const c = squash(text[i])
      for (let k = 0; k < c.length; k++) owner.push({ node: n as Text, offset: i })
      hay += c
    }
  }

  const base = host.parentElement!.getBoundingClientRect()
  const out: Record<string, Box[]> = {}
  for (const m of marks) {
    const needle = squash(m.text)
    if (!needle) continue
    const at = hay.indexOf(needle)
    if (at < 0) continue
    const range = document.createRange()
    range.setStart(owner[at].node, owner[at].offset)
    const end = owner[at + needle.length - 1]
    range.setEnd(end.node, end.offset + 1)
    out[m.id] = [...range.getClientRects()].map((r) => ({
      page: 0,
      left: (r.left - base.left) / base.width,
      top: (r.top - base.top) / base.height,
      width: r.width / base.width,
      height: r.height / base.height,
    }))
  }
  return out
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

// NFKC 要跟後端 convert.pdf_to_text 一致：PDF 字型常把中文對映到康熙部首區
// （「工」存成 U+2F2F），肉眼一樣但碼位不同，不正規化就框不出抽到的值
const squash = (s: string) => s.normalize('NFKC').replace(SQUASH_RE, '')

function findBoxes(pages: PageData[], marks: Mark[]): Record<string, Box[]> {
  if (pages.length === 0) return {}

  let hay = ''
  const owner: { p: number; i: number }[] = [] // hay 每個字元來自哪一頁的哪個片段
  pages.forEach((pg, p) =>
    pg.items.forEach((it, i) => {
      const sq = squash(it.str)
      hay += sq
      for (let k = 0; k < sq.length; k++) owner.push({ p, i })
    }),
  )

  const out: Record<string, Box[]> = {}
  for (const m of marks) {
    const needle = squash(m.text)
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
