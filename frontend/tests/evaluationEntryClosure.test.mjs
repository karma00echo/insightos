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
  globalThis.__entryMocks = mocks
  const file = name === 'App' ? '../src/App.vue' : `../src/components/${name}.vue`
  const source = readFileSync(new URL(file, import.meta.url), 'utf8')
    .replace('</script>', `defineExpose({ ${expose} })\n</script>`)
  const { descriptor } = parse(source)
  let script = compileScript(descriptor, { id: name, inlineTemplate: true }).content
  script = script.replace(/import (\w+) from ['"][^'"]+\.vue['"]/g, (_all, local) => `const ${local} = globalThis.__entryMocks.${local}`)
  let js = ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
  js = js.replaceAll('from "vue"', `from '${vueUrl}'`).replaceAll("from 'vue'", `from '${vueUrl}'`)
    .replaceAll("from 'lucide-vue-next'", `from '${iconsUrl}'`)
    .replace(/import\s+\{([^}]+)\}\s+from\s+['"][^'"]*\/(?:api|uploadQueue)['"];?/g, 'const {$1} = globalThis.__entryMocks')
  return (await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64') + `#${++version}`)).default
}
function mount(Component) {
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
  const app = renderer.createApp({ render: () => h(Component, { ref: value => { vm = value } }) })
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
for (const role of ['user', 'organization-admin', 'platform-admin']) {
  test(`${role}: no evaluation entry, no evaluation requests on restore, navigation, polling or login`, async () => {
    const env = environment(), mocks = mocksFor(role)
    const C = await component('App', mocks, 'go, view, currentView, openConversation, logout, signIn, auth')
    try {
      // A new mount represents refresh with the same old page/cache values.
      for (let refresh = 0; refresh < 2; refresh++) {
        const app = mount(C)
        try {
          await flush()
          assert.equal(app.vm.view, 'chat')
          assert.doesNotMatch(text(app.root), /评测中心|加入评测|添加到评测集|真实质量评测/)
          for (const label of ['AI 对话', '深度研究', '知识库管理', '团队与治理']) assert.ok(text(app.root).includes(label))
          await app.vm.openConversation('saved'); await flush()
          assert.match(text(app.root), /有据回答/)
          assert.match(text(app.root), /查看检索轨迹/)
          assert.match(text(app.root), /证据.md/)
          assert.doesNotMatch(text(app.root), /加入评测/)
          for (const retired of ['evaluations', 'research-evaluations', 'unknown', null]) {
            app.vm.go(retired); await flush(); assert.equal(app.vm.view, 'chat')
            app.vm.currentView = retired; await flush(); assert.equal(app.vm.view, 'chat')
          }
          for (const target of ['library', 'research', 'governance', 'system']) {
            app.vm.go(target); await flush(); assert.equal(app.vm.view, target)
          }
          assert.ok(nodes(app.root).some(n => n.type === 'main' && n.props.class?.includes('system-page')))
          app.vm.go('research'); await flush()
          assert.ok(nodes(app.root).some(n => n.type === 'retained-page' && n.props.name === 'research'))
          for (const callback of env.timers.values()) await callback()
          await flush()
          app.vm.logout(); await flush()
          assert.doesNotMatch(text(app.root), /评测/)
          Object.assign(app.vm.auth, { email: 'test@example.com', password: 'local-test' })
          await app.vm.signIn(); await flush()
          assert.equal(app.vm.view, 'chat')
          assert.ok(mocks.calls.some(([path]) => path === '/auth/login'))
          assert.equal(mocks.calls.filter(([path]) => /evaluat|benchmark/.test(path)).length, 0)
        } finally { app.unmount() }
        assert.equal(env.timers.size, 0)
      }
    } finally { env.restore() }
  })
}
test('platform tasks omit evaluation controls and normalize retired task state', async () => {
  const env = environment(), mocks = mocksFor('platform-admin')
  const C = await component('PlatformPanel', mocks, 'kind, selectedKind, loadJobs')
  const app = mount(C)
  try {
    await flush()
    const options = nodes(app.root).filter(n => n.type === 'option').map(n => n.props.value)
    assert.ok(options.includes('ingestion')); assert.ok(options.includes('cleanup'))
    assert.ok(!options.includes('evaluation')); assert.doesNotMatch(text(app.root), /评测/)
    app.vm.kind = 'evaluation'; await app.vm.loadJobs()
    assert.equal(app.vm.kind, 'ingestion')
    app.vm.selectedKind = 'evaluation'; await app.vm.loadJobs()
    app.vm.kind = 'cleanup'; await app.vm.loadJobs()
    for (const callback of env.timers.values()) await callback()
    assert.equal(mocks.calls.filter(([path]) => /evaluat/.test(path)).length, 0)
    assert.ok(mocks.calls.some(([path]) => path === '/platform/jobs?kind=cleanup'))
  } finally { app.unmount(); env.restore() }
})
