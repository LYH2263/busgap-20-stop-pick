<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'

const LINE_ID = 1
const tips = ref<any[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    // 建议与报告同一口径：只点名已落库单站报告里的异常，未落库站点（如仅在预览中出现的火车站大间隔）不会出现。
    const data = await api(`/reports/suggestions?line_id=${LINE_ID}`)
    tips.value = data.suggestions || []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
<template>
  <h1>建议</h1>
  <p class="sub">仅针对已落库单站报告中的串车与大间隔给出调班提示</p>
  <button class="btn" :disabled="loading" @click="load">刷新建议</button>
  <div class="card" v-for="(t, i) in tips" :key="i" style="margin-top:.9rem">
    <div>
      <strong>{{ t.stop_name }}</strong> · {{ t.earlier_trip }} → {{ t.later_trip }} · 间隔 {{ t.gap_min }} 分
      <span class="muted" style="font-size:.72rem">（报告 #{{ t.report_id }}）</span>
    </div>
    <p class="muted">{{ t.suggestion }}</p>
  </div>
  <p v-if="!tips.length && !loading" class="muted" style="margin-top:1rem">暂无已落库的异常建议（预览结果不会出现在这里）。</p>
</template>
