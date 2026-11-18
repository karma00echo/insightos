<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, ApiError } from '../api'
import type { Document } from '../api'
const props = defineProps<{ documents: Document[] }>()
const emit = defineEmits<{ unauthorized: []; openImpact: [decisionId: string, changeId: string] }>()
type Change = { id: string; state: string; kind?: string; old?: { id: string; version: string }; new?: { id: string; version: string }; detected_at?: string; scope?: string; binary_changed?: boolean; truncated?: boolean; differences?: { operation: string; old: { text: string; locator: unknown; actor_id?: string; changed_at?: string }[]; new: { text: string; locator: unknown; actor_id?: string; changed_at?: string }[] }[]; references?: { decision_id: string; decision_version: number; relationship: string }[]; references_truncated?: boolean }
type Version = { id: string; status: string; created_at: string }
const kind = ref('document'), oldId = ref(''), newId = ref(''), oldVersion = ref(''), newVersion = ref(''), confirmed = ref(false)
const oldVersions = ref<Version[]>([]), newVersions = ref<Version[]>([]), items = ref<Change[]>([]), detail = ref<Change | null>(null)
const busy = ref(false), error = ref(''), offset = ref(0), visible = ref(true)
let alive = true, generation = 0, controller: AbortController | null = null
const labels: Record<string, string> = { changed: '内容发生变化', unchanged: '内容未变化', incomparable: '无法安全比较', unavailable: '来源已失效或无权访问' }
function clear() { items.value = []; detail.value = null; oldVersions.value = []; newVersions.value = []; oldId.value = ''; newId.value = ''; oldVersion.value = ''; newVersion.value = ''; confirmed.value = false }
async function request(path: string, options: RequestInit = {}, mode = 'list') {
  const turn = ++generation; controller?.abort(); controller = new AbortController(); const local = controller
  busy.value = true; error.value = ''; detail.value = null; items.value = []
  const timer = setTimeout(() => local.abort(), 30_000)
  try {
    const r = await api<Change | { items: Change[] }>(path, { ...options, signal: local.signal, cache: 'no-store' })
    if (!alive || turn !== generation) return
    if (mode === 'list') items.value = (r as { items: Change[] }).items.filter(v => v.state !== 'unavailable')
    else { const value = r as Change; if (value.state === 'unavailable') { clear(); detail.value = { id: value.id, state: value.state } } else detail.value = value }
  } catch (e) {
    if (!alive || turn !== generation) return
    error.value = '请求失败：请检查来源访问权限、确认及版本；来源失效时不会返回差异。可重新检测或刷新。'
    if (e instanceof ApiError && [401, 403, 404, 410].includes(e.status)) clear()
    if (e instanceof ApiError && e.status === 401) { clear(); emit('unauthorized') }
  } finally { clearTimeout(timer); if (alive && turn === generation) busy.value = false }
}
function load(page = 0) { offset.value = page; return request(`/source-changes?offset=${page}&limit=20`) }
async function versions(side: 'old' | 'new') {
  const turn = ++generation; controller?.abort(); controller = new AbortController(); const local = controller
  confirmed.value = false; detail.value = null; items.value = []; busy.value = true; error.value = ''
  if (side === 'old') { oldVersions.value = []; oldVersion.value = '' } else { newVersions.value = []; newVersion.value = '' }
  const timer = setTimeout(() => local.abort(), 30_000)
  try {
    const id = side === 'old' ? oldId.value : newId.value
    if (!id) return
    const rows = await api<Version[]>(`/documents/${encodeURIComponent(id)}/versions`, { signal: local.signal, cache: 'no-store' })
    if (!alive || turn !== generation) return
    if (side === 'old') oldVersions.value = rows.filter(v => v.status === 'ready'); else newVersions.value = rows.filter(v => v.status === 'ready')
  } catch { if (alive && turn === generation) { clear(); error.value = '版本不可读取，请重新核对来源。' } }
  finally { clearTimeout(timer); if (alive && turn === generation) busy.value = false }
}
function detect() {
  if (!confirmed.value || busy.value) return
  return request('/source-changes/detect', { method: 'POST', body: JSON.stringify({ kind: kind.value, old: { id: oldId.value, version: oldVersion.value }, new: { id: kind.value === 'memory' ? oldId.value : newId.value, version: newVersion.value }, confirmed: true }) }, 'detail')
}
function hide() { visible.value = !document.hidden; generation++; controller?.abort(); clear(); busy.value = false; if (visible.value) void load() }
onMounted(() => { void load(); document.addEventListener('visibilitychange', hide) })
onUnmounted(() => { alive = false; generation++; controller?.abort(); clear(); document.removeEventListener('visibilitychange', hide) })
</script>

<template>
  <section class="source-changes" aria-label="来源变化检测">
    <h2>来源版本与变化</h2><p>仅比较明确选择的版本，不判断对决策的影响。新上传不自动关联旧文件；目标使用已确认 Memory 的 plan 分类。</p>
    <template v-if="visible">
      <p v-if="error" role="alert">{{ error }}</p>
      <form @submit.prevent="detect"><fieldset :disabled="busy" @input="confirmed = false" @change="confirmed = false">
        <label>来源类型<select v-model="kind" @change="clear"><option value="document">知识库文档</option><option value="memory">已确认 Memory / 计划</option></select></label>
        <template v-if="kind === 'document'">
          <label>原文档<select v-model="oldId" @change="versions('old')"><option value="">请选择</option><option v-for="d in props.documents" :key="d.id" :value="d.id">{{ d.name }} · {{ d.id.slice(0, 8) }}</option></select></label>
          <label>原版本<select v-model="oldVersion"><option value="">请选择</option><option v-for="v in oldVersions" :key="v.id" :value="v.id">{{ v.created_at }} · {{ v.id }}</option></select></label>
          <label>新文档<select v-model="newId" @change="versions('new')"><option value="">请选择</option><option v-for="d in props.documents" :key="d.id" :value="d.id">{{ d.name }} · {{ d.id.slice(0, 8) }}</option></select></label>
          <label>新版本<select v-model="newVersion"><option value="">请选择</option><option v-for="v in newVersions" :key="v.id" :value="v.id">{{ v.created_at }} · {{ v.id }}</option></select></label>
        </template>
        <template v-else><label>Memory ID<input v-model="oldId" required /></label><label>原确认修订<input v-model="oldVersion" inputmode="numeric" required /></label><label>下一确认修订<input v-model="newVersion" inputmode="numeric" required /></label><p>须为同一记忆相邻的已确认修订；撤销和删除不代表目标改变。</p></template>
      </fieldset><label class="source-check"><input v-model="confirmed" type="checkbox" :disabled="busy" />我确认这两个版本应当比较；不同文档只建立本次明确关联，不推断文件链</label><button class="btn primary" :disabled="busy || !confirmed || !oldId || !oldVersion || !newVersion || (kind === 'document' && !newId)">检测所选版本</button></form>
      <button class="btn secondary" :disabled="busy" @click="load()">刷新已检测记录</button><p v-if="busy" role="status">正在验证来源和计算差异…</p>
      <div v-for="c in items" :key="c.id"><button class="btn secondary" @click="request(`/source-changes/${c.id}`, {}, 'detail')">{{ labels[c.state] }} · {{ c.kind }} · {{ c.detected_at }}（UTC）</button></div>
      <p v-if="!busy && !detail && !items.length">没有当前可访问的变化记录。来源失效的记录不参与列表展示。</p>
      <div v-if="!detail" class="button-row"><button class="btn secondary" :disabled="busy || !offset" @click="load(offset - 20)">上一页变化</button><button class="btn secondary" :disabled="busy || items.length < 20" @click="load(offset + 20)">下一页变化</button></div>
      <article v-if="detail" aria-label="变化详情"><h3>{{ labels[detail.state] }}</h3><template v-if="detail.state !== 'unavailable'">
        <p>原版本：{{ detail.old?.id }} / {{ detail.old?.version }}</p><p>新版本：{{ detail.new?.id }} / {{ detail.new?.version }}</p><p>比较范围：{{ detail.scope }}。reparsed_original_text 为原文件重新解析文本；extracted_chunks 为保留分块。两者均不代表原始排版或语义变化。</p><p v-if="detail.kind === 'document'">文件字节变化：{{ detail.binary_changed ? '是' : '否' }}</p>
        <p v-if="detail.state === 'incomparable'">解析内容缺失、不完整或超过安全比较上限；未推断内容是否相同。</p><p v-if="detail.truncated">差异展示已截断，不能视为完整差异。</p>
        <section v-for="(d, index) in detail.differences" :key="index"><h4>{{ d.operation }}</h4><div v-for="side in (['old', 'new'] as const)" :key="side"><strong>{{ side === 'old' ? '原内容' : '新内容' }}</strong><div v-for="(unit, n) in d[side]" :key="n"><pre>{{ unit.text }}</pre><small>实际定位：{{ unit.locator }} {{ unit.actor_id ? `操作者 ${unit.actor_id} · ${unit.changed_at}（UTC）` : '' }}</small></div></div></section>
        <h4>明确引用关系（不等于受到影响）</h4><button v-if="!detail.references" class="btn secondary" :disabled="busy" @click="request(`/source-changes/${detail.id}`, {}, 'detail')">读取引用关系</button><p v-for="(r, n) in detail.references" :key="n">Decision {{ r.decision_id }} · 引用版本 {{ r.decision_version }} · {{ r.relationship }} <button class="btn secondary" @click="emit('openImpact', r.decision_id, detail.id)">查看当前决定并复核影响</button></p><p v-if="detail.references?.length === 0">未发现当前有权查看的明确引用关系。</p><p v-if="detail.references_truncated">仅展示前100项授权引用关系，并非完整候选集合；只会评估你选择的当前决定。</p>
      </template><p v-else>来源失效或无权访问，版本定位、差异及引用关系已保护。</p></article>
    </template>
  </section>
</template>

<style scoped>
.source-changes{margin:24px 0;padding:24px;border:1px solid #e2dff2;border-radius:18px;background:#faf9ff;overflow-wrap:anywhere}.source-changes fieldset{border:0;padding:0;display:grid;gap:12px}.source-changes label{display:grid;gap:6px;margin:12px 0}.source-changes select,.source-changes input:not([type=checkbox]){max-width:100%;min-width:0;width:100%;padding:10px;box-sizing:border-box}.source-changes .source-check{display:flex;align-items:center}.source-changes pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0edfa;padding:12px;border-radius:8px}.source-changes small{white-space:pre-wrap}.source-changes button{margin:6px 6px 6px 0;max-width:100%;white-space:normal;overflow-wrap:anywhere}
</style>
