import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, defineComponent, h, nextTick, reactive } from 'vue'
import ts from 'typescript'

const require = createRequire(import.meta.url)
const vueUrl = pathToFileURL(require.resolve('vue/dist/vue.runtime.esm-bundler.js')).href
let componentVersion = 0
// v-model's focus guard references DOM constructors even on a custom renderer.
globalThis.Document ??= class Document {}
globalThis.ShadowRoot ??= class ShadowRoot {}
async function component(name, mocks) {
  globalThis.__researchMocks = mocks
  const { descriptor } = parse(readFileSync(new URL(`../src/components/${name}.vue`, import.meta.url), 'utf8'))
  let source = compileScript(descriptor, { id: name, inlineTemplate: true }).content
  source = source.replace(/import\s+ResearchReport\s+from\s+'\.\/ResearchReport.vue'/,
    'const ResearchReport = globalThis.__researchMocks.ResearchReport')
  let js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
  js = js.replaceAll('from "vue"', `from '${vueUrl}'`).replaceAll("from 'vue'", `from '${vueUrl}'`)
  js = js.replace(/import\s+\{([^}]+)\}\s+from\s+['"]\.\.\/api['"];?/,
    'const {$1} = globalThis.__researchMocks')
  return (await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64') + `#${++componentVersion}`)).default
}

function mount(Component, props) {
  const make = type => ({ type, props: {}, children: [], text: '', parent: null, addEventListener() {}, removeEventListener() {}, getRootNode() { return { activeElement: null } } })
  const renderer = createRenderer({
    createElement: make, createText: text => ({ ...make('text'), text }), createComment: text => ({ ...make('comment'), text }),
    insert(child, parent, anchor) { child.parent = parent; const at = parent.children.indexOf(anchor); at < 0 ? parent.children.push(child) : parent.children.splice(at, 0, child) },
    remove(child) { const list = child.parent?.children; if (list) list.splice(list.indexOf(child), 1) },
    setText(node, text) { node.text = text }, setElementText(node, text) { node.text = text; node.children = [] },
    patchProp(node, key, _prev, value) { node.props[key] = value; node[key] = value },
    parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  })
  const state = reactive(props), root = make('root')
  const app = renderer.createApp({ render: () => h(Component, state) })
  app.mount(root)
  return { state, root, unmount: () => app.unmount() }
}
function nodes(root) { return [root, ...root.children.flatMap(nodes)] }
function text(root) { return nodes(root).filter(n => n.type !== 'comment').map(n => n.text).join(' ') }
async function flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); await nextTick() }

test('Chat launch posts once, preserves scope and hands completed report to the reader', async () => {
  const timers = [], calls = [], oldTimeout = globalThis.setTimeout, oldClear = globalThis.clearTimeout
  globalThis.setTimeout = (fn, ms, ...args) => ms === 1200 ? (timers.push(fn), timers.length) : oldTimeout(fn, ms, ...args)
  globalThis.clearTimeout = id => { if (typeof id !== 'number') oldClear(id) }
  const task = { id: 'task-1', question: '研究这个具体问题', status: 'queued', phase: 'queued', plan: {}, steps: [],
    evidence: [], events: [], usage: {}, limits: { tasks: 4, retrievals: 8, seconds: 270, model_calls: 4 }, restricted: false }
  const api = async (path, options) => {
    calls.push([path, options])
    if (path === '/research' && options?.method === 'POST') return { ...task }
    if (path === '/research') return []
    return { ...task, status: 'completed', phase: 'completed', report_workflow: { status: 'saved', report_id: 'report-1' },
      result: { findings: [], analysis: [], gaps: [], conflicts: [], limitations: [], follow_up_questions: [] } }
  }
  const child = defineComponent({ props: ['taskId', 'canGenerate', 'autoReportId'], setup: props => () => h('report-stub', props) })
  const C = await component('DeepResearch', { api, downloadDocument() {}, ResearchReport: child })
  const mounted = mount(C, { knowledgeBases: [], launch: { id: 'fixed-launch-key', question: task.question, scope: ['kb-selected'] }, onLaunched() {} })
  try {
    await flush()
    const posts = calls.filter(([, o]) => o?.method === 'POST')
    assert.equal(posts.length, 1)
    assert.deepEqual(JSON.parse(posts[0][1].body), { question: task.question, knowledge_base_ids: ['kb-selected'], request_id: 'fixed-launch-key', generate_report: true })
    await timers.shift()(); await flush()
    const reader = nodes(mounted.root).find(n => n.type === 'report-stub')
    assert.equal(reader.props.autoReportId, 'report-1'); assert.equal(reader.props.canGenerate, true)
    assert.equal(calls.filter(([, o]) => o?.method === 'POST').length, 1)
  } finally { mounted.unmount(); globalThis.setTimeout = oldTimeout; globalThis.clearTimeout = oldClear }
})

test('automatic report refresh opens AI analysis with pending status and exports saved bytes through the existing endpoint', async () => {
  const calls = [], downloads = []
  const report = { id: 'r1', task_id: 't1', title: '研究报告', version: 1, created_at: '2026-10-30T00:00:00Z', content_sha256: 'hash',
    current_validation: { status: 'VALID', valid: 1, total: 1, notice: '仍需人工复核', checks: [] },
    content: { generation: { method: 'llm_evidence_writing', model_calls: 1, model: 'mock-model' }, evaluations: [],
      validation: { status: 'VALID', valid: 1, total: 1 }, evidence: [],
      sections: [{ id: 'discussion', title: '综合分析', paragraphs: [], claim_ids: ['w1'] }],
      claims: [{ id: 'w1', text: '证据限定范围内的机制分析内容。', quality_status: 'NEEDS_REVIEW', evidence_ids: ['e1'], source_pointer: '/report_writing/discussion/0' }] } }
  let ready = false
  const api = async (path, options) => { calls.push([path, options]); return path.includes('?') ? (ready ? [report] : []) : report }
  const C = await component('ResearchReport', { api, downloadDocument() {}, downloadReport: async (...args) => downloads.push(args) })
  const mounted = mount(C, { taskId: 't1', canGenerate: false })
  try {
    await flush(); ready = true; mounted.state.autoReportId = 'r1'; mounted.state.canGenerate = true; await flush()
    assert.match(text(mounted.root), /AI证据报告写作/)
    assert.match(text(mounted.root), /证据限定范围内的机制分析内容/)
    assert.match(text(mounted.root), /研究质量待人工复核/)
    const button = nodes(mounted.root).find(n => n.type === 'button' && text(n).includes('导出 Markdown'))
    await button.props.onClick(); assert.deepEqual(downloads, [['r1', 1]])
    assert.equal(calls.filter(([, o]) => o?.method === 'POST').length, 0)
  } finally { mounted.unmount() }
})

test('failed research separates prior gap validation from terminal error and usage from attempts', async () => {
  const diagnostic = { code: 'MODEL_REQUEST_FAILED', message: '最终调用失败', stage: 'synthesizing', error_type: 'ProviderError',
    task_id: 'failed-history', at: '2026-10-30T14:52:11Z', checks: [], context: { evidence_groups: 6 } }
  const task = { id: 'failed-history', question: '已保存的失败研究', status: 'failed', phase: 'failed', plan: {}, steps: [],
    evidence: [], events: [], usage: { model_calls: 3, input_tokens: 2342, output_tokens: 1046 }, restricted: false,
    limits: { tasks: 4, retrievals: 8, seconds: 270, model_calls: 4 },
    error: '最终调用失败', error_details: diagnostic,
    prior_error_details: { ...diagnostic, code: 'EVIDENCE_GAP', message: '子问题仍有证据缺口', stage: 'verifying',
      checks: [{ rule: 'gap', path: ['analysis', 0] }] },
    call_observations: { attempts: 3, responses_received: 2, calls_with_usage: 2, network_send_count: null } }
  const calls = []
  const api = async (path, options) => { calls.push([path, options]); return path === '/research' ? [task] : task }
  const child = defineComponent({ setup: () => () => h('report-stub') })
  const C = await component('DeepResearch', { api, downloadDocument() {}, ResearchReport: child })
  const mounted = mount(C, { knowledgeBases: [] })
  try {
    await flush()
    assert.match(text(mounted.root), /此前核验失败 · EVIDENCE_GAP/)
    assert.match(text(mounted.root), /失败详情 · MODEL_REQUEST_FAILED/)
    assert.match(text(mounted.root), /analysis.0/)
    assert.match(text(mounted.root), /模型调用尝试 3 次/)
    assert.match(text(mounted.root), /获得用量 2 次/)
    assert.equal(calls.filter(([, o]) => o?.method === 'POST').length, 0)
  } finally { mounted.unmount() }
})
