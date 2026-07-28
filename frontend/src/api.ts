// 所有後端呼叫與對應型別。型別只服務於這些契約，所以跟呼叫放在一起。

export type FieldSpec = {
  key: string
  label: string
  kind: string // text | date | money | choice | longtext | list
  choices: string[]
  sensitive: boolean
  derived: boolean
}

export type Health = {
  api: string
  db: boolean
  llm: { available: boolean; backend: string; host: string; model: string }
}

export type PlanItem = {
  slot_id: string
  label: string
  kind: string
  options: string[]
  field_key: string
  ordinal: number
  value: string
  existing: string
  confidence: number
  source: string
  status: 'fill' | 'skip'
  note: string
}

export type Plan = {
  job_id: string
  filename: string
  fingerprint: string
  template_cached: boolean
  llm_available: boolean
  stats: { slots: number; fill: number; skip: number; by_source: Record<string, number> }
  items: PlanItem[]
}

export type ImportRow = {
  row_id: string
  field_key: string
  ordinal: number
  current: string
  incoming: string
  default_checked: boolean
}

export type ImportPreview = {
  import_id: string
  filename: string
  rows: ImportRow[]
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
  downloading: boolean
  progress: number
  error: string
}

export type ModelsOut = {
  active: string
  running: boolean
  starting: string | null
  models: ModelInfo[]
}

export type Profile = Record<string, any>

/** 後端錯誤一律帶 X-Request-Id，附在訊息裡才對得到 log。 */
export class ApiError extends Error {
  requestId: string
  constructor(message: string, requestId: string) {
    super(message)
    this.requestId = requestId
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init)
  const requestId = res.headers.get('X-Request-Id') ?? ''
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body.detail) detail = body.detail
    } catch {
      // 回應不是 JSON（例如 proxy 掛了），沿用狀態碼當訊息
    }
    throw new ApiError(detail, requestId)
  }
  return res.json() as Promise<T>
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
  health: () => request<Health>('/health'),
  fields: () => request<FieldSpec[]>('/fields'),

  getProfile: () => request<Profile>('/profile'),
  saveProfile: (profile: Profile) => putJson<{ ok: boolean }>('/profile', profile),

  analyze: (file: File, onProgress?: (pct: number) => void) =>
    upload<Plan>('/jobs', file, onProgress),
  getJob: (jobId: string) => request<Plan>(`/jobs/${jobId}`),
  fixMappings: (jobId: string, fixes: { slot_id: string; field_key: string }[]) =>
    request<Plan>(`/jobs/${jobId}/mappings`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fixes }),
    }),
  makeOutput: (jobId: string) =>
    postJson<{ written: number; failed: number; download_url: string }>(
      `/jobs/${jobId}/output`,
      {},
    ),
  downloadUrl: (jobId: string) => `/api/jobs/${jobId}/output`,

  analyzeImport: (file: File, onProgress?: (pct: number) => void) =>
    upload<ImportPreview>('/imports', file, onProgress),
  getImport: (importId: string) => request<ImportPreview>(`/imports/${importId}`),
  applyImport: (importId: string, rowIds: string[]) =>
    postJson<{ applied: number }>(`/imports/${importId}/apply`, { row_ids: rowIds }),

  logs: ({ level }: { level?: string }) =>
    request<LogEntry[]>(`/logs${level ? `?level=${level}` : ''}`),
  clearLogs: () => request<{ ok: boolean }>('/logs', { method: 'DELETE' }),

  models: () => request<ModelsOut>('/models'),
  selectModel: (name: string) => postJson<{ ok: boolean }>('/models/select', { name }),
  downloadModel: (name: string) => postJson<{ ok: boolean }>('/models/download', { name }),
}
