<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type Evaluation, type ResearchSummary } from '../api'

const emit = defineEmits<{ completed: [run: Evaluation] }>()
const tasks = ref<ResearchSummary[]>([]), taskId = ref(''), caseId = ref('')
const benchmark = ref<{ version: string; cases: { id: string; question: string; split: string; provenance: string; expected_points: string[] }[] } | null>(null)
const busy = ref(false), error = ref('')
const comparisonId = ref(''), review = ref<Record<string, unknown> | null>(null)
const selected = computed(() => benchmark.value?.cases.find(c => c.id === caseId.value))
const mismatch = computed(() => !!selected.value && tasks.value.find(t => t.id === taskId.value)?.question !== selected.value.question)
async function refresh() {
  try { tasks.value = (await api<ResearchSummary[]>('/research')).filter(t => ['completed', 'failed', 'cancelled'].includes(t.status)) }
  catch (e) { error.value = e instanceof Error ? e.message : '读取失败' }
}
async function loadFile(event: Event) {
  error.value = ''
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    if (file.size > 512 * 1024) throw new Error('评测集文件不能超过 512 KB')
    const data = JSON.parse(await file.text())
    if (!data.version || !Array.isArray(data.cases) || !data.cases.length) throw new Error('请选择含版本与用例的 Benchmark 文件')
    benchmark.value = data; caseId.value = data.cases[0].id; selectCase()
  } catch (e) { benchmark.value = null; error.value = e instanceof Error ? e.message : '文件无效' }
}
function selectCase() { taskId.value = tasks.value.find(t => t.question === selected.value?.question)?.id || '' }
async function loadReview(event: Event) {
  error.value = ''; review.value = null
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    if (file.size > 256 * 1024) throw new Error('人工核验记录不能超过 256 KB')
    review.value = JSON.parse(await file.text())
  } catch (e) { error.value = e instanceof Error ? e.message : '人工核验文件无效' }
}
async function run() {
  busy.value = true; error.value = ''
  try {
    const result = await api<Evaluation>('/research-evaluations', { method: 'POST', body: JSON.stringify({
      name: `研究离线评测 · ${caseId.value}`, task_id: taskId.value, case: selected.value, benchmark_version: benchmark.value?.version,
      comparison_task_id: comparisonId.value || null, review: review.value,
    }) }); emit('completed', result)
  } catch (e) { error.value = e instanceof Error ? e.message : '评测失败' }
  finally { busy.value = false }
}
onMounted(refresh)
</script>

<template>
  <section class="panel research-evaluation">
    <div class="section-heading"><h2>研究任务离线评测 <span>RESEARCH QUALITY</span></h2><button class="text-button" :disabled="busy" @click="refresh">刷新研究记录</button></div>
    <p>读取已保存的研究计划、证据与结论，不重新调用模型。工程检查和研究质量分别报告；未人工确认的语义指标保留待复核。</p>
    <div class="research-evaluation-fields">
      <label>导入 Benchmark<input type="file" accept=".json,application/json" :disabled="busy" @change="loadFile" /></label>
      <label>评测用例<select v-model="caseId" :disabled="!benchmark || busy" @change="selectCase"><option value="">请选择用例</option><option v-for="c in benchmark?.cases" :key="c.id" :value="c.id">{{ c.id }} · {{ c.split === 'holdout' ? '保留集' : '开发集' }}</option></select></label>
      <label>已保存研究任务<select v-model="taskId" :disabled="busy"><option value="">请选择问题相同的历史任务</option><option v-for="t in tasks" :key="t.id" :value="t.id">{{ t.question }} · {{ t.status }} · {{ t.id.slice(0, 8) }}</option></select></label>
      <label>独立执行对比（可选）<select v-model="comparisonId" :disabled="busy"><option value="">单次记录：一致性显示证据不足</option><option v-for="t in tasks.filter(t => t.id !== taskId && t.question === selected?.question && t.status === 'completed')" :key="t.id" :value="t.id">{{ t.id }} · {{ t.created_at }}</option></select></label>
      <label>人工核验记录（可选）<input type="file" accept=".json,application/json" :disabled="busy" @change="loadReview" /><small>需标注人、依据、证据位置及任务、Case、来源快照摘要；模型评判不能冒充人工。</small></label>
    </div>
    <div v-if="selected" class="research-case-preview"><strong>{{ selected.question }}</strong><p>标注来源：{{ selected.provenance }} · {{ benchmark?.version }}</p><ul><li v-for="point in selected.expected_points" :key="point">{{ point }}</li></ul></div>
    <p v-if="mismatch" class="alert">用例问题与所选存档任务不同，请重新选择。</p>
    <p v-if="error" role="alert" class="alert error-alert">{{ error }}</p>
    <div class="evaluation-actions"><small>合成记录与真实历史任务分开评分。保留集不用于优化。</small><button class="btn primary" :disabled="busy || !selected || !taskId || mismatch" @click="run">{{ busy ? '正在核验…' : '离线复评并保存' }}</button></div>
  </section>
</template>

<style scoped>
.research-evaluation{padding:24px;margin-bottom:24px}.research-evaluation>p,.research-case-preview p{color:var(--muted);font-size:13px}.research-evaluation-fields{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:18px 0}.research-evaluation-fields label{display:flex;flex-direction:column;gap:8px;min-width:0;font-size:13px}.research-evaluation-fields label:last-child{grid-column:1/-1}.research-evaluation-fields select,.research-evaluation-fields input{width:100%;min-width:0;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--surface,#fff);color:inherit}.research-case-preview{padding:16px;border:1px solid var(--border);border-radius:10px;overflow-wrap:anywhere}.research-case-preview ul{padding-left:20px;font-size:13px}.evaluation-actions{gap:14px;flex-wrap:wrap}@media(max-width:600px){.research-evaluation{padding:16px}.research-evaluation-fields{grid-template-columns:1fr}}
</style>
