<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, ApiError } from '../api'
import { actionLabels } from '../actionTypes'
import type { Action, ActionStatus } from '../actionTypes'
const props = defineProps<{ decisionId: string; version: number }>()
const emit = defineEmits<{ open: [id: string]; unauthorized: [] }>()
const items = ref<Action[]>([]), counts = ref<Record<ActionStatus, number> | null>(null)
const offset = ref(0), total = ref(0), loading = ref(false), error = ref(''), available = ref(false)
let alive = true, generation = 0, controller: AbortController | null = null
async function load(page = 0) {
  const turn = ++generation; controller?.abort(); controller = new AbortController()
  const requestController = controller
  items.value = []; counts.value = null; total.value = 0; available.value = false; loading.value = true; error.value = ''; offset.value = page
  const timer = setTimeout(() => requestController.abort(), 30_000)
  try {
    const r = await api<{ available: boolean; counts: Record<ActionStatus, number> | null; items: Action[]; total: number | null }>(`/decisions/${encodeURIComponent(props.decisionId)}/actions?version=${props.version}&offset=${page}&limit=20`, { signal: requestController.signal, cache: 'no-store' })
    if (!alive || turn !== generation) return
    if (r.available && r.items.every(a => a.source_state === 'available' && a.source?.id === props.decisionId && a.source.version === props.version)) {
      available.value = true; items.value = r.items; counts.value = r.counts; total.value = r.total || 0
    }
  } catch (e) { if (alive && turn === generation) { error.value = '关联行动读取失败，请重新核对。'; if (e instanceof ApiError && e.status === 401) emit('unauthorized') } }
  finally { clearTimeout(timer); if (alive && turn === generation) loading.value = false }
}
onMounted(() => load())
onUnmounted(() => { alive = false; generation++; controller?.abort(); items.value = []; counts.value = null })
</script>

<template>
  <section class="dc-action" aria-label="关联行动与进度">
    <div class="dc-toolbar"><h3>关联行动与进度 · 决定 v{{ version }}</h3><button class="btn secondary" :disabled="loading" @click="load(offset)">刷新行动进度</button></div>
    <p v-if="loading" role="status">正在核对关联与权限…</p><p v-else-if="error" role="alert">{{ error }}</p>
    <template v-else-if="available">
      <p>当前版本有效关联 {{ total }} 项。按任务状态计数，取消单独列出；任务完成不会改变决定状态。</p>
      <div class="dc-toolbar"><span v-for="(label, state) in actionLabels" :key="state" class="dc-tag">{{ label }} {{ counts?.[state] || 0 }}</span></div>
      <p v-if="!items.length">此页没有已批准的关联行动。旧版任务不会计入当前版本。</p>
      <div class="dc-cards"><button v-for="a in items" :key="a.id" class="dc-card" @click="emit('open', a.id)"><strong>{{ a.revision.content?.title }}</strong><p>{{ actionLabels[a.status] }} · 期限 {{ a.revision.due_date || '未设置' }}</p><span>查看行动详情 →</span></button></div>
      <div class="dc-toolbar"><button class="btn secondary" :disabled="!offset" @click="load(offset - 20)">上一页行动</button><button class="btn secondary" :disabled="offset + items.length >= total" @click="load(offset + 20)">下一页行动</button></div>
    </template>
    <p v-else>此版本已过时、决定已撤销或来源不可访问，关联明细与统计已保护。任务状态仍由你独立管理。</p>
  </section>
</template>
