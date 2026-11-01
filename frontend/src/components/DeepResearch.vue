<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import ResearchReport from './ResearchReport.vue'
import { api, downloadDocument, type KnowledgeBase, type ResearchSummary, type ResearchTask, type ResearchFinding, type ResearchEvidence } from '../api'

const props = defineProps<{ knowledgeBases: KnowledgeBase[]; launch?: { id: string; question: string; scope: string[] } | null }>()
const emit = defineEmits<{ launched: [] }>()
const question = ref(''), scope = ref<string[]>([]), tasks = ref<ResearchSummary[]>([])
const task = ref<ResearchTask | null>(null), error = ref(''), busy = ref(true), focused = ref('')
const active = computed(() => !!task.value && ['queued', 'running'].includes(task.value.status))
const labels: Record<string, string> = { queued: '待执行', running: '执行中', completed: '已完成', failed: '失败', cancelled: '已取消', planning: '研究规划', retrieving: '资料检索', synthesizing: '综合分析', verifying: '引用核验', writing_report: '报告写作与保存' }
let timer: ReturnType<typeof setTimeout> | undefined, disposed = false, revision = 0
let attempt: { text: string; scope: string; key: string } | null = null
let retryAttempt: { id: string; key: string } | null = null
function fail(e: unknown) { error.value = e instanceof Error ? e.message : '研究请求失败' }
async function list() { tasks.value = await api<ResearchSummary[]>('/research') }
function schedule() {
  clearTimeout(timer)
  if (!disposed && active.value) timer = setTimeout(() => { if (task.value) void load(task.value.id, true) }, 1200)
}
async function load(id: string, polling = false) {
  const seq = ++revision
  if (!polling) { busy.value = true; error.value = ''; focused.value = '' }
  try {
    const result = await api<ResearchTask>(`/research/${encodeURIComponent(id)}`)
    if (disposed || seq !== revision) return
    task.value = result
    tasks.value = tasks.value.map(item => item.id === result.id ? result : item)
    schedule()
    if (!active.value) await list()
  } catch (e) { if (seq === revision) { fail(e); clearTimeout(timer) } }
  finally { if (seq === revision) busy.value = false }
}
async function start() {
  if (busy.value || active.value || question.value.trim().length < 5) return
  busy.value = true; error.value = ''; focused.value = ''
  const text = question.value.trim(), selected = JSON.stringify([...scope.value].sort())
  if (!attempt || attempt.text !== text || attempt.scope !== selected) attempt = { text, scope: selected, key: crypto.randomUUID() }
  try {
    task.value = await api<ResearchTask>('/research', { method: 'POST', body: JSON.stringify({ question: text, knowledge_base_ids: scope.value, request_id: attempt.key, generate_report: true }) })
    attempt = null
    schedule(); await list()
  } catch (e) { fail(e) }
  finally { busy.value = false }
}
async function cancel() {
  if (!task.value || busy.value) return
  busy.value = true; error.value = ''; ++revision; clearTimeout(timer)
  try { task.value = await api<ResearchTask>(`/research/${task.value.id}/cancel`, { method: 'POST' }); await list() }
  catch (e) { fail(e); schedule() }
  finally { busy.value = false }
}
async function retry() {
  if (!task.value || busy.value || active.value) return
  busy.value = true; error.value = ''; focused.value = ''
  if (!retryAttempt || retryAttempt.id !== task.value.id) retryAttempt = { id: task.value.id, key: crypto.randomUUID() }
  try {
    task.value = await api<ResearchTask>(`/research/${task.value.id}/retry`, { method: 'POST', body: JSON.stringify({ request_id: retryAttempt.key }) })
    retryAttempt = null; schedule(); await list()
  } catch (e) { fail(e) }
  finally { busy.value = false }
}
function locate(e: ResearchEvidence) {
  const l = e.locator
  return l?.line_start ? `第 ${l.line_start}–${l.line_end || l.line_start} 行` : `第 ${e.page || 1} 页`
}
function show(id: string) {
  focused.value = id
  requestAnimationFrame(() => document.getElementById(`research-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
}
function groupLabel(f: ResearchFinding) { return f.kind === 'evidence' ? '原文证据' : '模型综合判断 · 语义未自动核验' }
onMounted(async () => {
  try {
    await list()
    if (disposed) return
    if (props.launch) {
      const launch = props.launch
      question.value = launch.question; scope.value = [...launch.scope]
      attempt = { text: launch.question, scope: JSON.stringify([...launch.scope].sort()), key: launch.id }
      emit('launched'); busy.value = false; await start()
    } else if (tasks.value[0]) await load(tasks.value[0].id)
  } catch (e) { fail(e) }
  finally { busy.value = false }
})
onUnmounted(() => { disposed = true; ++revision; clearTimeout(timer) })
</script>

<template>
  <main class="page research-page">
    <div class="page-heading"><div><span class="eyebrow">DEEP RESEARCH</span><h1>深度研究<span class="heading-dot">.</span></h1><p>从一个问题到有据可查的研究报告，自动写作并保存。</p></div><span class="outline-chip">知识库研究 · 单 Agent</span></div>
    <div v-if="error" class="research-error" role="alert">{{ error }} <button class="btn secondary" @click="task ? load(task.id) : list().catch(fail)">重新读取</button></div>
    <section class="panel research-compose">
      <label for="research-question">研究问题</label>
      <textarea id="research-question" v-model="question" rows="3" maxlength="1200" :disabled="busy || active" placeholder="例如：LangGraph 与 Qdrant 分别承担什么职责？资料是否支持将两者组合？还有哪些证据缺口？"></textarea>
      <details class="research-scope"><summary>资料范围 · {{ scope.length ? `已选 ${scope.length} 个知识库` : '全部可访问知识库' }}</summary><label v-for="kb in knowledgeBases" :key="kb.id"><input v-model="scope" type="checkbox" :value="kb.id" :disabled="busy || active" />{{ kb.name }}</label><p v-if="!knowledgeBases.length">暂无知识库，可先在知识库管理中上传资料。</p></details>
      <div class="research-actions"><small>任务会自动保存。取消后，已发出的模型请求仍可能计费。</small><button class="btn primary" :disabled="busy || active || question.trim().length < 5" @click="start">{{ busy ? '处理中…' : '开始深度研究' }}</button></div>
    </section>
    <div class="research-layout">
      <aside class="panel research-history"><h2>研究记录</h2><button v-for="item in tasks" :key="item.id" :class="{ selected: task?.id === item.id }" :disabled="busy" @click="load(item.id)"><strong>{{ item.question }}</strong><small>{{ labels[item.status] || item.status }}</small></button><p v-if="!tasks.length">还没有研究任务。</p></aside>
      <section v-if="task" class="research-detail" aria-live="polite">
        <div class="panel research-status"><div><span class="eyebrow">{{ labels[task.status] }}</span><h2>{{ task.question }}</h2><p>当前阶段：{{ labels[task.phase] || task.phase }}</p></div><button v-if="active" class="btn secondary" :disabled="busy" @click="cancel">取消研究</button><button v-else class="btn secondary" :disabled="busy || task.restricted" @click="retry">按原计划重新执行</button></div>
        <p v-if="task.error" class="research-error" role="alert">{{ task.error }}</p>
        <section v-if="task.prior_error_details" class="panel research-error-detail"><h2>此前核验失败 · {{ task.prior_error_details.code }}</h2><p>{{ task.prior_error_details.message }}</p><ul><li v-for="(check, i) in task.prior_error_details.checks" :key="i">{{ check.rule }} · {{ check.path.join('.') }}</li></ul><p>这是最终错误之前的核验事件；下方最终错误单独保留，二者不视为同一错误。</p></section>
        <details v-if="task.error_details" class="panel research-error-detail"><summary>失败详情 · {{ task.error_details.code }}</summary><p>{{ task.error_details.message }}</p><p>阶段：{{ labels[task.error_details.stage] || task.error_details.stage }} · 类型：{{ task.error_details.error_type }}<br />时间：{{ new Date(task.error_details.at).toLocaleString('zh-CN') }}<br />任务：{{ task.error_details.task_id }}</p><ul v-if="task.error_details.checks.length"><li v-for="(check, i) in task.error_details.checks" :key="i">{{ check.rule }} · {{ check.path.join('.') || '整体结构' }}</li></ul><p>已保存 {{ task.error_details.context.evidence_groups }} 组证据；未通过核验的输出不会作为研究结果发布。</p></details>
        <div v-if="task.limits" class="research-budget">上限 {{ task.limits.tasks }} 个子问题 · {{ task.limits.retrievals }} 轮检索 · {{ task.limits.seconds }} 秒 · {{ task.limits.model_calls }} 次模型调用<br />模型调用尝试 {{ task.usage.model_calls || 0 }} 次<span v-if="task.call_observations?.calls_with_usage != null"> · 获得用量 {{ task.call_observations.calls_with_usage }} 次（外发次数须查传输记录）</span> · 已返回用量 输入 {{ task.usage.input_tokens || 0 }} / 输出 {{ task.usage.output_tokens || 0 }} tokens · 预留 {{ task.usage.reserved_tokens || 0 }} tokens</div>
        <ResearchReport v-if="!task.restricted" :key="task.id" :task-id="task.id" :can-generate="task.status === 'completed' && !!task.result" :auto-report-id="task.report_workflow?.report_id" :writing="task.phase === 'writing_report' && active" :workflow-error="task.report_workflow?.status === 'failed' ? task.report_workflow.message : undefined" />
        <section v-if="task.plan.objective" class="panel research-plan"><h2>研究计划</h2><p>{{ task.plan.objective }}</p><small>{{ task.plan.planner === 'rules' ? '当前未配置模型，使用规则拆分计划；结果仅整理原文。' : '结构化模型规划，已通过字段、数量与重复检查。' }}</small><ol><li v-for="step in task.steps" :key="step.id"><div><strong>{{ step.question }}</strong><span class="outline-chip">{{ labels[step.status] }}</span></div><p>{{ step.expected_output }}</p><small>{{ step.rounds.length }} 轮检索 · {{ step.evidence_ids.length }} 组证据</small><p v-if="step.gap" class="research-gap">证据缺口：{{ step.gap }}</p><details v-if="step.rounds.length"><summary>检索执行记录</summary><p v-for="(round, n) in step.rounds" :key="n">查询：{{ round.query }}<br />召回 {{ round.retrieved }} 条，接纳证据 {{ round.accepted.join('、') || '无' }}</p></details></li></ol></section>
        <section v-if="task.result" class="panel research-result"><h2>研究结果</h2><p class="research-budget">{{ task.result.provider === 'extractive' ? '本地原文整理' : task.result.provider === 'abstention' ? '证据不足' : task.result.provider }} · {{ task.result.verification_scope }}</p>
          <template v-for="(group, n) in [task.result.findings, task.result.analysis]" :key="n"><h3>{{ n === 0 ? '主要发现' : '综合分析' }}</h3><p v-if="!group.length">{{ n === 0 ? '当前没有充分证据支持结论。' : '未生成有依据的综合判断。' }}</p><article v-for="(finding, i) in group" :key="i" class="research-finding"><small>{{ groupLabel(finding) }}</small><p>{{ finding.text }}</p><button v-for="eid in finding.evidence_ids" :key="eid" class="research-ref" @click="show(eid)">{{ eid }} · 查看证据</button></article></template>
          <div v-for="(conflict, i) in task.result.conflicts" :key="i" class="research-gap">潜在来源冲突 · {{ conflict.evidence_ids.join(' / ') }}：{{ conflict.reason }}</div>
          <div v-for="gap in task.result.gaps" :key="gap.step_id" class="research-gap">{{ gap.question }}：{{ gap.reason }}</div>
          <h3>局限性</h3><ul><li v-for="line in task.result.limitations" :key="line">{{ line }}</li></ul><h3>进一步研究</h3><ul><li v-for="line in task.result.follow_up_questions" :key="line">{{ line }}</li></ul><p v-if="!task.result.follow_up_questions.length">暂无补充问题；仍需结合上述局限评估。</p>
        </section>
        <section v-if="task.evidence.length" class="panel research-evidence"><h2>检索证据 · {{ task.evidence.length }} 组</h2><p>重复片段合并在同一组；同一文档内的片段不代表独立佐证。</p><details v-for="e in task.evidence" :id="`research-${e.id}`" :key="e.id" :open="focused === e.id" :class="{ focused: focused === e.id }"><summary>{{ e.id }} · {{ e.document_name }} · {{ locate(e) }} · {{ e.step_ids.join(' / ') }}</summary><small>{{ e.locator?.section?.join(' / ') }}</small><pre>{{ e.text }}</pre><p>对应子问题：{{ e.step_ids.join('、') }} · 合并来源位置 {{ e.origins.length }} 处</p><p v-for="(origin, i) in e.origins" :key="i">{{ origin.document_name }} · {{ origin.locator?.line_start ? `第 ${origin.locator.line_start}–${origin.locator.line_end} 行` : `第 ${origin.page || 1} 页` }}</p><button class="btn secondary" @click="downloadDocument(e.document_id, e.document_name, e.revision_id).catch(fail)">查看版本原文</button></details></section>
        <details class="panel research-events"><summary>真实执行时间线（{{ task.events.length }}）</summary><ol><li v-for="(entry, i) in task.events" :key="i"><time>{{ new Date(entry.at).toLocaleTimeString('zh-CN') }}</time> {{ entry.detail }}</li></ol></details>
      </section>
      <section v-else class="panel research-empty"><h2>提出一个值得深入的问题</h2><p>系统将生成计划，逐项检索知识库并整理来源。研究记录可在页面刷新后恢复。</p></section>
    </div>
  </main>
</template>

<style scoped>
.research-page{height:calc(100dvh - 68px);overflow-y:auto}
@media(max-width:800px){.research-page{height:calc(100dvh - 59px)}}
.research-scope label{flex-direction:row;align-items:center}
.research-page{max-width:1400px;margin:0 auto;width:100%;overflow-wrap:anywhere}.research-compose{padding:24px;margin-bottom:24px}.research-compose>label{font-weight:600;display:block;margin-bottom:10px}.research-compose textarea{width:100%;resize:vertical;min-height:100px;border:1px solid #dddfee;border-radius:12px;padding:14px;line-height:1.7;background:#fafaff;font:inherit}.research-actions,.research-status,.research-plan li>div{display:flex;align-items:center;justify-content:space-between;gap:16px}.research-actions{margin-top:16px}.research-actions small,.research-budget,small{color:#646b82;line-height:1.7}.research-scope{padding-top:12px}.research-scope label{display:inline-flex;gap:6px;margin:12px 16px 0 0}.research-layout{display:grid;grid-template-columns:230px minmax(0,1fr);gap:24px;align-items:start}.research-history{padding:18px;display:grid;gap:10px}.research-history button{text-align:left;border:0;border-radius:10px;background:#f8f8fc;padding:12px;color:#343b52;cursor:pointer}.research-history button.selected{background:#eeebff;color:#6250c8}.research-history strong{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.6;font-weight:500}.research-history small{display:block;margin-top:6px}.research-detail{display:grid;gap:18px;min-width:0}.research-detail>.panel,.research-empty{padding:24px}.research-page h2{font-size:18px;margin:0 0 14px}.research-page h3{font-size:16px;margin:22px 0 12px}.research-page p{line-height:1.85;margin:8px 0}.research-status h2{margin:6px 0}.research-status .btn{flex-shrink:0}.research-plan ol{padding-left:24px}.research-plan li{padding:14px 0;border-bottom:1px solid #eeedf6}.research-page summary{cursor:pointer;line-height:1.8}.research-plan details{margin-top:10px}.research-gap{background:#fff8ed;border:1px solid #f2dfbf;color:#805921;padding:12px;border-radius:10px;margin:12px 0;line-height:1.8}.research-finding{padding:14px 0;border-bottom:1px solid #efedf7}.research-finding p{white-space:pre-wrap}.research-ref{border:0;background:#f0edff;color:#6754c9;padding:6px 10px;border-radius:8px;cursor:pointer;margin:4px 6px 0 0}.research-evidence details{border:1px solid #e7e5f1;padding:12px;border-radius:10px;margin-top:12px}.research-evidence details.focused{border-color:#9986eb;background:#fcfbff}.research-evidence pre{white-space:pre-wrap;font:inherit;line-height:1.8;background:#f7f7fb;padding:16px;border-radius:8px}.research-events ol{padding-left:20px;line-height:2}.research-events time{color:#777d92;font-size:12px;margin-right:8px}.research-error{background:#fff0f1;color:#a73c4b;padding:14px;border-radius:12px;line-height:1.8}.research-result ul{padding-left:20px;line-height:1.9}.research-budget{font-size:12px}.research-empty{line-height:1.8}@media(max-width:800px){.research-layout{grid-template-columns:minmax(0,1fr)}.research-history{max-height:230px;overflow:auto}.research-actions,.research-status{align-items:flex-start;flex-direction:column}.research-compose,.research-detail>.panel{padding:18px}.research-actions .btn{width:100%}.research-plan li>div{align-items:flex-start}.research-plan .outline-chip{flex-shrink:0}}
</style>
