import type { PlanItem, PreviewBlock, PreviewCell, PreviewSeg } from '../api'

/**
 * 左右對照：左邊是上傳的原始文件，右邊是套用「我的資料」後的樣子。
 *
 * 對齊的關鍵是「逐列配對」：每個段落、表格的每一列，左右兩邊放在
 * 同一個 flex 列裡（高度自動取較高的一邊），表格再用 table-fixed +
 * 相同的 colgroup，讓兩邊每一欄的寬度完全一致。
 */
export default function DocPreview({
  blocks,
  items,
}: {
  blocks: PreviewBlock[]
  items: PlanItem[]
}) {
  const byId = new Map(items.map((i) => [i.slot_id, i]))

  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      <div className="flex text-xs font-medium text-slate-600 bg-slate-50 border-b border-slate-200">
        <div className="w-1/2 px-4 py-2 border-r border-slate-200">原始文件</div>
        <div className="w-1/2 px-4 py-2">
          填寫後預覽
          <span className="ml-2 font-normal text-slate-400">
            藍底＝將填入的值，灰字＝略過不填
          </span>
        </div>
      </div>

      <div className="overflow-auto max-h-[75vh] p-4 space-y-3">
        {blocks.map((block, i) =>
          block.kind === 'p' ? (
            <PairedParagraph key={i} segs={block.segs} byId={byId} />
          ) : (
            <PairedTable key={i} block={block} byId={byId} />
          ),
        )}
      </div>
    </div>
  )
}

function PairedParagraph({
  segs,
  byId,
}: {
  segs: PreviewSeg[]
  byId: Map<string, PlanItem>
}) {
  return (
    <div className="flex items-stretch gap-4">
      <p className="w-1/2 text-sm text-slate-700 whitespace-pre-wrap break-all">
        {segs.map((s, i) => (
          <OriginalSeg key={i} seg={s} />
        ))}
      </p>
      <p className="w-1/2 text-sm text-slate-700 whitespace-pre-wrap break-all">
        {segs.map((s, i) => (
          <FilledSeg key={i} seg={s} item={s.s ? byId.get(s.s) : undefined} />
        ))}
      </p>
    </div>
  )
}

function PairedTable({
  block,
  byId,
}: {
  block: Extract<PreviewBlock, { kind: 'table' }>
  byId: Map<string, PlanItem>
}) {
  return (
    <div className="space-y-0">
      {block.rows.map((row, r) => (
        <div key={r} className="flex items-stretch gap-4">
          <RowTable cells={row.cells} gridCols={block.grid_cols} byId={byId} side="left" />
          <RowTable cells={row.cells} gridCols={block.grid_cols} byId={byId} side="right" />
        </div>
      ))}
    </div>
  )
}

/** 一列一個 table：左右同列包在同一個 flex 裡，高度才會同步。 */
function RowTable({
  cells,
  gridCols,
  byId,
  side,
}: {
  cells: PreviewCell[]
  gridCols: number
  byId: Map<string, PlanItem>
  side: 'left' | 'right'
}) {
  return (
    <table className="w-1/2 table-fixed border-collapse -mt-px">
      <colgroup>
        {Array.from({ length: gridCols }, (_, i) => (
          <col key={i} />
        ))}
      </colgroup>
      <tbody>
        <tr>
          {cells.map((cell, c) => (
            <td
              key={c}
              colSpan={cell.colspan}
              className="border border-slate-300 px-1.5 py-1 text-xs text-slate-700 align-top break-all"
            >
              {cell.segs.map((s, i) =>
                side === 'left' ? (
                  <OriginalSeg key={i} seg={s} />
                ) : (
                  <FilledSeg key={i} seg={s} item={s.s ? byId.get(s.s) : undefined} />
                ),
              )}
            </td>
          ))}
        </tr>
      </tbody>
    </table>
  )
}

function OriginalSeg({ seg }: { seg: PreviewSeg }) {
  if (!seg.s) return <>{seg.t}</>
  // 偵測到的可填位置：淡淡標一下，空格給一條底線佔位
  return (
    <span className="border-b border-dashed border-sky-300">
      {seg.t || '    '}
    </span>
  )
}

function FilledSeg({ seg, item }: { seg: PreviewSeg; item?: PlanItem }) {
  if (!seg.s || !item) return <>{seg.t}</>
  if (item.status === 'skip') {
    return <span className="text-slate-400">{seg.t}</span>
  }
  if (item.kind === 'checkbox') {
    return <>{checkOption(seg.t, item.value)}</>
  }
  return (
    <mark className="bg-sky-100 text-sky-900 rounded px-0.5" title={`原本：${seg.t || '（空白）'}`}>
      {item.value}
    </mark>
  )
}

/** 勾選題：把選中選項前面的 □ 換成 ■，其他保持原樣。 */
function checkOption(text: string, value: string) {
  if (!value) return text
  const escaped = value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const re = new RegExp(`[□☐▢◻](?=[ 　]*${escaped})`)
  if (!re.test(text)) return text
  const marked = text.replace(re, '■')
  const at = marked.indexOf('■')
  const end = marked.indexOf(value, at) + value.length
  return (
    <>
      {marked.slice(0, at)}
      <mark className="bg-sky-100 text-sky-900 rounded px-0.5">{marked.slice(at, end)}</mark>
      {marked.slice(end)}
    </>
  )
}
