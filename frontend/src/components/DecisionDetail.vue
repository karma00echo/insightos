<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, ApiError } from '../api'
import type { CandidateDraft, Decision, DecisionPage, DecisionRevision } from '../decisionTypes'
import DecisionContent from './DecisionContent.vue'
import DecisionEditor from './DecisionEditor.vue'
import DecisionActions from './DecisionActions.vue'
import DecisionImpact from './DecisionImpact.vue'
import ActionComposer from './ActionComposer.vue'
const planning = ref(false)
const props = defineProps<{ decisionId: string; changeId?: string }>()
const emit = defineEmits<{ back: []; deleted: []; unauthorized: []; openAction: [id: string] }>()
const current = ref<Decision | null>(null), viewed = ref<DecisionRevision | null>(null)
const history = ref<DecisionRevision[]>([]), historyOffset = ref(0), selectedVersion = ref<number | null>(null)
const loading = ref(false), busy = ref(false), error = ref(''), notice = ref('')
const editing = ref<CandidateDraft | null>(null), editVersion = ref(0), conflict = ref(false)
const action = ref<'revoke' | 'delete' | null>(null), approved = ref(false)
const pending = ref<{ method: string; path: string; body: string; kind: 'edit' | 'revoke' | 'delete' } | null>(null)
let alive = true, generation = 0, controller: AbortController | null = null
const base = `/decisions/${encodeURIComponent(props.decisionId)}`
function safeRevision(r: DecisionRevision): DecisionRevision {
  if (r.availability === 'available' || r.availability === 'available_original_version') return r
  return { version: r.version, status: r.status, confirmation: r.confirmation, revision_position: r.revision_position, availability: 'unavailable', evidence_status: r.evidence_status, created_at: r.created_at }
}
function clearPrivate() { planning.value = false; current.value = null; viewed.value = null; history.value = []; editing.value = null; pending.value = null; action.value = null; approved.value = false }
function message(reason: unknown) {
  const status = reason instanceof ApiError ? reason.status : 0
  return status === 401 ? '登录已过期，请重新登录。' : status === 403 ? '当前无权访问此决定。' : status === 404 || status === 410 ? '决定或版本不存在，可能已被删除。' : status === 422 ? '字段或来源验证失败，请检查内容与来源状态并重新确认。' : status === 409 ? '服务器版本或请求标识有冲突。编辑内容已保留，请查看服务器当前版本后决定如何处理。' : '暂未获得服务器结果，请检查网络后重试。'
}
function denied(reason: unknown) {
  if (!(reason instanceof ApiError) || ![401, 403, 404, 410].includes(reason.status)) return false
  clearPrivate(); error.value = message(reason)
  if (reason.status === 401) emit('unauthorized')
  return true
}
async function load(version: number | null = null, offset = historyOffset.value, keepError = false) {
  if (busy.value) return
  const turn = ++generation; controller?.abort(); const requestController = new AbortController(); controller = requestController
  planning.value = false; current.value = null; viewed.value = null; history.value = []; loading.value = true
  selectedVersion.value = version; historyOffset.value = offset
  if (!keepError) error.value = ''
  const timer = setTimeout(() => requestController.abort(), 30_000)
  try {
    const [d, h, v] = await Promise.all([
      api<Decision>(base, { signal: requestController.signal, cache: 'no-store' }),
      api<DecisionPage<DecisionRevision>>(`${base}/history?offset=${offset}&limit=20`, { signal: requestController.signal, cache: 'no-store' }),
      version == null ? Promise.resolve(null) : api<DecisionRevision>(`${base}/versions/${version}`, { signal: requestController.signal, cache: 'no-store' }),
    ])
    if (!alive || turn !== generation) return
    d.revision = safeRevision(d.revision); current.value = d
    // History navigation holds metadata only. Opening any version always revalidates on the server.
    history.value = h.items.map(r => ({ version: r.version, status: r.status, confirmation: r.confirmation, revision_position: r.revision_position, availability: r.availability, evidence_status: r.evidence_status, created_at: r.created_at }))
    viewed.value = v ? safeRevision(v) : d.revision
    if (d.revision.availability === 'unavailable') { editing.value = null; pending.value = null; conflict.value = false }
    else if (editing.value && d.current_version !== editVersion.value) conflict.value = true
  } catch (reason) {
    if (!alive || turn !== generation) return
    if (!denied(reason)) error.value = message(reason)
  } finally { clearTimeout(timer); if (alive && turn === generation) loading.value = false }
}
function startEdit() {
  if (!current.value?.revision.content || current.value.revision.availability === 'unavailable' || pending.value || busy.value) return
  editing.value = JSON.parse(JSON.stringify(current.value.revision.content)); editVersion.value = current.value.current_version
  conflict.value = false; selectedVersion.value = null; viewed.value = current.value.revision; action.value = null
}
function rebase() {
  if (!conflict.value || !current.value || current.value.revision.availability === 'unavailable') return
  editVersion.value = current.value.current_version; conflict.value = false
  notice.value = '已采用服务器当前版本作为编辑基线。请再次核对草稿并明确确认。'
}
function save(draft: CandidateDraft, reason: string) {
  if (!editing.value || conflict.value || pending.value || busy.value || !current.value || current.value.revision.availability === 'unavailable') return
  pending.value = { kind: 'edit', method: 'PUT', path: base, body: JSON.stringify({ ...draft, reason, expected_version: editVersion.value, request_id: crypto.randomUUID(), confirmed: true, basis: [] }) }
  void send()
}
function confirmAction() {
  if (!action.value || !approved.value || !current.value || pending.value || busy.value) return
  pending.value = { kind: action.value, method: action.value === 'delete' ? 'DELETE' : 'POST', path: base + (action.value === 'revoke' ? '/revoke' : ''), body: JSON.stringify({ request_id: crypto.randomUUID(), expected_version: current.value.current_version }) }
  void send()
}
async function send() {
  if (!pending.value || busy.value) return
  const request = pending.value, turn = ++generation
  controller?.abort(); const requestController = new AbortController(); controller = requestController; busy.value = true; loading.value = false; error.value = ''; notice.value = ''
  viewed.value = null; history.value = []
  const timer = setTimeout(() => requestController.abort(), 30_000)
  let reload = false
  try {
    await api(request.path, { method: request.method, body: request.body, signal: requestController.signal, cache: 'no-store' })
    if (!alive || turn !== generation) return
    clearPrivate(); conflict.value = false
    if (request.kind === 'delete') { emit('deleted'); return }
    notice.value = request.kind === 'revoke' ? '你已撤销此决定，历史版本仍然保留。' : '服务器已接受修订，以下重新读取当前版本。'
    reload = true
  } catch (reason) {
    if (!alive || turn !== generation) return
    if (!denied(reason)) {
      error.value = message(reason)
      if (reason instanceof ApiError && [409, 422].includes(reason.status)) {
        pending.value = null; approved.value = false; action.value = null
        if (request.kind === 'edit') conflict.value = true
        reload = true
      } else error.value = '结果尚不确定，已冻结本次请求。请使用同一请求重试；离开页面不会撤回已送达的操作。'
    }
  } finally {
    clearTimeout(timer)
    if (alive && turn === generation) { busy.value = false; if (reload) await load(null, 0, true) }
  }
}
onMounted(() => load())
onUnmounted(() => { alive = false; generation++; controller?.abort(); clearPrivate() })
</script>

<template>
  <section class="dc-detail" aria-label="决策详情">
    <div class="dc-toolbar"><button class="btn secondary" @click="emit('back')">返回决策列表</button><button class="btn secondary" :disabled="busy || !!pending" @click="load(selectedVersion)">重新读取</button></div>
    <p v-if="error" class="dc-notice" role="alert">{{ error }}</p><p v-if="notice" class="dc-notice" role="status">{{ notice }}</p><p v-if="loading" role="status">正在核对来源并加载决策…</p>
    <div v-if="pending" class="dc-notice"><p>本次操作内容已冻结，重复点击不会生成新请求。</p><button class="btn primary" :disabled="busy" @click="send">{{ busy ? '正在提交…' : '使用同一请求重试' }}</button></div>
    <template v-if="current">
      <div class="dc-toolbar"><span class="dc-tag">{{ current.status === 'withdrawn' ? '决定已被你撤销' : '当前决定有效' }} · 当前版本 {{ current.current_version }}</span><span class="dc-muted">创建：{{ new Date(current.created_at + (current.created_at.endsWith('Z') ? '' : 'Z')).toLocaleString('zh-CN') }} · 修改：{{ new Date(current.updated_at + (current.updated_at.endsWith('Z') ? '' : 'Z')).toLocaleString('zh-CN') }}</span></div>
      <div class="dc-toolbar" v-if="!pending && !editing">
        <button class="btn primary" :disabled="busy || loading || current.status !== 'active' || current.revision.availability === 'unavailable'" @click="planning = true">制定行动计划</button>
        <button class="btn primary" :disabled="busy || loading || current.revision.availability === 'unavailable'" @click="startEdit">{{ current.status === 'withdrawn' ? '编辑并重新确认' : selectedVersion != null ? '编辑当前决定' : '编辑决定' }}</button>
        <button class="btn secondary" :disabled="busy || loading || current.status === 'withdrawn'" @click="action = 'revoke'; approved = false">撤销决定</button><button class="btn secondary dc-danger" :disabled="busy || loading" @click="action = 'delete'; approved = false">永久删除</button>
      </div>
      <ActionComposer v-if="planning && current.status === 'active' && current.revision.availability !== 'unavailable'" :source="{ id: current.id, version: current.current_version }" @saved="load(); notice = '行动已创建，请在左侧我的行动中管理。'" @cancel="planning = false" @unauthorized="emit('unauthorized')" />
      <section v-if="action && !pending" class="dc-action" role="alertdialog" :aria-label="action === 'delete' ? '永久删除确认' : '撤销决定确认'">
        <h3>{{ action === 'delete' ? '永久删除此决定？' : '撤销此决定？' }}</h3><p>{{ action === 'delete' ? '将清除此决定及全部历史业务内容，无法从决策中心恢复。' : '这是你主动撤销决定；会新增撤销版本，保留历史，不会自动选择其他方案。' }}</p>
        <label class="dc-check"><input v-model="approved" type="checkbox" />我理解并确认此操作</label><div class="button-row"><button class="btn primary" :disabled="!approved || busy" @click="confirmAction">{{ action === 'delete' ? '确认永久删除' : '确认撤销' }}</button><button class="btn secondary" @click="action = null; approved = false">取消</button></div>
      </section>
      <section v-if="conflict && editing" class="dc-notice"><h3>请比较服务器当前内容与保留的草稿</h3><p>不会自动覆盖新版本。采用当前版本后仍须再次明确确认保存。</p><button class="btn secondary" @click="rebase">已查看当前版本，继续我的编辑</button></section>
      <DecisionImpact v-if="props.changeId && viewed && viewed.version === current.current_version && current.status === 'active' && viewed.availability !== 'unavailable' && !editing && !pending && !action" :key="`${current.id}:${viewed.version}`" :decision-id="current.id" :version="viewed.version" :change-id="props.changeId" @revise="startEdit" @invalidated="clearPrivate(); error = '来源或决定已变化，请重新读取。'" @unauthorized="emit('unauthorized')" />
      <DecisionActions v-if="viewed && !editing && !pending" :key="`${current.id}:${viewed.version}`" :decision-id="current.id" :version="viewed.version" @open="id => emit('openAction', id)" @unauthorized="emit('unauthorized')" />
      <DecisionContent v-if="viewed && (!editing || conflict)" :revision="viewed" />
      <section v-if="!editing && !pending" class="dc-history" aria-label="决策历史"><h3>版本历史 · 只读追溯</h3><p class="dc-muted">“被后续修订取代”仅指版本位置，不等于整条决定已撤销。</p><div class="dc-history-items"><button v-for="r in history" :key="r.version" class="dc-history-item" :aria-pressed="viewed?.version === r.version" @click="load(r.version)"><strong>v{{ r.version }}</strong><span>{{ r.revision_position === 'current' ? '当前版本' : '已被后续修订取代' }}</span><span>{{ r.status === 'withdrawn' ? '用户撤销' : r.version === 1 ? '创建并确认' : '编辑并确认' }}</span><small>{{ new Date(r.created_at + (r.created_at.endsWith('Z') ? '' : 'Z')).toLocaleString('zh-CN') }}</small><small v-if="r.availability === 'unavailable'">依据不可用 · 正文隐藏</small></button></div><div class="dc-toolbar"><button class="btn secondary" :disabled="historyOffset === 0 || loading" @click="load(selectedVersion, Math.max(0, historyOffset - 20))">上一页历史</button><button class="btn secondary" :disabled="history.length < 20 || loading" @click="load(selectedVersion, historyOffset + 20)">下一页历史</button><button v-if="selectedVersion != null" class="text-button" @click="load(null)">查看当前版本</button></div></section>
    </template>
    <DecisionEditor v-if="editing" :content="editing" :locked="busy || !!pending" :blocked="conflict || loading || !current" :restored="current?.status === 'withdrawn'" @confirm="save" @cancel="editing = null; conflict = false" />
  </section>
</template>
