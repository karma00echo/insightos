<script setup lang="ts">
import type { DecisionRevision } from '../decisionTypes'
defineProps<{ revision: DecisionRevision }>()
const basisLabels = { user_input: '用户直接陈述', memory: '个人记忆', user_message: '用户消息', document_chunk: '文档片段' }
function date(value?: string | null) { return value ? new Date(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z').toLocaleString('zh-CN') : '—' }
</script>

<template>
  <section class="decision-content" aria-label="决策版本内容">
    <div class="dc-tags"><span class="dc-tag">版本 {{ revision.version }} · {{ revision.revision_position === 'current' ? '当前版本' : '已被后续修订取代 · 只读' }}</span><span class="dc-tag">{{ revision.status === 'withdrawn' ? '用户已撤销' : '当时决定有效' }}</span><span class="dc-tag">{{ revision.confirmation === 'confirmed' ? '用户已确认' : '确认已撤销' }}</span></div>
    <p class="dc-muted">版本时间：{{ date(revision.created_at) }} · 确认时间：{{ date(revision.confirmed_at) }}</p>
    <div v-if="revision.availability === 'unavailable'" class="dc-notice" role="status">依据已不可用。问题、选择、理由及来源定位已隐藏；不能编辑此内容，仍可撤销或永久删除。</div>
    <template v-else-if="revision.content">
      <p v-if="revision.availability === 'available_original_version'" class="dc-notice">原文档已有新版本，原始依据版本仍可用；引用未自动替换。</p>
      <h2>{{ revision.content.question }}</h2>
      <div class="dc-choice"><span class="eyebrow">最终选择</span><h3>{{ revision.content.options.find(o => o.id === revision.content?.selected_option)?.label }}</h3></div>
      <div class="dc-content-grid">
        <section><h3>备选方案</h3><ul><li v-for="option in revision.content.options" :key="option.id">{{ option.label }}<span v-if="option.id === revision.content.selected_option" class="dc-muted"> · 已选择</span></li></ul></section>
        <section><h3>决策理由</h3><ul><li v-for="(item, i) in revision.content.reasons" :key="i">{{ item }}</li></ul></section>
        <section><h3>关键假设</h3><ul v-if="revision.content.assumptions.length"><li v-for="(item, i) in revision.content.assumptions" :key="i">{{ item }}</li></ul><p v-else class="dc-muted">未填写</p></section>
        <section><h3>复核条件</h3><ul v-if="revision.content.review_conditions.length"><li v-for="(item, i) in revision.content.review_conditions" :key="i">{{ item }}</li></ul><p v-else class="dc-muted">未填写</p></section>
      </div>
      <section class="dc-basis"><h3>决策依据</h3><p class="dc-muted">{{ revision.evidence_status === 'unverified' ? '依据未独立核验。用户确认不表示外部事实已验证。' : '核验状态未知。' }}</p>
        <article v-for="(basis, i) in revision.basis" :key="i" class="dc-source"><strong>{{ basisLabels[basis.type] }}</strong><small>{{ basis.type }}</small>
          <p v-if="basis.type === 'memory'">用户确认的个人陈述，不是外部客观事实；仅引用原修订，不复制记忆正文。</p>
          <p v-if="basis.id">引用：{{ basis.id }}</p><p v-if="basis.revision">原修订：{{ basis.revision }}</p><p v-if="basis.version">原始版本：{{ basis.version }}</p>
          <p v-if="basis.document_id">文档：{{ basis.document_id }}</p><p v-if="basis.page != null">页码：{{ basis.page }}</p><p v-if="basis.locator?.section?.length">章节：{{ basis.locator.section.join(' / ') }}</p><p v-if="basis.locator?.line_start != null">行：{{ basis.locator.line_start }}–{{ basis.locator.line_end ?? basis.locator.line_start }}</p>
        </article>
      </section>
      <p v-if="revision.content.change_reason"><strong>修改原因：</strong>{{ revision.content.change_reason }}</p>
    </template>
  </section>
</template>
