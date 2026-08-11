import type { ReactNode } from 'react'

export function Header({ title, desc }: { title: string; desc: string }) {
  return (
    <div>
      <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
      <p className="text-sm text-slate-500 mt-1">{desc}</p>
    </div>
  )
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="bg-rose-50 border border-rose-200 text-rose-700 rounded-md px-4 py-3 text-sm">
      {message}
    </div>
  )
}

/** 頁面骨架：標題＋錯誤列＋內容。填寫與匯入的上傳畫面與結果畫面都是這個形狀。 */
export function PageShell({
  title,
  desc,
  error,
  children,
}: {
  title: string
  desc: string
  error: string
  children: ReactNode
}) {
  return (
    <div className="space-y-6">
      <Header title={title} desc={desc} />
      {error && <ErrorBox message={error} />}
      {children}
    </div>
  )
}

/** 結果畫面底部的「← 換一份檔案＋主按鈕」列。 */
export function FooterBar({
  onRestart,
  onSubmit,
  disabled,
  label,
}: {
  onRestart: () => void
  onSubmit: () => void
  disabled: boolean
  label: string
}) {
  return (
    <div className="flex items-center justify-between pb-8">
      <button onClick={onRestart} className="text-sm text-slate-500 hover:text-slate-800">
        ← 換一份檔案
      </button>
      <button
        onClick={onSubmit}
        disabled={disabled}
        className="px-6 py-2.5 rounded-md bg-sky-600 text-white font-medium
                   hover:bg-sky-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
      >
        {label}
      </button>
    </div>
  )
}

/** 「將被覆蓋」的琥珀色小標。 */
export function OverwriteBadge({ children }: { children: ReactNode }) {
  return (
    <span className="ml-2 text-xs bg-amber-100 text-amber-800 rounded px-1.5 py-0.5">
      {children}
    </span>
  )
}
