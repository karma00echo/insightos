<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, ApiError } from '../api'
import type { Action } from '../actionTypes'
const props = defineProps<{ source?: { id: string; version: number } }>()
const emit = defineEmits<{ saved: []; cancel: []; unauthorized: [] }>()
const title = ref(''), description = ref(''), due = ref(''), approved = ref(false)
const busy = ref(false), loading = ref(false), error = ref(''), notice = ref(''), blocked = ref(false)
const pending = ref<string | null>(null)
let alive = true, controller: AbortController | null = null
async function propose() {
  if (!props.source) return
  loading.value = true; blocked.value = true
  controller = new AbortController(); const timer = setTimeout(() => controller?.abort(), 30_000)
  try {
    const result = await api<{ candidate: { title: string; description: string }; notice: string }>('/actions/proposals', { method: 'POST', body: JSON.stringify(props.source), signal: controller.signal, cache: 'no-store' })
    if (!alive) return
    title.value = result.candidate.title; description.value = result.candidate.description; notice.value = result.notice; blocked.value = false
  } catch (e) {
    if (!alive) return
    error.value = '决定来源不可用或请求失败，请返回并重新核对。'
    if (e instanceof ApiError && e.status === 401) emit('unauthorized')
  } finally { clearTimeout(timer); if (alive) loading.value = false }
}
async function submit() {
  if (busy.value || blocked.value || loading.value || (!pending.value && (!approved.value || !title.value.trim()))) return
  pending.value ??= JSON.stringify({ request_id: crypto.randomUUID(), approved: true, title: title.value, description: description.value, due_date: due.value || null, source: props.source || null })
  busy.value = true; error.value = ''; controller = new AbortController()
  const timer = setTimeout(() => controller?.abort(), 30_000)
  try {
    const result = await api<Action>('/actions', { method: 'POST', body: pending.value, signal: controller.signal, cache: 'no-store' })
    if (!alive) return
    if (!result.id || !result.approved_at) throw new Error('Invalid persistence response')
    title.value = ''; description.value = ''; due.value = ''; pending.value = null; emit('saved')
  } catch (e) {
    if (!alive) return
    const status = e instanceof ApiError ? e.status : 0
    if ([401, 403, 404, 410].includes(status)) { title.value = ''; description.value = ''; pending.value = null; blocked.value = true; error.value = '任务或来源不可用，请返回重新读取。'; if (status === 401) emit('unauthorized') }
    else if ([409, 422].includes(status)) { pending.value = null; approved.value = false; blocked.value = !!props.source; error.value = '字段、来源版本或请求冲突，请重新核对后批准；关联提案请返回重开。' }
    else error.value = '结果尚不确定。内容已冻结，请使用同一请求重试；离页不会撤回已送达的批准。'
  } finally { clearTimeout(timer); if (alive) busy.value = false }
}
onMounted(propose)
onUnmounted(() => { alive = false; controller?.abort(); title.value = ''; description.value = ''; pending.value = null })
</script>

<template>
  <section class="dc-action" aria-label="行动提案">
    <h2>{{ source ? '制定行动计划' : '手动创建行动' }}</h2>
    <p>每次明确批准一项任务。提案仅留在当前页面；忽略、离开或刷新不会创建任务。不执行外部操作。</p>
    <p v-if="loading" role="status">正在核对决定来源…</p><p v-if="notice" role="status">{{ notice }}</p><p v-if="error" role="alert">{{ error }}</p>
    <form @submit.prevent="submit">
      <fieldset :disabled="busy || !!pending || blocked || loading" class="action-fields">
        <label>任务标题<input v-model="title" required maxlength="300" @input="approved = false" /></label>
        <label>任务说明<textarea v-model="description" maxlength="4000" rows="4" placeholder="补充具体步骤与完成标准" @input="approved = false" /></label>
        <label>截止日期（可选，不自动提醒）<input v-model="due" type="date" @input="approved = false" /></label>
        <label class="dc-check"><input v-model="approved" type="checkbox" />我已核对并明确批准创建此任务</label>
      </fieldset>
      <div class="dc-toolbar"><button class="btn primary" type="submit" :disabled="busy || loading || blocked || (!pending && (!approved || !title.trim()))">{{ busy ? '正在提交…' : pending ? '使用同一请求重试' : '批准并创建任务' }}</button><button class="btn secondary" type="button" @click="emit('cancel')">忽略并关闭</button></div>
    </form>
  </section>
</template>
