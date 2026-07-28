import { useRef, useState } from 'react'

export type UploadPhase =
  | { kind: 'idle' }
  | { kind: 'uploading'; percent: number }
  | { kind: 'analyzing'; seconds: number; stage: string }

type Props = {
  title: string
  hint: string
  phase: UploadPhase
  onFile: (file: File) => void
}

export default function Dropzone({ title, hint, phase, onFile }: Props) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const busy = phase.kind !== 'idle'

  function take(files: FileList | null) {
    const file = files?.[0]
    if (file) onFile(file)
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        if (!busy) take(e.dataTransfer.files)
      }}
      onClick={() => !busy && inputRef.current?.click()}
      className={`border-2 border-dashed rounded-xl p-12 text-center transition ${
        dragging ? 'border-sky-500 bg-sky-50' : 'border-slate-300 bg-white hover:border-slate-400'
      } ${busy ? 'cursor-wait' : 'cursor-pointer'}`}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".doc,.docx"
        className="hidden"
        onChange={(e) => {
          take(e.target.files)
          e.target.value = '' // 清空才能重複選同一個檔案
        }}
      />
      {busy ? <Progress phase={phase} /> : (
        <>
          <p className="text-slate-800 font-medium">{title}</p>
          <p className="text-sm text-slate-500 mt-1">{hint}</p>
          <p className="text-xs text-slate-400 mt-3">接受 .docx 與舊版 .doc（.doc 會自動轉檔）</p>
        </>
      )}
    </div>
  )
}

function Progress({ phase }: { phase: UploadPhase }) {
  // 上傳有明確百分比；解析與模型判斷沒有可回報的進度，
  // 所以改用已經過的秒數，至少讓人知道它還在動。
  const uploading = phase.kind === 'uploading'
  const percent = uploading ? phase.percent : 100

  const stage = phase.kind === 'analyzing' ? phase.stage : ''

  return (
    <div className="max-w-sm mx-auto">
      <div className="flex justify-between text-sm mb-2">
        <span className="text-slate-700">{uploading ? '上傳中' : stage || '分析中'}</span>
        <span className="text-slate-500 tabular-nums">
          {uploading ? `${percent}%` : `已 ${phase.kind === 'analyzing' ? phase.seconds : 0} 秒`}
        </span>
      </div>
      <div className="h-2 bg-slate-200 rounded-full overflow-hidden">
        <div
          className={`h-full bg-sky-500 transition-all ${uploading ? '' : 'animate-pulse'}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      {!uploading && (
        <p className="text-xs text-slate-400 mt-3">
          第一次遇到的格式要靠模型判讀，約 1～4 分鐘；看過的格式幾秒就好。
          分析在伺服器進行，切到其他頁面不會中斷。
        </p>
      )}
    </div>
  )
}
