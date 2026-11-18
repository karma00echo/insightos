<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, ApiError } from '../api'
const props = defineProps<{ decisionId: string; version: number; changeId: string }>()
const emit = defineEmits<{ unauthorized: []; revise: []; invalidated: [] }>()
type Quote = { evidence_id: string; start: number; end: number; text: string }
type Review = { analysis_id: string; sequence: number; status: string; actor_id: string; created_at: string }
type Result = { status: string; reason?: string; findings: { decision: Quote; changes: Quote[]; explanation: string }[]; uncertainties: string[]; questions: string[]; review: Review | null; generated_at?: string; model?: { provider: string; model: string }; input?: { old: unknown; new: unknown; scope: string; evidence: Record<string, { locator?: unknown; source?: unknown; side?: string }> } }
const result = ref<Result | null>(null), events = ref<Review[]>([]), busy = ref(false), error = ref(''), notice = ref('')
const pending = ref<{ path: string; body: string; status: string } | null>(null)
const currentReview = ref<Review | null>(null)
let alive = true, generation = 0, controller: AbortController | null = null
const labels: Record<string, string> = { potential_impact: '可能影响原决定 · 待人工核查', no_clear_impact: '本次证据范围内未发现明确影响', insufficient_evidence: '证据不足，无法判断', pending: '尚未复核', viewed: '用户已查看', needs_review: '用户标记需要复核', keep: '用户选择暂不修改', revise: '用户选择进入修订流程' }
const target = () => ({ decision_id: props.decisionId, decision_version: props.version, change_id: props.changeId })
function clear() { result.value = null; events.value = []; pending.value = null; currentReview.value = null }
function cancel() { generation++; controller?.abort(); clear(); busy.value = false; notice.value = '已清理临时分析。已送达的复核操作可能已记录，请刷新记录核对。' }
async function run(kind: 'history' | 'analyze' | 'review') {
  if (busy.value || (kind === 'review' && !pending.value)) return
  const request = kind === 'review' ? pending.value! : { path: `/decision-impact/${kind}`, body: JSON.stringify(target()), status: '' }
  const turn = ++generation; controller?.abort(); const local = new AbortController(); controller = local
  busy.value = true; error.value = ''; notice.value = ''
  // Clear derived text on every fresh read or mutation; never restore it after failure.
  result.value = null; events.value = []; currentReview.value = null
  const timer = setTimeout(() => local.abort(), 50_000)
  try {
    const value = await api<Result | Review | { items: Review[] }>(request.path, { method: 'POST', body: request.body, signal: local.signal, cache: 'no-store' })
    if (!alive || turn !== generation || document.hidden || local.signal.aborted) return
    if (kind === 'analyze') {
      const r = value as Result
      if (!['potential_impact', 'no_clear_impact', 'insufficient_evidence'].includes(r.status)) throw new Error('invalid response')
      result.value = r
      currentReview.value = r.review
    } else if (kind === 'history') events.value = (value as { items: Review[] }).items
    else { pending.value = null; currentReview.value = value as Review; events.value = [value as Review]; notice.value = '已记录你的复核选择；原决定和行动没有改变。'; if (request.status === 'revise') emit('revise') }
  } catch (e) {
    if (!alive || turn !== generation) return
    clear()
    const status = e instanceof ApiError ? e.status : 0
    error.value = status === 409 ? '决定版本、依据或来源已变化，旧分析已清理，请重新读取。' : status === 502 ? '模型结构、证据引用或事实校验失败，未生成可用分析。' : status === 422 ? '输入证据超限或无效，未生成分析。' : '请求失败、取消或模型暂不可用；没有确认新的决定。可主动重试。'
    if (kind === 'review' && ![401, 403, 404, 409, 410, 422].includes(status)) pending.value = request
    if ([403, 404, 409, 410].includes(status)) emit('invalidated')
    if (status === 401) emit('unauthorized')
  } finally { clearTimeout(timer); if (alive && turn === generation) busy.value = false }
}
function choose(status: string) {
  const review = currentReview.value
  if (!review || busy.value || pending.value) return
  pending.value = { path: `/decision-impact/${review.analysis_id}/review`, body: JSON.stringify({ request_id: crypto.randomUUID(), expected_sequence: review.sequence, status }), status }
  void run('review')
}
function hide() { if (document.hidden) cancel() }
onMounted(() => { void run('history'); document.addEventListener('visibilitychange', hide) })
onUnmounted(() => { alive = false; cancel(); document.removeEventListener('visibilitychange', hide) })
</script>

<template>
  <section class="impact" aria-label="决策影响与人工复核">
    <h3>变化影响与人工复核</h3>
    <p>只分析你选择的当前决定 v{{ version }}。来源变化是事实，AI 解释是待核查建议，最终决定属于你。没有评估其他相关决定。</p>
    <p>来源变化：{{ changeId }}。分析会发送到应用已配置的模型；本应用不保存分析正文，供应商可能按其政策留存输入。</p>
    <div class="button-row"><button class="btn primary" :disabled="busy || !!pending" @click="run('analyze')">分析所选变化的影响</button><button class="btn secondary" :disabled="busy || !!pending" @click="run('history')">刷新复核记录</button><button v-if="busy" class="btn secondary" @click="cancel">取消并清理</button></div>
    <p v-if="busy" role="status">正在验证来源并处理请求…</p><p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <button v-if="pending && !busy" class="btn secondary" @click="run('review')">使用同一请求重试复核操作</button>
    <article v-if="result">
      <h4>AI 分析：{{ labels[result.status] }}</h4><p v-if="result.reason">{{ result.reason === 'unchanged' ? '没有检测到内容变化，未调用模型。' : '差异无法完整比较，未调用模型。' }}</p>
      <p>引用原文与可机械检查的数字已校验；语义支持、比较关系及推断正确性仍需人工核查。未发现明确影响不等于未来绝无影响。</p>
      <template v-if="result.input"><p>原来源：{{ result.input.old }}</p><p>新来源：{{ result.input.new }}</p><p>比较范围：{{ result.input.scope }}</p></template>
      <section v-for="(f, i) in result.findings" :key="i"><h4>原决定中用户确认的内容</h4><blockquote>{{ f.decision.text }}</blockquote><small>{{ f.decision.evidence_id }} · {{ f.decision.start }}–{{ f.decision.end }}</small><h4>实际变化证据</h4><div v-for="(q, j) in f.changes" :key="j"><blockquote>{{ q.text }}</blockquote><small>{{ result.input?.evidence[q.evidence_id]?.side === 'old' ? '原版本证据' : '新版本证据' }} · 原文区间 {{ q.start }}–{{ q.end }}</small><details><summary>来源版本与定位</summary><small>{{ result.input?.evidence[q.evidence_id]?.source }} · {{ result.input?.evidence[q.evidence_id]?.locator }}</small></details></div><h4>AI 对可能关系的解释</h4><p>{{ f.explanation }}</p></section>
      <h4>未知项与证据缺口</h4><ul><li v-for="u in result.uncertainties" :key="u">{{ u }}</li></ul><h4>建议你确认的问题</h4><ul><li v-for="q in result.questions" :key="q">{{ q }}</li></ul>
      <p v-if="result.model">模型：{{ result.model.provider }} / {{ result.model.model }} · {{ result.generated_at }}（UTC）</p>
      <template v-if="result.review"><p>{{ labels[result.review.status] }}</p><div class="button-row"><button class="btn secondary" @click="choose('viewed')">标记已查看</button><button class="btn secondary" @click="choose('needs_review')">需要进一步复核</button><button class="btn secondary" @click="choose('keep')">暂不修改决定</button><button class="btn primary" @click="choose('revise')">进入原决定修订</button></div><p>进入修订不会自动保存；仍需编辑并明确确认。不会更新关联行动。</p></template>
    </article>
    <p v-for="(r, i) in events" :key="i">{{ labels[r.status] }} · 分析 {{ r.analysis_id }} · 操作 {{ r.sequence }} · {{ r.actor_id }} · {{ r.created_at }}（UTC）</p>
    <div v-if="currentReview && !result && !busy && !pending" class="button-row"><button class="btn secondary" @click="choose('needs_review')">需要进一步复核</button><button class="btn secondary" @click="choose('keep')">暂不修改决定</button><button class="btn primary" @click="choose('revise')">进入原决定修订</button></div>
    <p v-if="events.length">仅保存无正文复核记录；再次查看建议需主动重新分析。最多展示最近 50 条操作。</p>
  </section>
</template>

<style scoped>
.impact{margin:20px 0;padding:20px;background:#f8f7ff;border:1px solid #ded9f2;border-radius:14px;overflow-wrap:anywhere;min-width:0}.impact blockquote{margin:10px 0;padding:12px;border-left:3px solid #8c7bdc;white-space:pre-wrap}.impact button{white-space:normal;max-width:100%;margin:4px}.impact small{white-space:pre-wrap}.impact .button-row{flex-wrap:wrap}
</style>
