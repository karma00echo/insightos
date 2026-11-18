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
  globalThis.__searchMocks = mocks
  const file = name === 'App' ? '../src/App.vue' : `../src/components/${name}.vue`
  const source = readFileSync(new URL(file, import.meta.url), 'utf8')
    .replace('</script>', `defineExpose({ ${expose} })\n</script>`)
  const { descriptor } = parse(source)
  let script = compileScript(descriptor, { id: name, inlineTemplate: true }).content
  script = script.replace(/import (\w+) from ['"][^'"]+\.vue['"]/g, (_all, local) => `const ${local} = globalThis.__searchMocks.${local}`)
  script = script.replace(/import ['"][^'"]+\.css['"];?/g, '')
  let js = ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
  js = js.replaceAll('from "vue"', `from '${vueUrl}'`).replaceAll("from 'vue'", `from '${vueUrl}'`)
    .replaceAll("from 'lucide-vue-next'", `from '${iconsUrl}'`)
    .replace(/import\s+\{([^}]+)\}\s+from\s+['"][^'"]*\/(?:api|uploadQueue)['"];?/g, 'const {$1} = globalThis.__searchMocks')
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



class Err extends Error { constructor(status) { super('PRIVATE exception'); this.status=status } }
const draft={question:'Saved question',options:[{id:'a',label:'A'},{id:'b',label:'B'}],selected_option:'a',reasons:['reason'],assumptions:[],review_conditions:[]}
const item={id:'actual-id',status:'active',current_version:2,updated_at:'2026-11-17T00:00:00',revision:{content:draft,availability:'available'}}
const page={items:[item]}
test('center search: private query only in POST body and pagination preserves query/status',async()=>{
 globalThis.document={addEventListener(){},removeEventListener(){}};const calls=[]
 const C=await component('DecisionCenter',{ApiError:Err,DecisionDetail:stub('detail'),api:async(p,o)=>{calls.push([p,o]);return page}},'query,searchQuery,filter,load')
 const app=mount(C)
 try{await flush();app.vm.searchQuery='private query';app.vm.filter='withdrawn';await app.vm.load(20);const [p,o]=calls.at(-1);assert.equal(p,'/decisions/search');assert.equal(o.method,'POST');assert.deepEqual(JSON.parse(o.body),{query:'private query',offset:20,limit:20,status:'withdrawn'})}finally{app.unmount();delete globalThis.document}
})
test('picker: selected reference holds actual id/version only and late search cannot expose rows',async()=>{
 let selected,resolve;const C=await component('DecisionReferencePicker',{ApiError:Err,api:async()=>page},'items,search,select')
 const app=mount(C,{onSelect:r=>selected=r});await app.vm.search();app.vm.select(item);assert.deepEqual(selected,{id:'actual-id',version:2});assert.deepEqual(app.vm.items,[]);app.unmount()
 const D=await component('DecisionReferencePicker',{ApiError:Err,api:()=>new Promise(r=>resolve=r)},'items,search');const late=mount(D);const task=late.vm.search();const vm=late.vm;late.unmount();resolve(page);await task;assert.deepEqual(vm.items,[])
})
test('discussion: selected references sent as metadata; previous reply never reinjected; failure clears protected reply',async()=>{
 const calls=[];let fail=false;const C=await component('DecisionDiscussion',{ApiError:Err,DecisionReferencePicker:stub('picker'),DecisionCandidateCard:stub('candidate'),api:async(p,o)=>{calls.push(JSON.parse(o.body));if(fail)throw new Err(422);return {classification:'decision',readiness:'no_decision',candidate:null,reply:'PRIVATE referenced reply',missing_fields:[],basis:[]}}},'selectReference,input,discuss,messages,result,references')
 const app=mount(C);try{app.vm.selectReference({id:'actual-id',version:2});app.vm.input='question one';await app.vm.discuss();app.vm.input='question two';await app.vm.discuss();assert.equal(calls[1].messages.length,1);assert.deepEqual(calls[1].decision_references,[{id:'actual-id',version:2}]);fail=true;app.vm.input='question three';await app.vm.discuss();assert.deepEqual(app.vm.messages,[]);assert.equal(app.vm.result,null)}finally{app.unmount()}
})

test('hidden discussion clears picker, reference reply and in-flight response even after visibility resumes',async()=>{
 let listener,resolve;globalThis.document={hidden:false,addEventListener(_k,f){listener=f},removeEventListener(){}}
 const C=await component('DecisionDiscussion',{ApiError:Err,DecisionReferencePicker:stub('picker'),DecisionCandidateCard:stub('candidate'),api:()=>new Promise(r=>resolve=r)},'selectReference,input,discuss,messages,result,references')
 const app=mount(C);try{app.vm.selectReference({id:'actual-id',version:2});app.vm.input='question';const task=app.vm.discuss();document.hidden=true;listener();document.hidden=false;listener();resolve({classification:'decision',readiness:'no_decision',reply:'PRIVATE late reply',candidate:null,missing_fields:[]});await task;await flush();assert.deepEqual(app.vm.references,[]);assert.deepEqual(app.vm.messages,[]);assert.equal(app.vm.result,null);assert.doesNotMatch(text(app.root),/PRIVATE/)}finally{app.unmount();delete globalThis.document}
})
