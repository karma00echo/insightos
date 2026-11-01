<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api, downloadDocument, downloadReport, type ReportSummary, type ResearchReport } from '../api'

const props = defineProps<{ taskId: string; canGenerate: boolean; autoReportId?: string; writing?: boolean; workflowError?: string }>()
const versions = ref<ReportSummary[]>([]), report = ref<ResearchReport | null>(null)
const busy = ref(false), error = ref(''), focused = ref(''), expanded = ref(false)
let generation = 0, disposed = false, attempt: { task: string; key: string } | null = null
const claims = computed(() => new Map(report.value?.content.claims.map(c => [c.id, c]) || []))
const labels: Record<string, string> = { VALID: '引用有效', INVALID: '引用失效', NEEDS_REVIEW: '待人工复核', NO_CITATIONS: '无引用 · 证据不足', TEXT_ATTRIBUTED: '仅验证原文归属', INSUFFICIENT_EVIDENCE: '证据不足', POTENTIAL_CONFLICT: '潜在来源冲突' }
function fail(e: unknown) { error.value = e instanceof Error ? e.message : '报告请求失败' }
async function load(id: string) {
  const seq = ++generation
  busy.value = true; error.value = ''; focused.value = ''
  try {
    const value = await api<ResearchReport>(`/research-reports/${encodeURIComponent(id)}`)
    if (!disposed && seq === generation) report.value = value
  } catch (e) { if (seq === generation) fail(e) }
  finally { if (seq === generation) busy.value = false }
}
async function refresh() {
  const seq = ++generation, task = props.taskId
  busy.value = true; error.value = ''; report.value = null; versions.value = []; focused.value = ''; expanded.value = false
  try {
    const items = await api<ReportSummary[]>(`/research-reports?task_id=${encodeURIComponent(task)}`)
    if (disposed || seq !== generation) return
    versions.value = items
    if (items[0]) {
      await load(items[0].id)
      if (!disposed && task === props.taskId && seq + 1 === generation && props.autoReportId) expanded.value = true
    }
  } catch (e) { if (seq === generation) fail(e) }
  finally { if (seq === generation) busy.value = false }
}
async function create() {
  if (busy.value || !props.canGenerate) return
  const seq = ++generation, task = props.taskId
  busy.value = true; error.value = ''
  if (!attempt || attempt.task !== task) attempt = { task, key: crypto.randomUUID() }
  try {
    const value = await api<ResearchReport>('/research-reports', { method: 'POST', body: JSON.stringify({ task_id: task, request_id: attempt.key }) })
    if (disposed || seq !== generation) return
    report.value = value; focused.value = ''; expanded.value = true; attempt = null
    versions.value = [value, ...versions.value.filter(r => r.id !== value.id)]
  } catch (e) { if (seq === generation) fail(e) }
  finally { if (seq === generation) busy.value = false }
}
function show(eid: string) {
  focused.value = eid
  requestAnimationFrame(() => document.getElementById(`report-evidence-${eid}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
}
async function exportFile() {
  if (!report.value || busy.value) return
  busy.value = true; error.value = ''
  try { await downloadReport(report.value.id, report.value.version) } catch (e) { fail(e) }
  finally { busy.value = false }
}
watch(() => [props.taskId, props.autoReportId], () => { void refresh() }, { immediate: true })
onUnmounted(() => { disposed = true; ++generation })
</script>

<template>
  <section class="panel report-panel" aria-label="研究报告">
    <div class="report-toolbar"><div><span class="eyebrow">RESEARCH REPORT</span><h2>研究报告</h2><p>研究完成后自动写作并保存，引用和待复核状态随报告保留。</p></div><button v-if="canGenerate" class="btn secondary" :disabled="busy" @click="create">{{ busy ? '处理中…' : '另存原文整理版本' }}</button></div>
    <p v-if="writing" role="status">正在基于研究证据写作、核验并保存报告，可继续在下方查看已有研究证据。</p>
    <p v-else-if="!canGenerate">完成研究后，报告将在这里自动显示。</p>
    <p v-if="workflowError" class="report-warning" role="alert">{{ workflowError }} 可查看下方已保存的研究结果，或按原计划重新执行。原文整理版本不代表AI写作成功。</p>
    <p v-if="error" class="report-warning" role="alert">{{ error }} <button class="btn secondary" :disabled="busy" @click="refresh">重新读取报告</button></p>
    <div v-if="versions.length" class="report-toolbar report-controls"><label>报告版本 <select aria-label="报告版本" :value="report?.id" :disabled="busy" @change="load(($event.target as HTMLSelectElement).value)"><option v-for="v in versions" :key="v.id" :value="v.id">v{{ v.version }} · {{ new Date(v.created_at).toLocaleString('zh-CN') }}</option></select></label><button class="btn secondary" :disabled="busy || !report" @click="expanded = !expanded">{{ expanded ? '收起报告' : '阅读报告' }}</button><button class="btn secondary" :disabled="busy || !report" @click="exportFile">导出 Markdown</button></div>
    <article v-if="report && expanded" class="report-reader">
      <h2>{{ report.title }}</h2><p class="report-meta">报告 {{ report.id }} · v{{ report.version }}<br />研究任务 {{ report.task_id }}<br />生成时间 {{ new Date(report.created_at).toLocaleString('zh-CN') }} · {{ report.content.generation.method === 'llm_evidence_writing' ? 'AI证据报告写作' : '原文确定性整理' }} · 报告模型调用 {{ report.content.generation.model_calls }} 次<span v-if="report.content.generation.model"> · {{ report.content.generation.model }}</span><br v-if="report.content.generation.reason" />{{ report.content.generation.reason }}</p>
      <div class="report-warning"><strong>研究质量待人工复核</strong><p>{{ report.current_validation.notice }}</p><p>保存时：{{ labels[report.content.validation.status] }} {{ report.content.validation.valid }}/{{ report.content.validation.total }}<br />当前检查：{{ labels[report.current_validation.status] }} {{ report.current_validation.valid }}/{{ report.current_validation.total }}</p></div>
      <div v-for="v in report.content.evaluations" :key="v.id" class="report-meta">既有评测 {{ v.id }}：{{ v.engineering }} / {{ v.quality }}。{{ v.note }}</div>
      <nav class="report-outline" aria-label="报告目录"><a v-for="s in report.content.sections" :key="s.id" :href="`#report-section-${s.id}`">{{ s.title }}</a></nav>
      <section v-for="s in report.content.sections" :id="`report-section-${s.id}`" :key="s.id" class="report-section"><h3>{{ s.title }}</h3><p v-for="(p, i) in s.paragraphs" :key="i">{{ p }}</p>
        <article v-for="cid in s.claim_ids" :key="cid" class="report-claim"><template v-if="claims.get(cid)"><small>{{ cid }} · {{ labels[claims.get(cid)!.quality_status] }} · {{ claims.get(cid)!.source_pointer }}</small><p>{{ claims.get(cid)!.text }}</p><button v-for="eid in claims.get(cid)!.evidence_ids" :key="eid" class="report-ref" @click="show(eid)">{{ eid }} · 查看证据</button></template></article>
        <template v-if="s.id === 'references'"><p v-if="!report.content.evidence.length">没有实际引用的可用证据。</p><details v-for="e in report.content.evidence" :id="`report-evidence-${e.id}`" :key="e.id" :open="focused === e.id"><summary>{{ e.id }} · {{ e.document_name }} · {{ e.locator?.line_start ? `第 ${e.locator.line_start}–${e.locator.line_end} 行` : `第 ${e.page} 页` }}</summary><p class="report-meta">文档 {{ e.document_id }}<br />版本 {{ e.revision_id }} · 子问题 {{ e.step_ids.join(' / ') }}</p><pre>{{ e.text }}</pre><p v-for="(o, i) in e.origins" :key="i" class="report-meta">合并来源 {{ o.document_name }} · {{ o.locator?.line_start ? `${o.locator.line_start}–${o.locator.line_end} 行` : `第 ${o.page} 页` }}</p><button class="btn secondary" @click="downloadDocument(e.document_id, e.document_name, e.revision_id).catch(fail)">查看引用版本原文</button></details>
          <h4>当前逐结论引用检查</h4><p v-for="(check, i) in report.current_validation.checks" :key="i" class="report-meta">{{ check.claim_id }} → {{ check.evidence_id || '无证据' }}：{{ labels[check.status] }}<span v-for="reason in check.reasons" :key="reason"> · {{ reason }}</span><span v-for="origin in check.origins?.filter(o => o.status !== 'VALID')" :key="origin.chunk_id"> · {{ origin.reasons.join('；') }}</span></p>
        </template>
      </section>
      <p class="report-meta">内容摘要 SHA256：{{ report.content_sha256 }}<br />导出使用已保存版本；PDF 暂未提供。</p>
    </article>
  </section>
</template>

<style scoped>
.report-panel{padding:24px;min-width:0}.report-toolbar{display:flex;gap:14px;align-items:center;justify-content:space-between;flex-wrap:wrap}.report-toolbar h2{margin:6px 0;font-size:18px}.report-toolbar p,.report-meta{color:#646b82;font-size:12px;line-height:1.8}.report-controls{justify-content:flex-start;margin-top:18px}.report-controls label{display:flex;align-items:center;gap:8px}.report-controls select{font:inherit;border:1px solid #ded9ef;padding:8px;border-radius:8px;max-width:300px;background:#faf9ff;color:#47415d}.report-reader{border-top:1px solid #e8e4f2;margin-top:22px;padding-top:22px;overflow-wrap:anywhere}.report-reader h2{font-size:21px;line-height:1.65}.report-warning{background:#fff8ec;border:1px solid #edddbd;border-radius:10px;padding:14px;color:#805921;margin:14px 0;font-size:13px;line-height:1.8}.report-outline{display:flex;flex-wrap:wrap;gap:8px 16px;padding:16px;background:#f7f5ff;border-radius:10px;margin:18px 0}.report-outline a{color:#6c50c7;font-size:13px}.report-section{scroll-margin-top:20px;padding:10px 0}.report-section h3{font-size:17px;margin:12px 0}.report-section p{white-space:pre-wrap;line-height:1.9}.report-claim{padding:14px;border-left:3px solid #b6a4ee;background:#faf9fd;margin:12px 0}.report-claim small{color:#75658d;line-height:1.8}.report-ref{background:#eee9ff;border:0;color:#6448b8;border-radius:6px;padding:6px 10px;margin:4px;cursor:pointer}.report-section details{padding:14px;border:1px solid #e3ddf0;border-radius:10px;margin:12px 0}.report-section summary{cursor:pointer;line-height:1.8}.report-section pre{white-space:pre-wrap;font:inherit;background:#f7f6fc;line-height:1.9;padding:16px;border-radius:8px}@media(max-width:800px){.report-panel{padding:18px}.report-controls label{align-items:flex-start;flex-direction:column}.report-controls select{max-width:100%;width:100%}.report-reader h2{font-size:18px}}
</style>
