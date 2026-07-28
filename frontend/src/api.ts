// 所有後端呼叫與對應型別。型別只服務於這些契約，所以跟呼叫放在一起。

export type FieldSpec = {
  key: string
  label: string
  kind: string // text | date | choice | longtext | list
  choices: string[]
  aliases: string[]
  sensitive: boolean
}

export type Health = {
  api: string
  db: boolean
  llm: { available: boolean; backend: string; host: string; model: string }
}

export type PlanItem = {
  anchor_id: string
  label: string
  kind: string
  options: string[]
  field_key: string
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
  stats: { anchors: number; fill: number; skip: number; by_source: Record<string, number> }
  items: PlanItem[]
}

export type ImportRow = {
  anchor_id: string
  label: string
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

function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  return request<T>(path, { method: 'POST', body: form })
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

  analyze: (file: File) => upload<Plan>('/jobs', file),
  fixMappings: (jobId: string, fixes: { anchor_id: string; field_key: string }[]) =>
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

  analyzeImport: (file: File) => upload<ImportPreview>('/imports', file),
  applyImport: (importId: string, anchorIds: string[]) =>
    postJson<{ applied: number }>(`/imports/${importId}/apply`, { anchor_ids: anchorIds }),
}
