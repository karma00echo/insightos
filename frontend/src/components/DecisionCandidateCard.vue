<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { api, ApiError } from '../api'
import type { Basis, CandidateDraft } from '../decisionTypes'

const props = defineProps<{ candidate: CandidateDraft; basis: Basis[] }>()
const emit = defineEmits<{ ignore: []; unauthorized: []; openDecision: [id: string] }>()
const draft = ref<CandidateDraft>(JSON.parse(JSON.stringify(props.candidate)))
const approved = ref(false), saving = ref(false), error = ref(''), savedId = ref('')
const pending = ref<string | null>(null)
let alive = true
let controller: AbortController | null = null
const lines = (text: string) => text.split('\n')
const normalized = (items: string[]) => items.map(s => s.trim()).filter(Boolean)
const valid = computed(() => draft.value.question.trim() && draft.value.question.trim().length <= 2000 &&
  draft.value.options.length >= 2 && draft.value.options.length <= 20 && draft.value.options.every(o => o.label.trim() && o.label.trim().length <= 2000) &&
  draft.value.options.some(o => o.id === draft.value.selected_option) && normalized(draft.value.reasons).length > 0 &&
  [draft.value.reasons, draft.value.assumptions, draft.value.review_conditions].every(items => normalized(items).length <= 20 && normalized(items).every(s => s.length <= 2000)))
const locked = computed(() => saving.value || !!pending.value || !!savedId.value)
function addOption() { if (!locked.value && draft.value.options.length < 20) draft.value.options.push({ id: crypto.randomUUID().replaceAll('-', ''), label: '' }) }
function removeOption(index: number) { if (!locked.value) draft.value.options.splice(index, 1) }
async function save() {
  if (saving.value || savedId.value || !approved.value || !valid.value) return
  if (!pending.value) pending.value = JSON.stringify({ ...draft.value, reasons: normalized(draft.value.reasons), assumptions: normalized(draft.value.assumptions), review_conditions: normalized(draft.value.review_conditions), basis: props.basis, request_id: crypto.randomUUID(), confirmed: true })
  saving.value = true; error.value = ''
  controller = new AbortController()
  const timeout = setTimeout(() => controller?.abort(), 30_000)
  try {
    const result = await api<{ id: string; current_version: number; revision: { confirmation: string; availability: string } }>('/decisions', { method: 'POST', body: pending.value, signal: controller.signal })
    if (!alive) return
    if (!result.id || result.revision?.confirmation !== 'confirmed' || result.revision?.availability === 'unavailable') {
      error.value = '服务响应尚不能确认可用保存结果，请使用同一请求重试。'; return
    }
    savedId.value = result.id
  } catch (reason) {
    if (!alive) return
    if (reason instanceof ApiError && reason.status === 401) { emit('unauthorized'); return }
    error.value = reason instanceof ApiError && reason.status === 422
      ? '字段或来源验证失败。请检查编辑内容；来源失效时请关闭候选并重新授权，不能移除来源后重试。'
      : reason instanceof ApiError && reason.status === 409 ? '请求标识冲突，尚未确认保存。已保留内容，请重试或核查结果。'
      : '尚未收到保存成功结果。内容已保留，请使用同一请求重试，避免重复创建。'
    if (reason instanceof ApiError && reason.status === 422) { pending.value = null; approved.value = false }
  } finally { clearTimeout(timeout); if (alive) saving.value = false }
}
onUnmounted(() => { alive = false; controller?.abort(); draft.value = { question: '', options: [], selected_option: '', reasons: [], assumptions: [], review_conditions: [] }; pending.value = null })
</script>

<template>
  <article class="decision-candidate" aria-label="待确认决策卡片">
    <h3>{{ savedId ? '决策 · 已确认保存' : '候选决策 · 尚未确认' }}</h3>
    <p>请编辑并核对全部字段。问题、假设及复核条件可能由 AI 起草；方案和理由提取自你的原话。确认只代表你的决定，依据仍未独立核验。</p>
    <fieldset :disabled="locked">
      <label>决策问题（AI 草稿，可编辑）<textarea v-model="draft.question" maxlength="2000" /></label>
      <div v-for="(option, index) in draft.options" :key="option.id" class="decision-option"><label>方案 {{ index + 1 }}<input v-model="option.label" maxlength="2000" /></label><button type="button" class="text-button" @click="removeOption(index)">移除方案</button></div>
      <button type="button" class="btn secondary" :disabled="draft.options.length >= 20" @click="addOption">添加备选方案</button>
      <label>我的最终选择<select v-model="draft.selected_option"><option value="">请选择</option><option v-for="option in draft.options" :key="option.id" :value="option.id">{{ option.label || '待补充方案' }}</option></select></label>
      <label>我的理由（每行一条）<textarea :value="draft.reasons.join('\n')" @input="draft.reasons = lines(($event.target as HTMLTextAreaElement).value)" /></label>
      <label>假设（AI 草稿，每行一条，可留空）<textarea :value="draft.assumptions.join('\n')" @input="draft.assumptions = lines(($event.target as HTMLTextAreaElement).value)" /></label>
      <label>复核条件（AI 草稿，每行一条，可留空）<textarea :value="draft.review_conditions.join('\n')" @input="draft.review_conditions = lines(($event.target as HTMLTextAreaElement).value)" /></label>
    </fieldset>
    <details><summary>本次明确授权的依据（未独立核验）</summary><pre>{{ JSON.stringify(basis, null, 2) }}</pre></details>
    <p v-if="!valid" role="status">请补全问题、至少两个方案、最终选择和至少一条理由。</p>
    <label v-if="!savedId"><input v-model="approved" type="checkbox" :disabled="locked" />我已核对最终内容，明确决定并同意长期保存。</label>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="savedId" role="status">服务器已确认保存，决定 ID：{{ savedId }}</p>
    <div class="button-row"><button v-if="savedId" class="btn primary" type="button" @click="emit('openDecision', savedId)">查看我的决定</button><button v-if="!savedId" type="button" class="btn primary" :disabled="saving || !approved || !valid" @click="save">{{ saving ? '正在保存…' : pending ? '使用同一请求重试保存' : '确认保存我的决定' }}</button><button type="button" class="btn secondary" :disabled="saving" @click="emit('ignore')">{{ savedId ? '关闭卡片' : '忽略候选' }}</button></div>
    <p v-if="pending && !savedId">确认请求可能已送达服务器；关闭或离开页面不会撤回已发出的确认。</p>
  </article>
</template>

<style scoped>
.decision-candidate { padding: 20px; border: 1px solid #c7b9ef; border-radius: 16px; background: #faf8ff; color: #34324d; }
fieldset { border: 0; padding: 0; margin: 12px 0; min-width: 0; }
label { display: grid; gap: 6px; margin: 12px 0; }
input[type=checkbox] { width: auto; }
textarea, input, select { max-width: 100%; width: 100%; box-sizing: border-box; }
textarea { min-height: 64px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
p { line-height: 1.6; } .decision-option { display: flex; align-items: center; gap: 10px; } .decision-option label { flex: 1; }
</style>
