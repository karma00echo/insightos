<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, ApiError } from '../api'
import { actionLabels, transitions } from '../actionTypes'
import type { Action, ActionRevision, ActionStatus } from '../actionTypes'
import ActionComposer from './ActionComposer.vue'
import '../decision-center.css'
const props = defineProps<{ initialActionId?: string }>()
const emit = defineEmits<{ unauthorized: []; chat: []; openDecision: [id: string] }>()
const reason = ref('')
const items = ref<Action[]>([]), current = ref<Action | null>(null), history = ref<ActionRevision[]>([])
const offset = ref(0), historyOffset = ref(0), filter = ref(''), creating = ref(false), visible = ref(true)
const loading = ref(false), busy = ref(false), error = ref(''), notice = ref('')
const editing = ref(false), title = ref(''), description = ref(''), due = ref(''), deleting = ref(false), approved = ref(false)
const pending = ref<{ path: string; method: string; body: string } | null>(null)
let alive = true, generation = 0, controller: AbortController | null = null
const safe = (a: Action): Action => ['manual', 'available'].includes(a.source_state) ? a : { ...a, source: undefined, revision: { version: a.revision.version, status: a.revision.status, operation: a.revision.operation, actor_id: a.revision.actor_id, created_at: a.revision.created_at } }
function clear() { reason.value = '';  items.value = []; current.value = null; history.value = []; title.value = ''; description.value = ''; due.value = ''; editing.value = false; deleting.value = false; approved.value = false; pending.value = null }
function invalidate() { generation++; controller?.abort(); clear() }
function failure(e: unknown) {
  const status = e instanceof ApiError ? e.status : 0
  if ([401, 403, 404, 410].includes(status)) { clear(); error.value = '任务不可访问或已删除。'; if (status === 401) emit('unauthorized') }
  else error.value = status === 409 ? '版本或请求冲突，已重新读取服务器内容；请核对后重新编辑并提交。' : status === 422 ? '字段或来源验证失败，请检查后重新提交。' : '请求失败，请重试。'
}
async function load(id = '', page = offset.value, hpage = 0) {
  if (busy.value || pending.value) return
  invalidate(); creating.value = false; loading.value = true; offset.value = page; historyOffset.value = hpage
  const turn = generation; controller = new AbortController(); const signal = controller.signal
  const timer = setTimeout(() => controller?.abort(), 30_000)
  try {
    if (id) {
      const [a, h] = await Promise.all([api<Action>(`/actions/${id}`, { signal, cache: 'no-store' }), api<{ items: ActionRevision[] }>(`/actions/${id}/history?offset=${hpage}&limit=20`, { signal, cache: 'no-store' })])
      if (!alive || turn !== generation) return
      current.value = safe(a); history.value = ['manual', 'available'].includes(a.source_state) ? h.items : h.items.map(r => ({ version: r.version, status: r.status, operation: r.operation, actor_id: r.actor_id, created_at: r.created_at }))
    } else {
      const result = await api<{ items: Action[] }>(`/actions?offset=${page}&limit=20${filter.value ? `&status=${filter.value}` : ''}`, { signal, cache: 'no-store' })
      if (alive && turn === generation) items.value = result.items.map(safe)
    }
  } catch (e) { if (alive && turn === generation) failure(e) }
  finally { clearTimeout(timer); if (alive && turn === generation) loading.value = false }
}
function edit() {
  if (!current.value?.revision.content) return
  title.value = current.value.revision.content.title; description.value = current.value.revision.content.description; due.value = current.value.revision.due_date || ''; reason.value = ''; approved.value = false; editing.value = true
}
function command(kind: 'edit' | 'status' | 'delete', status?: ActionStatus) {
  if (!current.value || busy.value || pending.value || (kind !== 'status' && !approved.value)) return
  const id = current.value.id
  pending.value = { path: `/actions/${id}${kind === 'status' ? '/status' : ''}`, method: kind === 'delete' ? 'DELETE' : kind === 'edit' ? 'PUT' : 'POST', body: JSON.stringify({ request_id: crypto.randomUUID(), expected_version: current.value.current_version, ...(kind !== 'delete' && reason.value.trim() ? { reason: reason.value } : {}), ...(kind === 'edit' ? { title: title.value, description: description.value, due_date: due.value || null } : kind === 'status' ? { status } : {}) }) }
  void send()
}
async function send() {
  if (!pending.value || busy.value) return
  const request = pending.value, id = current.value?.id || '', turn = ++generation
  controller?.abort(); controller = new AbortController(); busy.value = true; error.value = ''
  const timer = setTimeout(() => controller?.abort(), 30_000)
  let reload = false, removed = false
  try {
    await api(request.path, { method: request.method, body: request.body, signal: controller.signal, cache: 'no-store' })
    if (!alive || turn !== generation) return
    removed = request.method === 'DELETE'; pending.value = null; clear(); notice.value = removed ? '任务及历史正文已永久删除。' : '服务器已保存你的操作。'; reload = true
  } catch (e) {
    if (!alive || turn !== generation) return
    if (e instanceof ApiError && [409, 422].includes(e.status)) { pending.value = null; failure(e); clear(); reload = true }
    else if (e instanceof ApiError && [401, 403, 404, 410].includes(e.status)) failure(e)
    else error.value = '结果尚不确定，本次请求已冻结。请使用同一请求重试；离页不撤回已送达操作。'
  } finally { clearTimeout(timer); if (alive && turn === generation) { busy.value = false; if (reload) await load(removed ? '' : id) } }
}
function saved() { notice.value = '任务已创建，可在列表中查看。'; creating.value = false; void load('', 0) }
function visibilityChanged() { visible.value = !document.hidden; invalidate(); busy.value = false; loading.value = false; creating.value = false; if (visible.value) void load() }
onMounted(() => { void load(props.initialActionId || ''); document.addEventListener('visibilitychange', visibilityChanged) })
onUnmounted(() => { alive = false; invalidate(); document.removeEventListener('visibilitychange', visibilityChanged) })
</script>

<template>
  <main class="page decision-center action-center">
    <div class="page-heading"><div><span class="eyebrow">ACTION CENTER</span><h1>我的行动<span class="heading-dot">.</span></h1><p>由你批准，由你掌握进度与期限。</p></div><button class="btn secondary" @click="emit('chat')">返回 AI 对话</button></div>
    <p v-if="error" role="alert" class="dc-notice">{{ error }}</p><p v-if="notice" role="status" class="dc-notice">{{ notice }}</p>
    <template v-if="visible">
      <ActionComposer v-if="creating" @saved="saved" @cancel="creating = false; load()" @unauthorized="emit('unauthorized')" />
      <template v-else>
        <div class="dc-toolbar"><button class="btn secondary" :disabled="busy || !!pending" @click="error = ''; load()">刷新列表</button><button class="btn primary" :disabled="busy || !!pending" @click="invalidate(); creating = true">手动创建行动</button></div>
        <p v-if="loading" role="status">正在核对来源并加载任务…</p>
        <div v-if="pending" class="dc-notice"><button class="btn primary" :disabled="busy" @click="send">使用同一请求重试</button></div>
        <section v-if="current" aria-label="任务详情" class="dc-action">
          <div class="dc-toolbar"><span class="dc-tag">{{ actionLabels[current.status] }} · v{{ current.current_version }}</span><button class="btn secondary" :disabled="busy || !!pending" @click="load(current.id)">重新读取任务</button></div>
          <h2>{{ current.revision.content?.title || '来源失效 · 正文已保护' }}</h2>
          <p v-if="current.revision.content" class="action-body">{{ current.revision.content.description }}</p>
          <p v-if="current.revision.content">截止日期：{{ current.revision.due_date || '未设置' }}</p>
          <p>批准人：{{ current.approved_by }} · 批准时间：{{ current.approved_at }}（UTC）</p>
          <p v-if="current.completed_at">完成时间：{{ current.completed_at }}（UTC）</p><p v-if="current.cancelled_at">取消时间：{{ current.cancelled_at }}（UTC）</p>
          <p v-if="!['manual', 'available'].includes(current.source_state)" role="status">来源已撤销、变更或不可访问，不能继续视为当前有效依据。任务状态由你决定，仍可更新状态或删除。</p>
          <p v-else>{{ current.source ? `来源：已确认决定 v${current.source.version}` : '来源：你手动创建' }}</p>
          <button v-if="current.source_state === 'available' && current.source" class="btn secondary" @click="emit('openDecision', current.source.id)">查看来源决定</button>
          <label v-if="!editing && !deleting && current.revision.content">状态修改原因（可选）<input v-model="reason" maxlength="500" :disabled="busy || !!pending" /></label>
          <div class="dc-toolbar"><button v-for="s in transitions[current.status]" :key="s" class="btn secondary" :disabled="busy || !!pending || editing || deleting" @click="command('status', s)">{{ s === 'todo' && ['completed', 'cancelled'].includes(current.status) ? '重新开启为待执行' : `设为${actionLabels[s]}` }}</button><button class="btn secondary" :disabled="!current.revision.content || busy || !!pending" @click="edit">编辑内容与期限</button><button class="btn secondary dc-danger" :disabled="busy || !!pending" @click="deleting = true; editing = false; approved = false">永久删除</button></div>
          <form v-if="editing" @submit.prevent="command('edit')"><fieldset :disabled="busy || !!pending" class="action-fields"><label>任务标题<input v-model="title" required maxlength="300" @input="approved = false" /></label><label>任务说明<textarea v-model="description" maxlength="4000" @input="approved = false" /></label><label>截止日期<input v-model="due" type="date" @input="approved = false" /></label><label>修改原因（可选）<input v-model="reason" maxlength="500" @input="approved = false" /></label><label class="dc-check"><input v-model="approved" type="checkbox" />确认保存修改</label><button class="btn primary" :disabled="!approved">保存修改</button><button class="btn secondary" type="button" @click="editing = false; title = ''; description = ''">取消编辑</button></fieldset></form>
          <section v-if="deleting" role="alertdialog" aria-label="删除任务确认"><p>永久清除此任务和全部历史正文，无法从行动中心恢复。</p><label class="dc-check"><input v-model="approved" type="checkbox" :disabled="busy || !!pending" />我确认永久删除</label><button class="btn primary" :disabled="!approved || busy || !!pending" @click="command('delete')">确认永久删除</button><button class="btn secondary" :disabled="busy || !!pending" @click="deleting = false; approved = false">取消</button></section>
          <h3>操作历史</h3><div v-for="r in history" :key="r.version" class="dc-history-item"><strong>v{{ r.version }} · {{ r.operation }} · {{ actionLabels[r.status] }}</strong><small>{{ r.actor_id }} · {{ r.created_at }}（UTC）</small><span v-if="r.content">{{ r.content.title }} · {{ r.content.description }} · 期限 {{ r.due_date || '未设置' }} · 原因：{{ r.content.change_reason || '未提供' }}</span></div>
          <div class="dc-toolbar"><button class="btn secondary" :disabled="!historyOffset || loading || busy || !!pending" @click="load(current.id, offset, historyOffset - 20)">上一页历史</button><button class="btn secondary" :disabled="history.length < 20 || loading || busy || !!pending" @click="load(current.id, offset, historyOffset + 20)">下一页历史</button></div>
        </section>
        <section v-else-if="!loading" aria-label="任务列表">
          <label>任务状态 <select v-model="filter" @change="load('', 0)"><option value="">全部</option><option v-for="(label, state) in actionLabels" :key="state" :value="state">{{ label }}</option></select></label>
          <div v-if="!items.length" class="dc-empty"><h2>还没有任务</h2><p>可以手动创建，或从我的决策中制定行动计划。只有批准后才会保存。</p></div>
          <div class="dc-cards"><button v-for="a in items" :key="a.id" class="dc-card" @click="error = ''; load(a.id)"><span class="dc-tag">{{ actionLabels[a.status] }}</span><h2>{{ a.revision.content?.title || '来源失效 · 正文已保护' }}</h2><p v-if="a.revision.content">期限：{{ a.revision.due_date || '未设置' }}</p></button></div>
          <div class="dc-toolbar"><button class="btn secondary" :disabled="!offset" @click="load('', offset - 20)">上一页</button><button class="btn secondary" :disabled="items.length < 20" @click="load('', offset + 20)">下一页</button></div>
        </section>
      </template>
    </template>
  </main>
</template>
