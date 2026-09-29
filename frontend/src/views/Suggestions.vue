<script setup lang="ts">import { onMounted, ref } from 'vue'
import { api } from '../api'

const tips = ref<any[]>([])
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    tips.value = (await api<{ suggestions: any[] }>('/reports/suggestions?line_id=1')).suggestions
  } finally {
    loading.value = false
  }
}
onMounted(load)

function label(s: string) {
  return s === 'bunching' ? '串车' : '大间隔'
}
</script>
<template>
  <h1>建议</h1>
  <p class="sub">只点名已落库单站报告中的串车与大间隔；未落库的站点不会出现在这里</p>
  <button class="btn" :disabled="loading" @click="load">刷新建议</button>
  <div class="card" v-for="t in tips" :key="t.report_id + t.stop_name + t.earlier_trip + t.later_trip"
       style="margin-top:.9rem">
    <div>
      <span class="badge" :class="t.status === 'bunching' ? 'badge-bad' : 'badge-warn'">{{ label(t.status) }}</span>
      <strong style="margin-left:.4rem">{{ t.stop_name }}</strong>
      <span class="muted"> · {{ t.earlier_trip }} → {{ t.later_trip }} · 间隔 {{ t.gap_min }} 分 · 报告 #{{ t.report_id }}</span>
    </div>
    <p class="muted" style="margin:.5rem 0 0">{{ t.suggestion }}</p>
  </div>
  <p v-if="!tips.length && !loading" class="muted" style="margin-top:1rem">
    暂无已落库的异常建议——请先在「串车报告」页预览并勾选一个有异常的站提交。
  </p>
</template>
