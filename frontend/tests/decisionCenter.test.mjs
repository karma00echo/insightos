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


const draft = () => ({ question: '私有决策问题', options: [{ id: 'a', label: '方案 A' }, { id: 'b', label: '方案 B' }], selected_option: 'a', reasons: ['私有理由'], assumptions: ['成立假设'], review_conditions: ['条件改变'] })
const revision = (version = 1, extra = {}) => ({ version, status: 'active', confirmation: 'confirmed', revision_position: 'current', availability: 'available', evidence_status: 'unverified', created_at: '2026-11-17T01:00:00', confirmed_at: '2026-11-17T01:00:00', content: { ...draft(), change_reason: '修订原因' }, basis: [{ type: 'user_input' }, { type: 'memory', id: 'memory-id', revision: 2 }, { type: 'document_chunk', id: 'chunk-id', version: 'original-version', document_id: 'doc-id', page: 3, locator: { section: ['标题'], line_start: 4, line_end: 6 } }], ...extra })
const decision = (version = 1, extra = {}) => ({ id: 'd1', current_version: version, status: 'active', created_at: '2026-11-17T01:00:00', updated_at: '2026-11-17T02:00:00', revision: revision(version), ...extra })
const page = items => ({ items, offset: 0, limit: 20 })
class FakeApiError extends Error { constructor(status) { super('DO-NOT-LEAK private server detail'); this.status = status } }
const click = (app, label) => { const n = nodes(app.root).find(n => n.type === 'button' && text(n).includes(label)); assert.ok(n, label); return n.props.onClick() }
function browserEnvironment() {
  const env = environment(), old = globalThis.document, listeners = new Map()
  globalThis.document = { hidden: false, addEventListener: (k, f) => listeners.set(k,f), removeEventListener: k => listeners.delete(k) }
  return { hide(value) { document.hidden = value; listeners.get('visibilitychange')?.() }, restore() { globalThis.document = old; env.restore() } }
}
async function detailComponents(api) {
  const mocks = { api, ApiError: FakeApiError }
  mocks.DecisionContent = await component('DecisionContent', mocks, '')
  mocks.DecisionEditor = await component('DecisionEditor', mocks, 'draft, reason, approved, submit')
  const C = await component('DecisionDetail', mocks, 'current, viewed, history, editing, pending, conflict, editVersion, action, approved, load, startEdit, save, send, confirmAction, rebase')
  return { C, mocks }
}
function server() {
  let value = decision(), failure = null
  const calls = []
  const api = async (p, o = {}) => {
    calls.push([p,o])
    if (o.method) { if (failure !== null) throw new FakeApiError(failure); value = decision(value.current_version + 1, o.method === 'POST' ? { status: 'withdrawn', revision: revision(value.current_version + 1, { status: 'withdrawn', confirmation: 'revoked', confirmed_at: null }) } : {}); return value }
    if (p.includes('/history?')) return page([value.revision, revision(1, { revision_position: 'superseded' })])
    if (p.includes('/versions/')) return revision(1, { revision_position: 'superseded' })
    return structuredClone(value)
  }
  return { api, calls, set value(v) { value = v }, set failure(v) { failure = v } }
}

test('content: real structured fields, source locators, confirmation and original version semantics', async () => {
  const C = await component('DecisionContent', {}, '')
  const app = mount(C, { revision: revision(1, { availability: 'available_original_version', revision_position: 'superseded' }) })
  try { const t = text(app.root); for (const s of ['私有决策问题', '方案 A', '私有理由', '成立假设', '条件改变', '修订原因', '原始依据版本仍可用', '用户确认的个人陈述', 'original-version', '已被后续修订取代', '未独立核验']) assert.ok(t.includes(s), s) } finally { app.unmount() }
})

test('content: unavailable masks even inconsistent protected fields in a response', async () => {
  const C = await component('DecisionContent', {}, '')
  const app = mount(C, { revision: revision(1, { availability: 'unavailable' }) })
  try { assert.doesNotMatch(text(app.root), /私有|memory-id|original-version|修订原因/); assert.match(text(app.root), /正文|隐藏/) } finally { app.unmount() }
})

test('editor: no unconfirmed submission, final fields, change reason and renewed approval after changes', async () => {
  let sent = null
  const C = await component('DecisionEditor', {}, 'draft, reason, approved, submit')
  const app = mount(C, { content: draft(), locked: false, blocked: false, restored: false, onConfirm: (d,r) => { sent = [d,r] } })
  try {
    app.vm.reason = '用户修改'; app.vm.submit(); assert.equal(sent,null)
    app.vm.approved = true; app.vm.draft.question = '最终编辑'; assert.equal(app.vm.approved,false)
    app.vm.approved = true; app.vm.submit(); assert.equal(sent[0].question,'最终编辑'); assert.equal(sent[1],'用户修改')
  } finally { app.unmount() }
})

test('detail: explicit edit uses expected_version and confirmed true, inherits basis, reloads server current', async () => {
  const env = browserEnvironment(), s = server(), { C } = await detailComponents(s.api), app = mount(C, { decisionId: 'd1' })
  try {
    await flush(); click(app,'编辑决定'); await flush(); app.vm.save(draft(),'用户确认修改'); await flush()
    const writes = s.calls.filter(([,o]) => o.method); assert.equal(writes.length,1)
    const body = JSON.parse(writes[0][1].body); assert.equal(body.expected_version,1); assert.equal(body.confirmed,true); assert.ok(body.request_id); assert.deepEqual(body.basis,[])
    assert.equal(app.vm.current.current_version,2); assert.equal(app.vm.editing,null)
    assert.ok(s.calls.filter(([p,o]) => p === '/decisions/d1' && !o.method).length >= 2)
  } finally { app.unmount(); env.restore() }
})

for (const status of [409,422]) test(`detail: ${status} keeps draft, reads current and requires user rebase`, async () => {
  const env = browserEnvironment(), s = server(), { C } = await detailComponents(s.api), app = mount(C, { decisionId: 'd1' })
  try {
    await flush(); app.vm.startEdit(); app.vm.editing.question = '保留草稿'; s.value = decision(2); s.failure = status
    app.vm.save(draft(),'修改原因'); await flush(); assert.equal(app.vm.editing.question,'保留草稿'); assert.equal(app.vm.conflict,true); assert.equal(app.vm.current.current_version,2)
    app.vm.save(draft(),'不应自动覆盖'); await flush(); assert.equal(s.calls.filter(([,o]) => o.method).length,1)
    click(app,'已查看当前版本'); await flush(); assert.equal(app.vm.editVersion,2); assert.equal(app.vm.conflict,false); assert.doesNotMatch(text(app.root),/DO-NOT-LEAK/)
  } finally { app.unmount(); env.restore() }
})

for (const status of [0,503]) test(`detail: uncertain ${status} locks exact request, double click and retry do not change key`, async () => {
  const env = browserEnvironment(), s = server(), { C } = await detailComponents(s.api), app = mount(C, { decisionId: 'd1' })
  try {
    await flush(); s.failure = status
    app.vm.startEdit(); app.vm.save(draft(),'修改'); app.vm.save(draft(),'双击'); await flush()
    const first = s.calls.find(([,o]) => o.method)[1].body
    assert.ok(app.vm.pending); await app.vm.send(); await flush()
    const writes = s.calls.filter(([,o]) => o.method); assert.equal(writes.length,2); assert.equal(writes[1][1].body,first)
    s.failure = null; await app.vm.send(); await flush(); assert.equal(app.vm.pending,null)
  } finally { app.unmount(); env.restore() }
})

test('history: version endpoint, metadata-only history, current lifecycle not confused with superseded', async () => {
  const env = browserEnvironment(), s = server(), { C } = await detailComponents(s.api), app = mount(C, { decisionId: 'd1' })
  try {
    s.value = decision(2); await flush(); await app.vm.load(1); await flush()
    assert.ok(s.calls.some(([p]) => p === '/decisions/d1/versions/1')); assert.equal(app.vm.viewed.revision_position,'superseded'); assert.equal(app.vm.current.status,'active')
    assert.ok(app.vm.history.every(r => !r.content && !r.basis)); assert.match(text(app.root),/只读/); assert.equal(app.vm.editing,null)
  } finally { app.unmount(); env.restore() }
})

test('revoke and permanent delete require second confirmation; deletion clears held private state', async () => {
  const env = browserEnvironment(), s = server(), { C } = await detailComponents(s.api); let deleted = 0
  const app = mount(C, { decisionId: 'd1', onDeleted: () => deleted++ })
  try {
    await flush(); click(app,'撤销决定'); app.vm.confirmAction(); assert.equal(s.calls.filter(([,o]) => o.method).length,0)
    app.vm.approved = true; app.vm.confirmAction(); await flush(); assert.equal(app.vm.current.status,'withdrawn'); assert.ok(s.calls.some(([p]) => p.endsWith('/revoke')))
    click(app,'永久删除'); await flush(); assert.match(text(app.root),/全部历史业务内容/); app.vm.approved = true; app.vm.confirmAction(); await flush()
    assert.equal(deleted,1); assert.equal(app.vm.current,null); assert.equal(app.vm.viewed,null); assert.equal(app.vm.editing,null); assert.equal(app.vm.pending,null); assert.deepEqual(app.vm.history,[]); assert.doesNotMatch(text(app.root),/私有/)
  } finally { app.unmount(); env.restore() }
})

test('source revoked: refresh clears editor and every protected field while actions remain', async () => {
  const env = browserEnvironment(), s = server(), { C } = await detailComponents(s.api), app = mount(C, { decisionId: 'd1' })
  try {
    await flush(); app.vm.startEdit(); s.value = decision(1,{ revision: revision(1,{ availability:'unavailable' }) }); await app.vm.load(); await flush()
    assert.equal(app.vm.editing,null); assert.equal(app.vm.current.revision.content,undefined); assert.doesNotMatch(text(app.root),/私有|memory-id/)
    assert.ok(nodes(app.root).some(n => n.type==='button' && text(n)==='永久删除')); assert.ok(nodes(app.root).some(n => n.type==='button' && text(n)==='撤销决定'))
    await app.vm.load(1); await flush(); // Version visibility is independently decided by the backend, not cached history.
    assert.ok(s.calls.some(([p]) => p.includes('/versions/1')))
  } finally { app.unmount(); env.restore() }
})

for (const status of [401,403,404]) test(`detail: ${status} clears private data and uses sanitized feedback`, async () => {
  const env = browserEnvironment(); let denied=0
  const { C } = await detailComponents(async () => { throw new FakeApiError(status) }), app = mount(C, { decisionId:'d1', onUnauthorized:()=>denied++ })
  try { await flush(); assert.equal(app.vm.current,null); assert.equal(denied,status===401?1:0); assert.doesNotMatch(text(app.root),/DO-NOT-LEAK/); assert.match(text(app.root),/登录|无权|不存在/) } finally { app.unmount(); env.restore() }
})

test('detail: out-of-order reads, deletion during read and unmount cannot restore private state', async () => {
  const env = browserEnvironment(), s = server(); let resolveOld
  let hold=false
  const { C } = await detailComponents((p,o) => hold && p==='/decisions/d1' && !o.method ? new Promise(r=>{resolveOld=r}) : s.api(p,o)), app=mount(C,{ decisionId:'d1' })
  try {
    await flush(); hold=true; const old=app.vm.load(1); await flush(); hold=false; await app.vm.load(); await flush()
    app.vm.action='delete'; app.vm.approved=true; app.vm.confirmAction(); await flush(); resolveOld(decision()); await old; await flush(); assert.equal(app.vm.current,null)
    hold=true; const late=app.vm.load(); await flush(); const vm=app.vm; app.unmount(); resolveOld(decision()); await late; assert.equal(vm.current,null)
  } finally { env.restore() }
})

test('center: filter/pagination/refetch, hidden-page clearing, late list after selecting detail', async () => {
  const env=browserEnvironment(), calls=[]; let resolveList, hold=false
  const mocks={ApiError:FakeApiError, DecisionDetail:stub('detail'), api:async(p,o)=>{calls.push(p); if(hold) return new Promise(r=>{resolveList=r}); return page(Array.from({length:20},(_,i)=>decision(1,{id:`d${i}`})))}}
  const C=await component('DecisionCenter',mocks,'items,selected,filter,offset,load,open'), app=mount(C)
  try {
    await flush(); assert.equal(app.vm.items.length,20); await app.vm.load(20); assert.ok(calls.at(-1).includes('offset=20'))
    app.vm.filter='withdrawn'; await app.vm.load(); assert.ok(calls.at(-1).includes('status=withdrawn'))
    hold=true; const pending=app.vm.load(); await flush(); app.vm.open('d2'); resolveList(page([decision()])); await pending; await flush(); assert.equal(app.vm.selected,'d2'); assert.deepEqual(app.vm.items,[])
    env.hide(true); await flush(); assert.equal(app.vm.selected,''); assert.doesNotMatch(text(app.root),/私有/); hold=false; env.hide(false); await flush(); assert.equal(app.vm.items.length,20)
  } finally { app.unmount(); env.restore() }
})

test('App: Chat-first nav, saved actual ID handoff, leave/logout/remount load through auth', async () => {
  const env=browserEnvironment(), mocks=mocksFor('user'); mocks.DecisionDiscussion=stub('discussion'); mocks.DecisionCenter=stub('center')
  const C=await component('App',mocks,'view,decisionId,openDecision,go,logout,signIn'), app=mount(C)
  try {
    await flush(); assert.equal(app.vm.view,'chat'); click(app,'我的决策'); await flush(); assert.equal(app.vm.view,'decisions')
    app.vm.go('chat'); app.vm.openDecision('actual-server-id'); await flush(); assert.equal(app.vm.decisionId,'actual-server-id'); assert.ok(nodes(app.root).some(n=>n.type==='retained-page' && n.props['initial-decision-id']==='actual-server-id'))
    app.vm.logout(); await flush(); assert.equal(app.vm.decisionId,''); assert.ok(!nodes(app.root).some(n=>n.props.name==='center')); await app.vm.signIn(); await flush(); assert.equal(app.vm.view,'chat')
  } finally { app.unmount(); env.restore() }
})

test('T05: success link emits only actual server ID after confirmed save', async () => {
  const env=browserEnvironment(); let id='', resolve
  const C=await component('DecisionCandidateCard',{ApiError:FakeApiError,api:()=>new Promise(r=>{resolve=r})},'approved,save')
  const app=mount(C,{candidate:draft(),basis:[{type:'user_input'}],onOpenDecision:v=>{id=v}})
  try { assert.doesNotMatch(text(app.root),/查看我的决定/); app.vm.approved=true; const task=app.vm.save(); await flush(); assert.equal(id,''); resolve(decision()); await task; await flush(); click(app,'查看我的决定'); assert.equal(id,'d1') } finally { app.unmount(); env.restore() }
})

test('timeout: abort after 30s retains exactly the same edit body for retry', async () => {
  const env=browserEnvironment(), s=server(), timers=[]; globalThis.setTimeout=(fn,ms)=>{timers.push({fn,ms});return timers.length}
  let retry=false
  const {C}=await detailComponents((p,o={})=>o.method && !retry ? new Promise((_resolve,reject)=>o.signal.addEventListener('abort',()=>reject(new FakeApiError(0)))) : s.api(p,o))
  const app=mount(C,{decisionId:'d1'})
  try {
    await flush(); app.vm.startEdit(); app.vm.save(draft(),'超时测试'); await flush(); const body=app.vm.pending.body
    assert.equal(timers.at(-1).ms,30000); timers.at(-1).fn(); await flush(); assert.equal(app.vm.pending.body,body)
    retry=true; await app.vm.send(); await flush(); assert.equal(s.calls.find(([,o])=>o.method)[1].body,body)
  } finally { app.unmount(); env.restore() }
})

test('mutation completion after logout/unmount cannot repopulate detail or emit success', async () => {
  const env=browserEnvironment(),s=server(); let resolveWrite, deleted=0
  const {C}=await detailComponents((p,o={})=>o.method ? new Promise(r=>{resolveWrite=r}) : s.api(p,o)),app=mount(C,{decisionId:'d1',onDeleted:()=>deleted++})
  try {
    await flush(); app.vm.action='delete'; app.vm.approved=true; app.vm.confirmAction(); await flush(); const vm=app.vm; app.unmount(); resolveWrite(undefined); await flush()
    assert.equal(deleted,0); assert.equal(vm.current,null); assert.equal(vm.pending,null); assert.equal(vm.editing,null)
  } finally { env.restore() }
})

test('all history reads apply current source masking, never reuse previously displayed version content', async () => {
  const env=browserEnvironment(),s=server(); let revoked=false
  const {C}=await detailComponents((p,o)=>p.includes('/versions/') && revoked ? Promise.resolve(revision(1,{availability:'unavailable'})) : s.api(p,o)),app=mount(C,{decisionId:'d1'})
  try {
    await flush(); await app.vm.load(1); await flush(); assert.match(text(app.root),/私有理由/)
    revoked=true; s.value=decision(1,{revision:revision(1,{availability:'unavailable'})}); await app.vm.load(1); await flush()
    assert.equal(app.vm.viewed.content,undefined); assert.equal(app.vm.current.revision.content,undefined); assert.doesNotMatch(text(app.root),/私有|memory-id|chunk-id/)
  } finally { app.unmount(); env.restore() }
})

test('list: failures clear stale rows, retry and empty states; unavailable rows hold only metadata', async () => {
  const env=browserEnvironment(); let response=page([decision()]),failure=false
  const C=await component('DecisionCenter',{ApiError:FakeApiError,DecisionDetail:stub('detail'),api:async()=>{if(failure)throw new FakeApiError(503);return response}},'items,load')
  const app=mount(C)
  try {
    await flush(); assert.match(text(app.root),/私有决策/); failure=true; await app.vm.load(); await flush(); assert.deepEqual(app.vm.items,[]); assert.doesNotMatch(text(app.root),/私有|DO-NOT-LEAK/); assert.match(text(app.root),/重试加载/)
    failure=false; response=page([]); click(app,'重试加载'); await flush(); assert.match(text(app.root),/留住你确认过的选择/)
    response=page([decision(1,{revision:revision(1,{availability:'unavailable'})})]); await app.vm.load(); await flush(); assert.equal(app.vm.items[0].revision.content,undefined); assert.doesNotMatch(text(app.root),/私有/)
  } finally { app.unmount(); env.restore() }
})

test('center: remount always reloads authenticated data, no private storage restoration', async () => {
  const env=browserEnvironment();let reads=0
  const C=await component('DecisionCenter',{ApiError:FakeApiError,DecisionDetail:stub('detail'),api:async()=>{reads++;return page([decision()])}},'items'), app=mount(C)
  try {await flush();assert.equal(reads,1);app.unmount();const other=mount(C);await flush();assert.equal(reads,2);other.unmount()}finally{env.restore()}
})
