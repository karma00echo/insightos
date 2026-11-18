<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, ApiError } from '../api'
import type { Basis, CandidateResult } from '../decisionTypes'
import DecisionReferencePicker from './DecisionReferencePicker.vue'
import DecisionCandidateCard from './DecisionCandidateCard.vue'

const emit = defineEmits<{ unauthorized: []; openDecision: [id: string] }>()
const messages = ref<{ role: 'user' | 'assistant'; content: string }[]>([])
const input = ref(''), sourceText = ref('[]'), error = ref(''), busy = ref(false)
const visible = ref(true)
const references = ref<{ id: string; version: number }[]>([])
function selectReference(r: { id: string; version: number }) { if (references.value.length < 3 && !references.value.some(x => x.id === r.id)) { references.value.push(r); messages.value = []; result.value = null } }
const result = ref<CandidateResult | null>(null)
const labels = { decision: '用户表达了选择，仍待确认', plan: '计划或意向', consideration: '尚在考虑', negation: '未做决定', ai_suggestion: 'AI 建议', unrelated: '无关讨论' }
let alive = true, controller: AbortController | null = null
async function discuss() {
  if (busy.value || result.value?.candidate || !input.value.trim()) return
  let sources: Basis[]
  try { sources = JSON.parse(sourceText.value); if (!Array.isArray(sources)) throw new Error() }
  catch { error.value = '来源需为授权引用的 JSON 数组，未提供时保留 []。'; return }
  const submitted = [...messages.value, { role: 'user' as const, content: input.value.trim() }]
  busy.value = true; error.value = ''; const requestController = new AbortController(); controller = requestController
  try {
    const response = await api<CandidateResult>('/decision-candidates', { method: 'POST', body: JSON.stringify({ messages: references.value.length ? [submitted[submitted.length - 1]] : submitted, sources, decision_references: references.value }), signal: requestController.signal })
    if (!alive || requestController.signal.aborted) return
    result.value = response
    messages.value = [...(references.value.length ? [submitted[submitted.length - 1]] : submitted), { role: 'assistant', content: response.reply }]
    input.value = ''
  } catch (reason) {
    if (!alive || requestController.signal.aborted) return
    if (reason instanceof ApiError && reason.status === 401) { emit('unauthorized'); return }
    if (references.value.length) { result.value = null; messages.value = [] }
    error.value = reason instanceof ApiError && reason.status === 422 ? '上下文或授权来源无效，请核对版本、权限和长度。'
      : reason instanceof ApiError && reason.status === 429 ? '请求或模型预算已达上限，请稍后重试。'
      : '候选暂未生成，临时输入已保留。请检查模型配置或稍后重试。'
  } finally { if (alive) busy.value = false }
}
function visibilityChanged() {
  if (document.hidden) {
    visible.value = false; alive = false; controller?.abort(); busy.value = false
    references.value = []; messages.value = []; result.value = null; input.value = ''; sourceText.value = '[]'
  } else { visible.value = true; alive = true }
}
onMounted(() => { if (typeof document !== 'undefined') document.addEventListener('visibilitychange', visibilityChanged) })
function ignore() { result.value = null }
onUnmounted(() => { if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', visibilityChanged); alive = false; controller?.abort(); messages.value = []; input.value = ''; sourceText.value = '[]'; references.value = []; result.value = null })
</script>

<template>
  <section v-if="visible" class="decision-discussion" aria-label="临时个人决策讨论">
    <h3>个人决策讨论</h3>
    <p>临时讨论与未确认候选仅保留在当前页面；刷新、关闭、离开聊天页或退出登录后丢失。只有点击确认保存的最终决定会长期保存。</p>
    <p>内容会发送至当前配置的语言模型；供应商可能按自身政策保留输入。这里不会自动导入历史聊天。</p>
    <DecisionReferencePicker v-if="!busy && !result?.candidate && references.length < 3" @select="selectReference" @unauthorized="emit('unauthorized')" /><p v-if="references.length">已引用 {{ references.length }} 条历史决定。这是用户此前确认的决定，并非已经独立核验的客观事实。引用模式仅讨论，不生成可保存候选；每轮重新核验历史决定。</p><button v-if="references.length && !busy" class="btn secondary" type="button" @click="references = []; messages = []; result = null">清除引用并开始新讨论</button><p v-for="r in references" :key="r.id">引用 {{ r.id }} · v{{ r.version }}</p><div v-for="(turn, index) in messages" :key="index" class="decision-turn"><strong>{{ turn.role === 'user' ? '你' : 'AI · 未确认' }}</strong><p>{{ turn.content }}</p></div>
    <p v-if="result" role="status">{{ labels[result.classification] }} · {{ result.readiness === 'ready' ? '候选字段齐全' : result.readiness === 'incomplete' ? '需要补充或编辑' : '没有可保存候选' }}<span v-if="result.missing_fields.length">：{{ result.missing_fields.join('、') }}</span></p>
    <DecisionCandidateCard v-if="result?.candidate" :candidate="result.candidate" :basis="result.basis" @open-decision="emit('openDecision', $event)" @ignore="ignore" @unauthorized="emit('unauthorized')" />
    <form v-else @submit.prevent="discuss">
      <label>补充问题、选择及理由<textarea v-model="input" maxlength="2000" rows="4" :disabled="busy" placeholder="例如：我考虑先做个人记忆或多 Agent，决定先做个人记忆，因为需要可靠数据基础。" /></label>
      <details><summary>显式授权来源（可选）</summary><p>仅提交你有权使用的引用；不自动读取历史。Memory 用 type/id/revision；消息及文档片段用 type/id/version。未提供时按个人陈述处理，不代表已核验。</p><textarea v-model="sourceText" aria-label="授权来源 JSON" :disabled="busy" maxlength="8000" rows="3" spellcheck="false" /></details>
      <button type="submit" class="btn primary" :disabled="busy || !input.trim()">{{ busy ? '正在讨论与识别…' : '讨论并识别候选' }}</button>
    </form>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.decision-discussion { margin: 16px 0; padding: 20px; overflow-y: auto; min-height: 0; }
p { line-height: 1.65; } label { display: grid; gap: 8px; } textarea { width: 100%; box-sizing: border-box; margin: 12px 0; } .decision-turn { padding: 12px; background: #f6f3ff; border-radius: 12px; margin: 8px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
