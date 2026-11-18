<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, ApiError } from '../api'
import type { Decision, DecisionPage } from '../decisionTypes'
import DecisionDetail from './DecisionDetail.vue'
import '../decision-center.css'
const props = defineProps<{ initialDecisionId?: string; initialChangeId?: string }>()
const emit = defineEmits<{ unauthorized: []; chat: []; openAction: [id: string] }>()
const items = ref<Decision[]>([]), selected = ref(props.initialDecisionId || '')
const query = ref(''), searchQuery = ref('')
const filter = ref(''), offset = ref(0), loading = ref(false), error = ref(''), notice = ref(''), visible = ref(true)
let alive = true, generation = 0, controller: AbortController | null = null
function invalidate() { generation++; controller?.abort(); items.value = []; loading.value = false }
async function load(nextOffset = 0) {
  invalidate(); selected.value = ''; offset.value = nextOffset; error.value = ''; loading.value = true
  const turn = generation; const requestController = new AbortController(); controller = requestController
  const timer = setTimeout(() => requestController.abort(), 30_000)
  try {
    const page = await api<DecisionPage<Decision>>(searchQuery.value ? '/decisions/search' : `/decisions?offset=${nextOffset}&limit=20${filter.value ? `&status=${filter.value}` : ''}`, { ...(searchQuery.value ? { method: 'POST', body: JSON.stringify({ query: searchQuery.value, offset: nextOffset, limit: 20, status: filter.value || 'active' }) } : {}), signal: requestController.signal, cache: 'no-store' })
    if (!alive || turn !== generation || !visible.value) return
    // Discard protected fields even if an inconsistent response contains them.
    items.value = page.items.map(d => d.revision.availability === 'available' || d.revision.availability === 'available_original_version' ? d : { id: d.id, current_version: d.current_version, status: d.status, created_at: d.created_at, updated_at: d.updated_at, revision: { version: d.revision.version, status: d.revision.status, confirmation: d.revision.confirmation, revision_position: d.revision.revision_position, availability: 'unavailable', evidence_status: d.revision.evidence_status, created_at: d.revision.created_at } })
  } catch (reason) {
    if (!alive || turn !== generation) return
    error.value = reason instanceof ApiError && reason.status === 401 ? '登录已过期，请重新登录。' : reason instanceof ApiError && [403, 404].includes(reason.status) ? '暂时无法访问决策列表。' : '决策列表加载失败，请检查网络后重试。'
    if (reason instanceof ApiError && reason.status === 401) emit('unauthorized')
  } finally { clearTimeout(timer); if (alive && turn === generation) loading.value = false }
}
function open(id: string) { invalidate(); selected.value = id; notice.value = '' }
function deleted() { notice.value = '决定及其历史已永久删除。'; void load() }
function visibilityChanged() {
  if (document.hidden) { visible.value = false; invalidate(); selected.value = '' }
  else { visible.value = true; notice.value = '已重新核对访问权限。离开可见页面时的未保存编辑已清除。'; void load() }
}
onMounted(() => { if (!selected.value) void load(); document.addEventListener('visibilitychange', visibilityChanged) })
onUnmounted(() => { alive = false; invalidate(); selected.value = ''; document.removeEventListener('visibilitychange', visibilityChanged) })
</script>

<template>
  <main class="page decision-center">
    <div class="page-heading"><div><span class="eyebrow">DECISION CONTINUITY</span><h1>我的决策<span class="heading-dot">.</span></h1><p>记住你的选择，也看清当时的理由与依据。</p></div><button class="btn secondary" @click="emit('chat')">返回 AI 对话</button></div>
    <p class="dc-muted">只展示你明确确认的决定。确认不等于事实核验；离开或隐藏页面后重新加载，不在浏览器持久保存正文。</p>
    <p v-if="notice" class="dc-notice" role="status">{{ notice }}</p>
    <template v-if="visible">
      <DecisionDetail v-if="selected" :key="selected" :decision-id="selected" :change-id="selected === props.initialDecisionId ? props.initialChangeId : undefined" @open-action="id => emit('openAction', id)" @back="load(offset)" @deleted="deleted" @unauthorized="emit('unauthorized')" />
      <section v-else aria-label="决策列表">
        <form class="dc-toolbar" @submit.prevent="searchQuery = query.trim(); load(0)"><label>关键词搜索<input v-model="query" maxlength="200" placeholder="问题、选择或理由" /></label><button class="btn primary" type="submit">搜索决定</button><button class="btn secondary" type="button" @click="query = ''; searchQuery = ''; load(0)">清除搜索</button><span class="dc-muted">关键词匹配，非语义搜索；默认仅搜索有效且依据可用的当前版本。</span></form><div class="dc-toolbar"><label>状态筛选 <select v-model="filter" @change="load(0)"><option value="">全部决定</option><option value="active">当前有效</option><option value="withdrawn">已撤销</option></select></label><button class="btn secondary" :disabled="loading" @click="load(offset)">刷新列表</button><span class="dc-muted">第 {{ Math.floor(offset / 20) + 1 }} 页</span></div>
        <p v-if="loading" class="dc-empty" role="status">正在加载你的决定…</p>
        <div v-else-if="error" class="dc-empty" role="alert"><p>{{ error }}</p><button class="btn secondary" @click="load(offset)">重试加载</button></div>
        <div v-else-if="!items.length" class="dc-empty"><h2>{{ searchQuery ? '没有匹配且依据可用的决定' : offset ? '这一页没有更多决定' : filter === 'withdrawn' ? '还没有已撤销的决定' : '这里将留住你确认过的选择' }}</h2><p>在 AI 对话中打开个人决策讨论，核对候选并明确确认后，即可在这里管理。</p><button class="btn primary" @click="emit('chat')">回到对话</button></div>
        <div v-else class="dc-cards"><button v-for="d in items" :key="d.id" class="dc-card" @click="open(d.id)"><div class="dc-tags"><span class="dc-tag">{{ d.status === 'withdrawn' ? '已撤销' : '当前有效' }}</span><span class="dc-muted">v{{ d.current_version }}</span></div><h2>{{ d.revision.availability === 'unavailable' ? '依据不可用 · 内容已隐藏' : d.revision.content?.question }}</h2><p v-if="d.revision.availability !== 'unavailable'" class="dc-card-choice">{{ d.revision.content?.options.find(o => o.id === d.revision.content?.selected_option)?.label }}</p><p v-if="d.revision.availability === 'available_original_version'" class="dc-muted">原始依据版本仍可用</p><p v-if="d.revision.availability === 'unavailable'" class="dc-muted">仍可查看安全状态、撤销或永久删除</p><footer>最近修改 {{ new Date(d.updated_at + (d.updated_at.endsWith('Z') ? '' : 'Z')).toLocaleString('zh-CN') }} <span>查看详情 →</span></footer></button></div>
        <div class="dc-toolbar dc-pagination"><button class="btn secondary" :disabled="offset === 0 || loading" @click="load(Math.max(0, offset - 20))">上一页</button><button class="btn secondary" :disabled="items.length < 20 || loading" @click="load(offset + 20)">下一页</button></div>
      </section>
    </template>
  </main>
</template>
