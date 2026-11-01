<script setup lang="ts">
import { computed } from 'vue'
type Metric = { numerator: number; denominator: number | null; valid_samples: number; pending: number; failed: number; score: number | null; status: string; definition: string; units: unknown[] }
type Row = { case_id: string; task_id: string; question: string; case_reference?: unknown; result: unknown; review: unknown; model_assisted_evaluation: string; split: string; engineering: { status: string; checks: unknown[] }; quality_status: string; metrics: Record<string, Metric>; failures: Record<string, unknown>[]; evidence: Record<string, unknown>[]; execution_record: unknown; model_config: unknown; evaluator_version: string; code_sha256: unknown; task_sha256: string; case_sha256: string; source_snapshot_sha256: string }
const props = defineProps<{ result: Record<string, unknown> }>()
const row = computed(() => props.result as unknown as Row)
const names: Record<string, string> = { task_completion: '任务完成度', evidence_coverage: '固定原文证据覆盖', citation_validity: '引用有效率', fact_support: '发现与分析支持', conflict_identification: '冲突识别', result_consistency: '独立研究结果一致性', plan_quality: '规划质量' }
const states: Record<string, string> = { scored: '已计分', pending_review: '待人工复核', insufficient_evidence: '证据不足', not_applicable: '不适用' }
function json(value: unknown) { return JSON.stringify(value, null, 2) }
</script>
<template>
  <div class="research-score">
    <p><strong>工程检查：{{ row.engineering.status }}</strong> · 研究质量：{{ row.quality_status }}</p>
    <p class="muted">Case {{ row.case_id }} · {{ row.split }} · Research Task {{ row.task_id }}</p>
    <p>{{ row.question }}</p>
    <div class="research-score-grid"><article v-for="(m, key) in row.metrics" :key="key"><span>{{ names[key] || key }}</span><strong>{{ m.score === null ? states[m.status] || '不可判定' : `${(m.score * 100).toFixed(1)}%` }}</strong><small>通过 {{ m.numerator }} / {{ m.denominator ?? '分母未知' }} · 有效 {{ m.valid_samples }} · 待复核 {{ m.pending }} · 失败 {{ m.failed }}</small><p>{{ m.definition }}</p><details><summary>逐项判定与待审原因</summary><pre>{{ json(m.units) }}</pre></details></article></div>
    <details><summary>计划、检索、综合与结论的分阶段检查</summary><pre>{{ json(row.engineering.checks) }}</pre></details>
    <details><summary>失败案例（{{ row.failures.length }}）</summary><pre>{{ json(row.failures) }}</pre></details>
    <details><summary>Case 标注来源、评分标准与原始研究结论</summary><pre>{{ json({ case: row.case_reference || '旧评测未保存完整Case，请查原Benchmark', result: row.result }) }}</pre></details>
    <details><summary>人工核验与模型辅助评估</summary><pre>{{ json({ human_review: row.review, model_assisted_evaluation: row.model_assisted_evaluation }) }}</pre></details>
    <details><summary>证据来源与位置（{{ row.evidence.length }} 组）</summary><pre>{{ json(row.evidence) }}</pre></details>
    <details><summary>研究计划、执行记录与用量</summary><pre>{{ json(row.execution_record) }}</pre></details>
    <details><summary>模型配置与执行版本</summary><pre>{{ json({ model: row.model_config, evaluator: row.evaluator_version, code: row.code_sha256, task_sha256: row.task_sha256, case_sha256: row.case_sha256, source_snapshot_sha256: row.source_snapshot_sha256 }) }}</pre></details>
  </div>
</template>
<style scoped>
.research-score{margin:20px 0;overflow-wrap:anywhere}.research-score>p{font-size:13px}.research-score-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.research-score-grid article{padding:16px;border:1px solid var(--border);border-radius:10px;background:#f7f6ff}.research-score-grid span,.research-score-grid small{display:block;font-size:12px}.research-score-grid strong{display:block;font-size:20px;color:#6353bf;margin:8px 0}.research-score-grid p{font-size:12px;color:var(--muted);line-height:1.6}.research-score details{padding:12px 0;border-bottom:1px solid var(--border)}.research-score summary{cursor:pointer;font-size:13px}.research-score pre{white-space:pre-wrap;max-height:360px;overflow:auto;font-size:12px;margin-top:12px}@media(max-width:600px){.research-score-grid{grid-template-columns:1fr}}
</style>
