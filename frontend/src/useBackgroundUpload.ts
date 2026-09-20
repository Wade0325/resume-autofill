import { useState } from 'react'
import { errorText } from './api'
import { usePolling } from './usePolling'
import type { UploadPhase } from './components/Dropzone'

// usePolling 的狀態形狀（api.ts 的 JobState / ImportState）
type Polled =
  | { status: 'processing'; stage: string; filename: string }
  | { status: 'failed'; error: string; filename: string }
  | { status: 'ready' }

/**
 * 「上傳檔案 → 後端背景分析 → 輪詢到結果」的整套狀態機，填寫與匯入共用。
 * 工作 id 存 sessionStorage，切到別頁再回來能接續，不必重傳檔案重跑模型。
 *
 * error 也給頁面其他失敗共用（欄位載入、套用），顯示位置是同一個 ErrorBox。
 * onDiscard 在「換了新檔／放棄追蹤／重來」時呼叫，頁面清自己的附帶狀態
 * （匯入頁存的勾選）。
 */
export function useBackgroundUpload<S extends Polled>(opts: {
  storageKey: string
  start: (file: File, onProgress: (pct: number) => void) => Promise<string>
  getState: (id: string) => Promise<S>
  hasResult: boolean
  onReady: (state: Extract<S, { status: 'ready' }>) => void
  onDiscard?: () => void
}) {
  const { storageKey, start, getState, hasResult, onReady, onDiscard } = opts
  const [phase, setPhase] = useState<UploadPhase>({ kind: 'idle' })
  const [error, setError] = useState('')
  const [trackingId, setTrackingId] = useState<string | null>(() =>
    sessionStorage.getItem(storageKey),
  )

  usePolling(trackingId, hasResult, getState, {
    ready: (st) => {
      onReady(st)
      setPhase({ kind: 'idle' })
    },
    failed: setError,
    progress: (stage, startedAt) => setPhase({ kind: 'analyzing', startedAt, stage }),
    giveUp: () => {
      sessionStorage.removeItem(storageKey)
      onDiscard?.()
      setTrackingId(null)
      setPhase({ kind: 'idle' })
    },
  })

  async function upload(file: File) {
    setError('')
    setPhase({ kind: 'uploading', percent: 0 })
    try {
      const id = await start(file, (percent) => setPhase({ kind: 'uploading', percent }))
      sessionStorage.setItem(storageKey, id)
      onDiscard?.() // 新的一次上傳，舊的附帶狀態不適用
      setPhase({ kind: 'analyzing', startedAt: Date.now(), stage: '準備中' })
      setTrackingId(id)
    } catch (e) {
      setError(errorText(e))
      setPhase({ kind: 'idle' })
    }
  }

  /** 接手一個既有的工作（填寫紀錄點「開啟」）：還在分析中的也接得起來。 */
  function track(id: string) {
    setError('')
    sessionStorage.setItem(storageKey, id)
    onDiscard?.()
    setTrackingId(id)
  }

  /** 回到上傳畫面重來。頁面自己負責清結果 state（setPlan(null) 那類）。 */
  function reset() {
    sessionStorage.removeItem(storageKey)
    onDiscard?.()
    setTrackingId(null)
    setPhase({ kind: 'idle' })
  }

  return { phase, error, setError, upload, track, reset }
}
