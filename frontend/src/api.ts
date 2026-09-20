// 所有後端呼叫與對應型別。型別只服務於這些契約，所以跟呼叫放在一起。

export type FieldSpec = {
  key: string
  label: string
  kind: string // text | date | money | choice | longtext | list
  choices: string[]
  derived: boolean
  per_job: boolean // 每份工作自己一個值，在填寫頁的「這次應徵」面板填
}

export type PlanItem = {
  slot_id: string
  label: string
  kind: string
  field_key: string
  value: string
  existing: string
  source: string
  status: 'fill' | 'skip'
  note: string
  ordinal: number // 清單欄位（學歷、經歷…）用第幾筆，從 0 起算
}

export type Plan = {
  job_id: string
  filename: string
  template_cached: boolean
  llm_available: boolean
  stats: { slots: number; fill: number; skip: number; by_source: Record<string, number> }
  form_fields: string[] // 模型看過版面後認出這份表格要填的欄位
  items: PlanItem[]
  entries: Record<string, number> // 我的資料裡每一種清單有幾筆（education: 3）
  apply: Record<string, string> // 這份工作的「這次應徵」：應徵職務、工作地點…
}

export type JobState =
  | { status: 'processing'; stage: string; filename: string }
  | { status: 'failed'; error: string; filename: string }
  | { status: 'ready'; plan: Plan }

// 兩條填寫路線：classic 看文字、vlm 看版面。running 是模型開著沒；
// vision 是這台機器的模型看不看得到圖（沒開時是開了之後看不看得到）
export type EngineOut = { engine: string; engines: string[]; running: boolean; vision: boolean }

export type ImportRow = {
  row_id: string
  field_key: string
  ordinal: number
  current: string
  incoming: string
  default_checked: boolean
  entry: '' | 'merge' | 'new' // 學經歷這種多筆資料：補進名稱對得上的那一筆，或新增一筆
  entry_name: string // 對上的那一筆的名稱（學校、公司…）
}

export type ImportPreview = {
  import_id: string
  filename: string
  rows: ImportRow[]
}

export type ImportState =
  | { status: 'processing'; stage: string; filename: string }
  | { status: 'failed'; error: string; filename: string }
  | { status: 'ready'; preview: ImportPreview }

// 填寫紀錄的一列：保留期內都還能重新下載
export type JobHistory = {
  job_id: string
  filename: string
  status: 'processing' | 'analyzed' | 'failed'
  engine: string
  error: string
  created_at: string
  downloadable: boolean
}

export type LogEntry = {
  time: string
  level: string
  module: string
  message: string
}

export type ModelInfo = {
  name: string
  size_gb: number
  note: string
  downloaded: boolean
  downloadable: boolean
  active: boolean
  vision: boolean // 視覺投影檔已就位，啟動時會掛上
  vision_downloadable: boolean // 主檔在但視覺檔還沒抓，可補下載
  downloading: boolean
  progress: number
  error: string
}

export type ModelsOut = {
  active: string
  running: boolean
  starting: string | null
  vision: boolean // 執行中的引擎目前吃不吃圖片
  device: string // 跑在 GPU 還是 CPU（空＝不知道，例如引擎是別的程式開的）
  models: ModelInfo[]
}

export type Profile = Record<string, any>

// 我的資料被換掉之前留的版本。reason 是被什麼換掉：save | import | restore | file
export type ProfileVersion = {
  id: number
  reason: string
  created_at: string
  changed: number // 跟現在相比有幾個欄位不一樣
}

/** 後端錯誤一律帶 X-Request-Id，附在訊息裡才對得到 log。 */
class ApiError extends Error {
  requestId: string
  constructor(message: string, requestId: string) {
    super(message)
    this.requestId = requestId
  }
}

async function ensureOk(res: Response): Promise<void> {
  if (res.ok) return
  const requestId = res.headers.get('X-Request-Id') ?? ''
  let detail = `HTTP ${res.status}`
  try {
    const body = await res.json()
    if (body.detail) detail = body.detail
  } catch {
    // 回應不是 JSON（例如 proxy 掛了），沿用狀態碼當訊息
  }
  throw new ApiError(detail, requestId)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init)
  await ensureOk(res)
  return res.json() as Promise<T>
}

/** 二進位資源（docx / pdf 預覽）。錯誤處理與 request 相同。 */
export async function fetchBlob(path: string): Promise<Blob> {
  const res = await fetch(`/api${path}`)
  await ensureOk(res)
  return res.blob()
}

/** 顯示給使用者的錯誤文字：後端錯誤附追蹤碼，回報問題時對得到 log。 */
export function errorText(e: any): string {
  return e?.requestId ? `${e.message}（追蹤碼 ${e.requestId}）` : e?.message ?? String(e)
}

/**
 * 用 XHR 而非 fetch：fetch 拿不到上傳進度。
 * 注意進度只涵蓋「檔案送到伺服器」，送達後的解析與模型判斷沒有進度可回報。
 */
function upload<T>(path: string, file: File, onProgress?: (pct: number) => void): Promise<T> {
  const form = new FormData()
  form.append('file', file)

  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api${path}`)

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      const requestId = xhr.getResponseHeader('X-Request-Id') ?? ''
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as T)
        return
      }
      let detail = `HTTP ${xhr.status}`
      try {
        detail = JSON.parse(xhr.responseText).detail ?? detail
      } catch {
        // 回應不是 JSON，沿用狀態碼當訊息
      }
      reject(new ApiError(detail, requestId))
    }
    xhr.onerror = () => reject(new ApiError('連線失敗，請確認服務是否還在執行', ''))
    xhr.send(form)
  })
}

function putJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export const api = {
  fields: () => request<FieldSpec[]>('/fields'),

  getEngine: () => request<EngineOut>('/engine'),
  setEngine: (engine: string) => postJson<{ engine: string }>('/engine', { engine }),

  getProfile: () => request<Profile>('/profile'),
  saveProfile: (profile: Profile) => putJson<{ ok: boolean }>('/profile', profile),
  profileVersions: () => request<ProfileVersion[]>('/profile/versions'),
  restoreVersion: (id: number) => postJson<Profile>(`/profile/versions/${id}/restore`, {}),
  restoreProfileFile: (data: unknown) => postJson<Profile>('/profile/restore', data),
  exportProfileUrl: '/api/profile/export',

  analyze: (file: File, onProgress?: (pct: number) => void) =>
    upload<{ job_id: string; status: string; filename: string }>('/jobs', file, onProgress),
  getJob: (jobId: string) => request<JobState>(`/jobs/${jobId}`),
  jobHistory: () => request<JobHistory[]>('/jobs'),
  fixMappings: (
    jobId: string,
    fixes: { slot_id: string; field_key: string; ordinal?: number }[],
  ) =>
    request<Plan>(`/jobs/${jobId}/mappings`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fixes }),
    }),
  setApply: (jobId: string, values: Record<string, string>) =>
    request<Plan>(`/jobs/${jobId}/apply`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values }),
    }),
  makeOutput: (jobId: string) =>
    postJson<{ written: number; failed: number }>(`/jobs/${jobId}/output`, {}),
  downloadUrl: (jobId: string) => `/api/jobs/${jobId}/output`,

  analyzeImport: (file: File, onProgress?: (pct: number) => void) =>
    upload<{ import_id: string; status: string; filename: string }>(
      '/imports', file, onProgress),
  getImport: (importId: string) => request<ImportState>(`/imports/${importId}`),
  applyImport: (importId: string, rowIds: string[]) =>
    postJson<{ applied: number; changed: string[] }>(`/imports/${importId}/apply`, {
      row_ids: rowIds,
    }),

  logs: ({ level }: { level?: string }) =>
    request<LogEntry[]>(`/logs${level ? `?level=${level}` : ''}`),
  clearLogs: () => request<{ ok: boolean }>('/logs', { method: 'DELETE' }),

  models: () => request<ModelsOut>('/models'),
  selectModel: (name: string) => postJson<{ ok: boolean }>('/models/select', { name }),
  downloadModel: (name: string) => postJson<{ ok: boolean }>('/models/download', { name }),
  deleteModel: (name: string) =>
    request<{ ok: boolean }>(`/models/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  downloadModelUrl: (url: string) =>
    postJson<{ ok: boolean; name: string }>('/models/download-url', { url }),
}
