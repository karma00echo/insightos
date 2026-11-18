<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { api, ApiError } from '../api'
import type { Decision, DecisionPage } from '../decisionTypes'
const emit = defineEmits<{ select: [r: { id: string; version: number }]; unauthorized: [] }>()
const query = ref(''), items = ref<Decision[]>([]), error = ref(''), busy = ref(false)
let alive = true, generation = 0, controller: AbortController | null = null
async function search() {
  const turn = ++generation; controller?.abort(); controller = new AbortController(); busy.value = true; items.value = []; error.value = ''
  try {
    const result = await api<DecisionPage<Decision>>('/decisions/search', { method: 'POST', body: JSON.stringify({ query: query.value, limit: 10 }), signal: controller.signal, cache: 'no-store' })
    if (alive && generation === turn) items.value = result.items.filter(d => d.status === 'active' && d.revision.availability !== 'unavailable')
  } catch (e) { if (alive && generation === turn) { error.value = '搜索暂不可用，请重试。'; if (e instanceof ApiError && e.status === 401) emit('unauthorized') } }
  finally { if (alive && generation === turn) busy.value = false }
}
function select(d: Decision) { emit('select', { id: d.id, version: d.current_version }); items.value = []; query.value = '' }
onUnmounted(() => { alive = false; generation++; controller?.abort(); items.value = []; query.value = '' })
</script>
<template><section aria-label="引用已有决定"><h4>引用已有决定</h4><p>关键词检索，非语义搜索。最多引用三条；服务端重新核对版本和权限。</p><form @submit.prevent="search"><label>搜索已有决定<input v-model="query" maxlength="200" /></label><button type="submit" class="btn secondary" :disabled="busy">搜索可引用决定</button></form><p v-if="error" role="alert">{{ error }}</p><button v-for="d in items" :key="d.id" class="btn secondary" type="button" @click="select(d)">{{ d.revision.content?.question }} · 引用 v{{ d.current_version }}</button></section></template>
