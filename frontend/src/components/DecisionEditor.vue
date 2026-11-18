<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import type { CandidateDraft } from '../decisionTypes'
const props = defineProps<{ content: CandidateDraft; locked: boolean; blocked: boolean; restored: boolean }>()
const emit = defineEmits<{ confirm: [draft: CandidateDraft, reason: string]; cancel: [] }>()
const draft = ref<CandidateDraft>(JSON.parse(JSON.stringify(props.content)))
const reason = ref(''), approved = ref(false)
function addOption() { draft.value.options.push({ id: crypto.randomUUID().replaceAll('-', ''), label: '' }) }
const fields = [{ key: 'reasons' as const, label: '决策理由' }, { key: 'assumptions' as const, label: '关键假设' }, { key: 'review_conditions' as const, label: '复核条件' }]
const clean = (items: string[]) => items.map(s => s.trim()).filter(Boolean)
const valid = computed(() => draft.value.question.trim().length > 0 && draft.value.question.length <= 2000 && draft.value.options.length >= 2 && draft.value.options.length <= 20 && draft.value.options.every(o => o.label.trim() && o.label.length <= 2000) && draft.value.options.some(o => o.id === draft.value.selected_option) && clean(draft.value.reasons).length > 0 && fields.every(f => clean(draft.value[f.key]).length <= 20 && clean(draft.value[f.key]).every(s => s.length <= 2000)) && reason.value.trim().length > 0 && reason.value.length <= 500)
watch([draft, reason], () => { approved.value = false }, { deep: true, flush: 'sync' })
watch(() => props.blocked, () => { approved.value = false })
function submit() {
  if (!approved.value || !valid.value || props.locked || props.blocked) return
  const d = draft.value
  emit('confirm', { question: d.question.trim(), options: d.options.map(o => ({ id: o.id, label: o.label.trim() })), selected_option: d.selected_option, reasons: clean(d.reasons), assumptions: clean(d.assumptions), review_conditions: clean(d.review_conditions) }, reason.value.trim())
}
onUnmounted(() => { draft.value = { question: '', options: [], selected_option: '', reasons: [], assumptions: [], review_conditions: [] }; reason.value = ''; approved.value = false })
</script>

<template>
  <form class="dc-editor" aria-label="编辑决策" @submit.prevent="submit">
    <h2>{{ restored ? '编辑并重新确认已撤销决定' : '编辑我的决定' }}</h2><p class="dc-muted">保存会创建新版本。既有依据始终继承，不能通过移除引用解除来源保护。</p>
    <fieldset :disabled="locked">
      <label>决策问题<textarea v-model="draft.question" maxlength="2000" /></label>
      <div v-for="(option, i) in draft.options" :key="option.id" class="dc-option"><label>方案 {{ i + 1 }}<input v-model="option.label" maxlength="2000" /></label><button type="button" class="text-button" @click="draft.options.splice(i, 1)">移除方案</button></div>
      <button type="button" class="btn secondary" :disabled="draft.options.length >= 20" @click="addOption">添加备选方案</button>
      <label>最终选择<select v-model="draft.selected_option"><option value="">请选择</option><option v-for="o in draft.options" :key="o.id" :value="o.id">{{ o.label }}</option></select></label>
      <label v-for="field in fields" :key="field.key">{{ field.label }}（每行一条）<textarea :value="draft[field.key].join('\n')" @input="draft[field.key] = ($event.target as HTMLTextAreaElement).value.split('\n')" /></label>
      <label>修改原因<input v-model="reason" maxlength="500" /></label>
      <label class="dc-check"><input v-model="approved" type="checkbox" />我已核对最终内容，明确确认{{ restored ? '恢复并保存' : '保存此修订' }}。</label>
    </fieldset>
    <p v-if="!valid" class="dc-muted">请填写问题、至少两个方案、最终选择、至少一条理由及修改原因。</p>
    <div class="button-row"><button class="btn primary" type="submit" :disabled="locked || blocked || !approved || !valid">确认保存新版本</button><button class="btn secondary" type="button" :disabled="locked" @click="emit('cancel')">取消编辑</button></div>
  </form>
</template>
