import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, defineComponent, h, nextTick } from 'vue'
import ts from 'typescript'

const require = createRequire(import.meta.url)
const vueUrl = pathToFileURL(require.resolve('vue/dist/vue.runtime.esm-bundler.js')).href
const iconsUrl = pathToFileURL(require.resolve('lucide-vue-next')).href
const stub = name => defineComponent({ setup: () => () => h('retained-page', { name }) })
globalThis.Document ??= class Document {}
globalThis.ShadowRoot ??= class ShadowRoot {}
let version = 0
async function component(name, mocks, expose) {
  globalThis.__centerMocks = mocks
  const file = name === 'App' ? '../src/App.vue' : `../src/components/${name}.vue`
  const source = readFileSync(new URL(file, import.meta.url), 'utf8')
    .replace('</script>', `defineExpose({ ${expose} })\n</script>`)
  const { descriptor } = parse(source)
  let script = compileScript(descriptor, { id: name, inlineTemplate: true }).content
  script = script.replace(/import (\w+) from ['"][^'"]+\.vue['"]/g, (_all, local) => `const ${local} = globalThis.__centerMocks.${local}`)
  script = script.replace(/import ['"][^'"]+\.css['"];?/g, '')
  let js = ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
  js = js.replaceAll('from "vue"', `from '${vueUrl}'`).replaceAll("from 'vue'", `from '${vueUrl}'`)
    .replaceAll("from 'lucide-vue-next'", `from '${iconsUrl}'`)
    .replace(/import\s+\{([^}]+)\}\s+from\s+['"][^'"]*\/actionTypes['"];?/g, 'const {$1} = globalThis.__centerMocks')
    .replace(/import\s+\{([^}]+)\}\s+from\s+['"][^'"]*\/(?:api|uploadQueue)['"];?/g, 'const {$1} = globalThis.__centerMocks')
  return (await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64') + `#${++version}`)).default
}
function mount(Component, props = {}) {
  const make = type => ({ type, props: {}, children: [], text: '', parent: null, get options() { return this.children.filter(child => child.type === 'option') }, addEventListener() {}, removeEventListener() {}, getRootNode() { return { activeElement: null } } })
  const renderer = createRenderer({
    createElement: make, createText: text => ({ ...make('text'), text }), createComment: text => ({ ...make('comment'), text }),
    insert(child, parent, anchor) { if (child.parent) { const at = child.parent.children.indexOf(child); if (at >= 0) child.parent.children.splice(at, 1) } child.parent = parent; const at = parent.children.indexOf(anchor); at < 0 ? parent.children.push(child) : parent.children.splice(at, 0, child) },
    remove(child) { const at = child.parent?.children.indexOf(child); if (at >= 0) child.parent.children.splice(at, 1) },
    setText(node, text) { node.text = text }, setElementText(node, text) { node.text = text; node.children = [] },
    patchProp(node, key, _prev, value) { node.props[key] = value; node[key] = value },
    parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  })
  const root = make('root'); let vm
  const app = renderer.createApp({ render: () => h(Component, { ...props, ref: value => { vm = value } }) })
  app.mount(root)
  return { root, get vm() { return vm }, unmount: () => app.unmount() }
}
const nodes = root => [root, ...root.children.flatMap(nodes)]
const text = root => nodes(root).filter(n => n.type !== 'comment').map(n => n.text).join(' ')
async function flush() { for (let i = 0; i < 30; i++) await Promise.resolve(); await nextTick() }
function environment() {
  const originals = { localStorage: globalThis.localStorage, setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout }
  const storage = new Map([['view', 'evaluations'], ['zhiheng_view', 'evaluations'], ['zhiheng_token', 'obsolete']]), timers = new Map()
  let id = 0
  globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }
  globalThis.setInterval = fn => { timers.set(++id, fn); return id }
  globalThis.clearInterval = id => timers.delete(id)
  globalThis.setTimeout = () => ++id
  globalThis.clearTimeout = () => {}
  return { timers, restore() { Object.assign(globalThis, originals) } }
}
function mocksFor(role) {
  const calls = []
  const actor = { id: role, email: `${role}@example.com`, name: role, is_platform_admin: role === 'platform-admin' }
  const citations = [{ chunk_id: 'chunk', document_id: 'doc', document_name: '证据.md', knowledge_base_id: 'kb', text: '原始证据', page: 1, locator: { line_start: 1, line_end: 1 } }]
  const api = async (path, options) => {
    calls.push([path, options])
    if (path === '/ready') return {}
    if (path === '/auth/oidc/config') return { enabled: false }
    if (path === '/auth/me') return actor
    if (path === '/auth/login') return { user: actor }
    if (path === '/auth/logout') return undefined
    if (path === '/organizations') return role === 'organization-admin' ? [{ id: 'org', name: '团队', role: 'admin' }] : []
    if (path === '/knowledge-bases') return [{ id: 'kb', name: '资料库', role: 'admin' }]
    if (path === '/documents' || path === '/conversations' || path === '/platform/users') return []
    if (path === '/system') return { mode: 'offline', features: {} }
    if (path === '/conversations/saved') return { id: 'saved', messages: [{ id: 'u', role: 'user', content: '原始问题' }, { id: 'a', role: 'assistant', content: '有据回答 [1]', citations, trace: [{ step: 'retrieve', detail: '检索完成' }] }] }
    if (path === '/platform/quotas') return { limits: {}, overridden: false }
    if (/^\/platform\/jobs\?kind=(ingestion|cleanup)$/.test(path)) return []
    throw new Error(`Unexpected product request: ${path}`)
  }
  return { calls, api, API_BASE: '/api', ApiError: class extends Error {}, downloadDocument() {}, streamChat() { throw new Error('No generation expected') }, submitUploads() {}, ResearchStarters: stub('starters'), DeepResearch: stub('research'), GovernancePanel: stub('governance'), DocumentVersions: stub('versions') }
}

class FakeError extends Error { constructor(status) { super('private-server-message'); this.status = status } }
const labels = { todo: '待执行', in_progress: '进行中', completed: '已完成', cancelled: '已取消' }
const transitions = { todo: ['in_progress', 'completed', 'cancelled'], in_progress: ['todo', 'completed', 'cancelled'], completed: ['todo'], cancelled: ['todo'] }
const row = (extra = {}) => ({ id: 'a1', current_version: 1, status: 'todo', source_state: 'manual', approved_by: 'u1', approved_at: '2026-11-17', revision: { version: 1, status: 'todo', operation: 'create', actor_id: 'u1', created_at: '2026-11-17', content: { title: 'PRIVATE-TITLE', description: 'PRIVATE-BODY' }, due_date: '2026-12-01' }, ...extra })
function browser() {
  const env = environment(), previous = globalThis.document, listeners = new Map()
  globalThis.document = { hidden: false, addEventListener: (k, f) => listeners.set(k, f), removeEventListener: k => listeners.delete(k) }
  return { hide(value) { document.hidden = value; listeners.get('visibilitychange')?.() }, restore() { globalThis.document = previous; env.restore() } }
}
async function composer(api, source) {
  const C = await component('ActionComposer', { api, ApiError: FakeError }, 'title, description, due, approved, busy, pending, blocked, submit')
  return mount(C, source ? { source } : {})
}
async function center(api) {
  const C = await component('ActionCenter', { api, ApiError: FakeError, actionLabels: labels, transitions, ActionComposer: stub('composer') }, 'items, current, history, load, edit, title, description, due, approved, command, send, pending, creating')
  return mount(C)
}

test('manual proposal requires explicit approval; double submit sends once and no draft persistence', async () => {
  const env = browser(), calls = []; let resolve
  try {
    const app = await composer((p, o) => { calls.push([p, o]); return new Promise(r => { resolve = r }) })
    await flush(); app.vm.title = 'my task'; await app.vm.submit(); assert.equal(calls.length, 0)
    app.vm.approved = true; const first = app.vm.submit(); await app.vm.submit(); assert.equal(calls.length, 1)
    const body = JSON.parse(calls[0][1].body); assert.equal(body.approved, true); assert.equal(body.due_date, null); assert.equal(body.source, null)
    resolve(row()); await first; await flush(); assert.equal(app.vm.title, ''); app.unmount()
  } finally { env.restore() }
})

test('source proposal is a read-only template request, ignore never creates', async () => {
  const env = browser(), calls = []
  try {
    const app = await composer(async (p, o) => { calls.push([p, o]); return { candidate: { title: 'template', description: '' }, notice: 'manual' } }, { id: 'd1', version: 4 })
    await flush(); assert.equal(app.vm.title, 'template'); assert.equal(calls.length, 1); assert.equal(calls[0][0], '/actions/proposals')
    assert.deepEqual(JSON.parse(calls[0][1].body), { id: 'd1', version: 4 }); app.unmount(); assert.equal(calls.length, 1)
  } finally { env.restore() }
})

test('uncertain create freezes identical request for retry, no early success', async () => {
  const env = browser(), calls = []; let count = 0
  try {
    const app = await composer(async (p, o) => { calls.push([p, o]); if (++count === 1) throw new FakeError(503); return row() })
    app.vm.title = 'task'; app.vm.approved = true; await app.vm.submit(); assert.ok(app.vm.pending); assert.match(text(app.root), /结果尚不确定/)
    await app.vm.submit(); assert.equal(calls[0][1].body, calls[1][1].body); assert.equal(app.vm.pending, null); app.unmount()
  } finally { env.restore() }
})

test('source failure blocks approval and late proposal after unmount cannot restore draft', async () => {
  const env = browser(); let resolve
  try {
    const failed = await composer(async () => { throw new FakeError(422) }, { id: 'd1', version: 1 }); await flush(); assert.ok(failed.vm.blocked); failed.unmount()
    const app = await composer(() => new Promise(r => { resolve = r }), { id: 'd1', version: 1 }); await flush(); app.unmount()
    resolve({ candidate: { title: 'PRIVATE-LATE', description: '' }, notice: '' }); await flush(); assert.doesNotMatch(text(app.root), /PRIVATE-LATE/)
  } finally { env.restore() }
})

test('center loads detail/history and source masking discards even inconsistent protected fields', async () => {
  const env = browser()
  try {
    const value = row({ source_state: 'changed' })
    const app = await center(async p => p.includes('/history?') ? { items: [value.revision] } : p === '/actions/a1' ? value : { items: [value] })
    await flush(); assert.doesNotMatch(text(app.root), /PRIVATE/); await app.vm.load('a1'); await flush()
    assert.doesNotMatch(text(app.root), /PRIVATE/); assert.equal(app.vm.current.status, 'todo'); assert.match(text(app.root), /仍可更新状态或删除/); app.unmount()
  } finally { env.restore() }
})

test('edit and status send current expected_version; 409 reload requires deliberate new edit', async () => {
  const env = browser(), calls = []; let failure = false
  try {
    const value = row()
    const app = await center(async (p, o = {}) => { calls.push([p, o]); if (o.method) { if (failure) throw new FakeError(409); return value } return p.includes('/history?') ? { items: [value.revision] } : p === '/actions/a1' ? value : { items: [value] } })
    await flush(); await app.vm.load('a1'); app.vm.edit(); app.vm.title = 'edited'; app.vm.approved = true; app.vm.command('edit'); await flush()
    let request = calls.find(([,o]) => o.method === 'PUT'); assert.equal(JSON.parse(request[1].body).expected_version, 1)
    failure = true; app.vm.command('status', 'completed'); await flush(); assert.match(text(app.root), /冲突/); assert.equal(app.vm.pending, null); assert.equal(app.vm.current.current_version, 1)
    app.unmount()
  } finally { env.restore() }
})

test('delete requires confirmation and removes current/history/private content after success', async () => {
  const env = browser(), calls = []
  try {
    const value = row()
    const app = await center(async (p, o = {}) => { calls.push([p, o]); if (o.method) return undefined; return p.includes('/history?') ? { items: [value.revision] } : p === '/actions/a1' ? value : { items: [] } })
    await flush(); await app.vm.load('a1'); app.vm.command('delete'); await flush(); assert.equal(calls.filter(([,o]) => o.method).length, 0)
    app.vm.approved = true; app.vm.command('delete'); await flush(); assert.equal(app.vm.current, null); assert.doesNotMatch(text(app.root), /PRIVATE/); assert.match(text(app.root), /永久删除/); app.unmount()
  } finally { env.restore() }
})

test('hidden or unmounted center clears private data and suppresses late reads', async () => {
  const env = browser(); let resolve
  try {
    let delay = false
    const app = await center(async p => delay && p === '/actions/a1' ? new Promise(r => { resolve = r }) : p.includes('/history?') ? { items: [row().revision] } : { items: [row()] })
    await flush(); delay = true; const request = app.vm.load('a1'); await flush(); env.hide(true); resolve(row()); await request; await flush()
    assert.equal(app.vm.current, null); assert.doesNotMatch(text(app.root), /PRIVATE/); app.unmount()
  } finally { env.restore() }
})
