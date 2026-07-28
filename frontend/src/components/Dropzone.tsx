import { useRef, useState } from 'react'

type Props = {
  title: string
  hint: string
  busy: boolean
  busyText: string
  onFile: (file: File) => void
}

export default function Dropzone({ title, hint, busy, busyText, onFile }: Props) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

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
      className={`border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition ${
        dragging ? 'border-sky-500 bg-sky-50' : 'border-slate-300 bg-white hover:border-slate-400'
      } ${busy ? 'opacity-60 cursor-wait' : ''}`}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".docx"
        className="hidden"
        onChange={(e) => {
          take(e.target.files)
          e.target.value = '' // 清空才能重複選同一個檔案
        }}
      />
      {busy ? (
        <p className="text-slate-600">{busyText}</p>
      ) : (
        <>
          <p className="text-slate-800 font-medium">{title}</p>
          <p className="text-sm text-slate-500 mt-1">{hint}</p>
          <p className="text-xs text-slate-400 mt-3">只接受 .docx，舊版 .doc 請先另存新檔</p>
        </>
      )}
    </div>
  )
}
