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
  globalThis.__candidateMocks = mocks
  const file = name === 'App' ? '../src/App.vue' : `../src/components/${name}.vue`
  const source = readFileSync(new URL(file, import.meta.url), 'utf8')
    .replace('</script>', `defineExpose({ ${expose} })\n</script>`)
  const { descriptor } = parse(source)
  let script = compileScript(descriptor, { id: name, inlineTemplate: true }).content
  script = script.replace(/import (\w+) from ['"][^'"]+\.vue['"]/g, (_all, local) => `const ${local} = globalThis.__candidateMocks.${local}`)
  let js = ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
  js = js.replaceAll('from "vue"', `from '${vueUrl}'`).replaceAll("from 'vue'", `from '${vueUrl}'`)
    .replaceAll("from 'lucide-vue-next'", `from '${iconsUrl}'`)
    .replace(/import\s+\{([^}]+)\}\s+from\s+['"][^'"]*\/(?:api|uploadQueue)['"];?/g, 'const {$1} = globalThis.__candidateMocks')
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

const proposal = () => ({ classification: 'decision', readiness: 'ready', confirmed: false, evidence_status: 'unverified', missing_fields: [], reply: '待确认', basis: [{ type: 'user_input' }], candidate: { question: '先做什么？', options: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }], selected_option: 'a', reasons: ['理由'], assumptions: [], review_conditions: [] } })
const success = { id: 'saved-id', current_version: 1, revision: { confirmation: 'confirmed', availability: 'available' } }
class FakeApiError extends Error { constructor(status) { super('private server detail'); this.status = status } }
const click = (app, label) => nodes(app.root).find(n => n.type === 'button' && text(n).includes(label)).props.onClick()

test('card: only dedicated confirmation saves final edits; double click and timeout retry reuse exact payload', async () => {
  const env = environment(), calls = []
  let reject, resolve
  const mocks = { ApiError: FakeApiError, api: (_path, options) => { calls.push([_path, options]); return new Promise((a,b) => { resolve = a; reject = b }) } }
  const C = await component('DecisionCandidateCard', mocks, 'draft, approved, pending, saving, save')
  const app = mount(C, { candidate: proposal().candidate, basis: proposal().basis })
  try {
    await flush(); assert.equal(calls.length, 0)
    const reasonBox = nodes(app.root).filter(n => n.type === 'textarea')[1]
    reasonBox.props.onInput({ target: { value: '第一条\n' } }); await flush()
    assert.equal(reasonBox.props.value, '第一条\n')
    reasonBox.props.onInput({ target: { value: '第一条\n第二条' } }); await flush()
    app.vm.draft.question = '用户最终修改'; app.vm.approved = true
    click(app, '确认保存我的决定'); await flush()
    app.vm.save(); await flush(); assert.equal(calls.length, 1)
    assert.doesNotMatch(text(app.root), /服务器已确认保存/)
    const payload = JSON.parse(calls[0][1].body)
    assert.equal(payload.question, '用户最终修改'); assert.equal(payload.confirmed, true); assert.ok(payload.request_id)
    assert.deepEqual(payload.reasons, ['第一条', '第二条'])
    reject(new FakeApiError(0)); await flush()
    assert.match(text(app.root), /尚未收到保存成功/)
    click(app, '同一请求重试'); await flush()
    assert.equal(calls[1][1].body, calls[0][1].body)
    resolve(success); await flush(); assert.match(text(app.root), /服务器已确认保存/)
    assert.equal(calls[0][0], '/decisions')
  } finally { app.unmount(); env.restore() }
})

for (const status of [409, 422]) {
  test(`card: ${status} retains edits and sources; validation needs renewed approval`, async () => {
    const env = environment(), calls = []
    const C = await component('DecisionCandidateCard', { ApiError: FakeApiError, api: async (p, o) => { calls.push(o); throw new FakeApiError(status) } }, 'draft, approved, pending, save')
    const basis = [{ type: 'memory', id: 'private-source', revision: 1 }]
    const app = mount(C, { candidate: proposal().candidate, basis })
    try {
      app.vm.draft.question = '保留编辑'; app.vm.approved = true
      await app.vm.save(); await flush()
      assert.equal(app.vm.draft.question, '保留编辑')
      assert.deepEqual(JSON.parse(calls[0].body).basis, basis)
      assert.equal(app.vm.pending === null, status === 422)
      assert.equal(app.vm.approved, status !== 422)
      assert.doesNotMatch(text(app.root), /服务器已确认保存/)
    } finally { app.unmount(); env.restore() }
  })
}

test('card: incomplete candidate editable, ignore makes no request, remount loses edits', async () => {
  const env = environment(); let writes = 0, ignored = 0
  const incomplete = { ...proposal().candidate, options: [{ id:'a', label:'A' }], reasons: [] }
  const C = await component('DecisionCandidateCard', { ApiError: FakeApiError, api: async () => { writes++ } }, 'draft, approved, save, valid, addOption')
  let app = mount(C, { candidate: incomplete, basis: proposal().basis, onIgnore: () => ignored++ })
  try {
    app.vm.approved = true; await app.vm.save(); assert.equal(writes, 0)
    app.vm.addOption(); app.vm.draft.options[1].label = 'B'; app.vm.draft.reasons = ['用户补充']; app.vm.draft.question = 'draft-only'
    await flush(); assert.ok(app.vm.valid)
    click(app, '忽略候选'); assert.equal(ignored, 1); assert.equal(writes, 0)
    app.unmount(); app = mount(C, { candidate: incomplete, basis: proposal().basis })
    assert.equal(app.vm.draft.question, incomplete.question)
  } finally { app.unmount(); env.restore() }
})

test('discussion: transient extraction, classification and ignore never call ordinary chat or save', async () => {
  const env = environment(), calls = []
  const C = await component('DecisionDiscussion', { ApiError: FakeApiError, DecisionCandidateCard: stub('candidate'), api: async (p,o) => { calls.push([p,o]); return proposal() } }, 'input, messages, result, discuss, ignore')
  let app = mount(C)
  try {
    app.vm.input = '临时决定'; await app.vm.discuss(); await flush()
    assert.match(text(app.root), /用户表达了选择，仍待确认/)
    assert.match(text(app.root), /刷新.*丢失/)
    assert.equal(calls.length, 1); assert.equal(calls[0][0], '/decision-candidates')
    app.vm.ignore(); await flush(); assert.equal(calls.length, 1)
    app.unmount(); app = mount(C); assert.deepEqual(app.vm.messages, []); assert.equal(app.vm.result, null)
  } finally { app.unmount(); env.restore() }
})

test('discussion: late response after unmount discarded and auth failure clears through parent', async () => {
  const env = environment(); let resolve, denied = 0
  const mocks = { ApiError: FakeApiError, DecisionCandidateCard: stub('candidate'), api: () => new Promise(a => { resolve = a }) }
  const C = await component('DecisionDiscussion', mocks, 'input, messages, result, discuss')
  const app = mount(C)
  try {
    app.vm.input = 'private'; const task = app.vm.discuss(); await flush(); app.unmount()
    resolve(proposal()); await task; await flush()
    const D = await component('DecisionDiscussion', { ...mocks, api: async () => { throw new FakeApiError(401) } }, 'input, discuss')
    const other = mount(D, { onUnauthorized: () => denied++ })
    other.vm.input = 'private'; await other.vm.discuss(); assert.equal(denied, 1); other.unmount()
  } finally { env.restore() }
})

test('App: existing chat entry opens temporary panel; leaving chat or logging out unmounts it', async () => {
  const env = environment(), mocks = mocksFor('user')
  mocks.DecisionDiscussion = stub('temporary-discussion')
  const C = await component('App', mocks, 'decisionDiscussion, go, logout, newConversation, openConversation')
  const app = mount(C)
  try {
    await flush(); click(app, '个人决策讨论'); await flush()
    assert.ok(nodes(app.root).some(n => n.type === 'retained-page' && n.props.name === 'temporary-discussion'))
    app.vm.go('library'); await flush()
    assert.ok(!nodes(app.root).some(n => n.type === 'retained-page' && n.props.name === 'temporary-discussion'))
    app.vm.go('chat'); app.vm.decisionDiscussion = true; await flush(); app.vm.newConversation(); await flush()
    assert.equal(app.vm.decisionDiscussion, false)
    app.vm.decisionDiscussion = true; await app.vm.openConversation('saved'); await flush()
    assert.equal(app.vm.decisionDiscussion, false)
    app.vm.decisionDiscussion = true; await flush(); app.vm.logout(); await flush()
    assert.ok(!nodes(app.root).some(n => n.type === 'retained-page' && n.props.name === 'temporary-discussion'))
    assert.equal(app.vm.decisionDiscussion, false)
  } finally { app.unmount(); env.restore() }
})
