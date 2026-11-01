import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

const source = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8').replaceAll('import.meta.env', '({})')
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
const { streamChat } = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'))
test('stream preserves selected scope, Unicode chunks, draft and final sources', async () => {
  const original = globalThis.fetch
  const final = { answer: '回答 [1]', citations: [{ chunk_id: 'c', document_id: 'd', text: '证据' }] }
  const bytes = new TextEncoder().encode('event: draft\ndata: {"text":"草稿"}\n\nevent: done\ndata: ' + JSON.stringify(final) + '\n\n')
  globalThis.fetch = async (_, options) => {
    assert.deepEqual(JSON.parse(options.body).knowledge_base_ids, ['only-this-kb'])
    return new Response(new ReadableStream({ start(controller) { for (const b of bytes) controller.enqueue(Uint8Array.of(b)); controller.close() } }))
  }
  try {
    let draft = ''
    const result = await streamChat({ knowledge_base_ids: ['only-this-kb'] }, new AbortController().signal, t => { draft += t })
    assert.equal(draft, '草稿'); assert.deepEqual(result, final)
  } finally { globalThis.fetch = original }
})
test('SSE errors are explicit instead of becoming successful answers', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async () => new Response('event: error\ndata: {"status":503,"detail":"模型请求超时"}\n\n')
  try { await assert.rejects(streamChat({}, new AbortController().signal, () => {}), /模型请求超时/) }
  finally { globalThis.fetch = original }
})
