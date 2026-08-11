import { useEffect } from 'react'

// 填寫與匯入的背景工作共用的狀態形狀（api.ts 的 JobState / ImportState）
type Polled =
  | { status: 'processing'; stage: string; filename: string }
  | { status: 'failed'; error: string; filename: string }
  | { status: 'ready' }

/**
 * 追蹤後端背景工作：每 2 秒問一次狀態，直到 ready 或 failed。
 * 後端 --reload 重啟會斷線幾秒，連續失敗 5 次才放棄（giveUp）。
 * paused 為 true（呼叫端已拿到結果）時不輪詢。
 */
export function usePolling<S extends Polled>(
  id: string | null,
  paused: boolean,
  getState: (id: string) => Promise<S>,
  on: {
    ready: (state: Extract<S, { status: 'ready' }>) => void
    failed: (error: string) => void
    progress: (stage: string, startedAt: number) => void
    giveUp: () => void
  },
) {
  useEffect(() => {
    if (!id || paused) return
    let stopped = false
    let fails = 0
    const started = Date.now()
    const poll = () => {
      getState(id)
        .then((st) => {
          if (stopped) return
          fails = 0
          if (st.status === 'ready') {
            on.ready(st as Extract<S, { status: 'ready' }>)
          } else if (st.status === 'failed') {
            on.failed(st.error)
            on.giveUp()
          } else {
            on.progress(st.stage, started)
          }
        })
        .catch(() => {
          if (!stopped && ++fails >= 5) on.giveUp()
        })
    }
    poll()
    const timer = setInterval(poll, 2000)
    return () => {
      stopped = true
      clearInterval(timer)
    }
    // on 是每次 render 新建的 callback 集，不進依賴；輪詢生命週期只跟著 id 與 paused
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, paused])
}
