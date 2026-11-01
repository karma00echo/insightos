<script setup lang="ts">
import { FileText, Layers3, ScanSearch, ArrowUpRight } from 'lucide-vue-next'
defineEmits<{ draft: [text: string] }>()
const starters = [
  { title: '知识问答', detail: '从资料中找到答案，让每个判断有据可循。', icon: FileText, question: '请概括资料中的核心观点，并列出支持证据。' },
  { title: '多文档比较', detail: '连接不同观点，比较方案与适用条件。', icon: Layers3, question: '请比较资料中不同方案的职责与适用条件。' },
  { title: '证据梳理', detail: '厘清已有依据，发现仍待确认的信息。', icon: ScanSearch, question: '哪些结论已有依据，哪些信息仍然缺失？' },
]
</script>

<template>
  <div class="research-starters" aria-label="研究问题模板">
    <button v-for="(item, index) in starters" :key="item.title" @click="$emit('draft', item.question)">
      <div class="starter-top"><span class="starter-icon" :class="`tone-${index}`"><component :is="item.icon" :size="22" /></span><ArrowUpRight :size="16" /></div>
      <strong>{{ item.title }}</strong><p>{{ item.detail }}</p>
    </button>
  </div>
</template>
