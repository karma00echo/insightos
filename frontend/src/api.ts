export type User = { id: string; email: string; name: string; is_platform_admin?: boolean }
export type KnowledgeBase = { id: string; name: string; description: string; created_at: string; document_count: number; organization_id?: string; visibility?: 'organization' | 'restricted'; role: 'admin' | 'editor' | 'reader' }
export type Document = { id: string; knowledge_base_id: string; name: string; size_bytes: number; status: string; chunk_count: number; error?: string; created_at: string; job_id?: string; active_revision_id?: string; source?: string; expires_at?: string }
export type Job = { id: string; status: string; progress: number; error?: string; document_id: string; stage?: string; processed_pages?: number; total_pages?: number }
export type Locator = { section?: string[]; line_start?: number; line_end?: number; warnings?: string[] }
export type Chunk = { id: string; text: string; page: number | null; ordinal: number; locator?: Locator }
export type Citation = { chunk_id: string; document_id: string; document_name: string; knowledge_base_id: string; page: number | null; text: string; score: number; revision_id?: string; source?: string; locator?: Locator }
export type Trace = { step: string; detail: unknown }
export type Message = { id: string; role: string; content: string; citations?: Citation[]; trace?: Trace[]; created_at?: string; pending?: boolean }
export type Conversation = { id: string; title: string; created_at: string }
export type EvaluationCase = { question: string; reference: string; expected_document_ids?: string[]; message_id?: string }
export type Evaluation = { id: string; name: string; status: string; metrics: Record<string, unknown>; results: Record<string, unknown>[]; created_at: string; error?: string }
export type Comparison = { dense: Record<string, unknown>; hybrid: Record<string, unknown>; relative_hit_rate_change: number | null }
export type Runtime = { mode: string; embedding_provider: string; embedding_model?: string; llm_provider: string; llm_backend?: string; llm_model?: string; reranker: unknown; vector_store: unknown; queue: unknown; max_upload_mb: number; features: Record<string, unknown> }
export type ChatResponse = { answer: string; citations: Citation[]; trace: Trace[]; cached: boolean; conversation_id: string; message_id: string; provider: string; usage: Record<string, unknown>; verification?: { mode: string; passed: boolean; semantic_checked?: boolean }; abstained?: boolean }
export type ResearchSummary = { id: string; question: string; status: string; phase: string; error?: string; created_at: string; updated_at: string }
export type ResearchEvidence = Citation & { id: string; step_ids: string[]; origins: Partial<Citation>[] }
export type ResearchFinding = { kind: 'evidence' | 'synthesis'; text: string; evidence_ids: string[]; step_ids: string[] }
export type ResearchDiagnostic = { code: string; category: string; message: string; stage: string; error_type: string; task_id: string; at: string; checks: { rule: string; path: (string | number)[] }[]; context: { steps: number; evidence_groups: number; model_calls: number; reserved_tokens: number } }
export type ResearchTask = ResearchSummary & {
  prior_error_details?: ResearchDiagnostic | null
  call_observations?: { attempts: number; responses_received: number | null; calls_with_usage: number | null; network_send_count: null; note: string } | null
  report_workflow?: { status: 'saved' | 'failed'; report_id?: string; version?: number; method?: string; message?: string } | null
  error_details?: ResearchDiagnostic | null
  knowledge_base_ids: string[]; restricted: boolean
  plan: { objective?: string; planner?: string }
  steps: { id: string; question: string; expected_output: string; status: string; evidence_ids: string[]; gap?: string; rounds: { query: string; retrieved: number; accepted: string[]; at: string }[] }[]
  evidence: ResearchEvidence[]; events: { at: string; phase: string; detail: string; diagnostic?: ResearchDiagnostic }[]
  usage: { model_calls?: number; input_tokens?: number; output_tokens?: number; reserved_tokens?: number }
  limits: { tasks: number; retrievals: number; seconds: number; model_calls: number; reserved_tokens: number }
  result?: { question: string; provider: string; findings: ResearchFinding[]; analysis: ResearchFinding[]; limitations: string[]; follow_up_questions: string[]; verification_scope: string; gaps: { step_id: string; question: string; reason: string }[]; conflicts: { evidence_ids: string[]; reason: string }[] }
}

export const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')
export type ReportValidation = { status: string; valid: number; total: number; semantic_checked: false; notice: string; checks: { claim_id: string; evidence_id: string | null; status: string; reasons: string[]; origins?: { chunk_id: string; status: string; reasons: string[] }[] }[] }
export type ReportSummary = { id: string; task_id: string; title: string; version: number; created_at: string; updated_at: string; quality_status: string; citation_status: string }
export type ResearchReport = ReportSummary & { content_sha256: string; current_validation: ReportValidation; content: {
  task_id: string; task_sha256: string; quality_status: string; generation: { method: string; model_calls: number; model?: string; input_tokens?: number; output_tokens?: number; reason?: string }
  sections: { id: string; title: string; paragraphs: string[]; claim_ids: string[] }[]
  claims: { id: string; source_pointer: string; kind: string; text: string; evidence_ids: string[]; step_ids: string[]; quality_status: string }[]
  evidence: ResearchEvidence[]; validation: ReportValidation; evaluations: { id: string; engineering?: string; quality?: string; note: string }[]
} }

export async function downloadReport(id: string, version: number) {
  const response = await fetch(`${API_BASE}/research-reports/${encodeURIComponent(id)}/export`, { credentials: 'same-origin' })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new ApiError(body.detail || `报告导出失败（${response.status}）`, response.status)
  }
  const url = URL.createObjectURL(await response.blob()), anchor = document.createElement('a')
  anchor.href = url; anchor.download = `research-report-${id}-v${version}.md`; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
export class ApiError extends Error {
  constructor(message: string, public status: number, public retryAfter?: number) { super(message); this.name = 'ApiError' }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('X-RAG-Request', '1')
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  let response: Response
  try { response = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: 'same-origin' }) }
  catch { throw new ApiError('无法连接服务，请确认后端已启动并检查 API 地址。', 0) }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const error = await response.json()
      if (typeof error.detail === 'string') message = error.detail
      else if (Array.isArray(error.detail)) message = error.detail.map((item: { msg: string; loc?: string[] }) => `${item.loc?.slice(1).join('.') || '参数'}: ${item.msg}`).join('；')
    } catch { /* A non-JSON server response still has a useful HTTP status. */ }
    const retry = response.headers.get('Retry-After')
    const seconds = retry ? Number(retry) || Math.max(0, (Date.parse(retry) - Date.now()) / 1000) : undefined
    throw new ApiError(message, response.status, seconds)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export async function downloadDocument(documentId: string, filename: string, revisionId?: string) {
  const path = revisionId ? `/documents/${encodeURIComponent(documentId)}/versions/${encodeURIComponent(revisionId)}/download` : `/documents/${encodeURIComponent(documentId)}/download`
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'same-origin',
  })
  if (!response.ok) throw new ApiError(`原文下载失败（${response.status}）`, response.status)
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export async function streamChat(body: Record<string, unknown>, signal: AbortSignal,
  onDraft: (text: string) => void): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE}/chat/stream`, { method: 'POST', credentials: 'same-origin', signal,
    headers: { 'Content-Type': 'application/json', 'X-RAG-Request': '1' }, body: JSON.stringify(body) })
  if (!response.ok || !response.body) throw new ApiError(`问答请求失败（${response.status}）`, response.status)
  const reader = response.body.getReader(), decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r/g, '')
      let end: number
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2)
        const kind = frame.split('\n').find(line => line.startsWith('event:'))?.slice(6).trim()
        const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n')
        if (!data) continue
        const payload = JSON.parse(data)
        if (kind === 'draft') onDraft(payload.text)
        if (kind === 'done') return payload as ChatResponse
        if (kind === 'error') throw new ApiError(payload.detail || '生成失败', payload.status || 503)
      }
      if (buffer.length > 2 * 1024 * 1024) throw new ApiError('响应超出大小限制', 502)
    }
    throw new ApiError('连接提前结束，可重试同一请求以恢复结果。', 502)
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
}
