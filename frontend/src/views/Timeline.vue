<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'

const LINE_ID = 1
const stops = ref<any[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    // 时间轴与报告同一口径：只渲染已落库单站报告涉及的站点与班次点。
    const data = await api(`/reports/timeline?line_id=${LINE_ID}`)
    stops.value = data.stops || []
  } finally {
    loading.value = false
  }
}

function dotColor(m: any) {
  if (m.gap_from_prev?.status === 'bunching') return 'var(--bg-red)'
  if (m.gap_from_prev?.status === 'large_gap') return 'var(--bg-amber)'
  return 'var(--bg-cyan)'
}
function statusLabel(s: string) {
  return s === 'bunching' ? '串车' : s === 'large_gap' ? '大间隔' : '正常'
}

onMounted(load)
</script>
<template>
  <h1>时间轴明细</h1>
  <p class="sub">仅展示已落库单站报告对应的站点；每个点的前后班次与间隔都能与报告逐条对上</p>
  <button class="btn" :disabled="loading" @click="load">刷新时间轴</button>

  <div class="card" v-for="s in stops" :key="s.stop_name" style="margin-top:1rem">
    <h2 class="sec">站点「{{ s.stop_name }}」 <span class="muted" style="font-size:.75rem">报告 #{{ s.report_id }}</span></h2>
    <div class="tl-track">
      <div
        v-for="m in s.marks"
        :key="m.trip_no"
        class="tl-mark"
        :style="{ left: m.pct + '%', background: dotColor(m) }"
        :title="m.trip_no + ' ' + m.actual_arrive + (m.gap_from_prev ? ' 与前车间隔 ' + m.gap_from_prev.gap_min + ' 分' : '（首班，无前车间隔）')"
      />
    </div>
    <table>
      <thead><tr><th>班次</th><th>到站时间</th><th>与前一班间隔</th><th>状态</th><th>相对位置</th></tr></thead>
      <tbody>
        <tr v-for="m in s.marks" :key="m.trip_no">
          <td>{{ m.trip_no }}</td>
          <td>{{ m.actual_arrive }}</td>
          <td>{{ m.gap_from_prev ? m.gap_from_prev.gap_min + ' 分' : '— 首班' }}</td>
          <td>{{ m.gap_from_prev ? statusLabel(m.gap_from_prev.status) : '—' }}</td>
          <td>{{ m.pct }}%</td>
        </tr>
      </tbody>
    </table>
  </div>
  <p v-if="!stops.length && !loading" class="muted" style="margin-top:1rem">时间轴为空：请先在「串车报告」预览并勾选一个站点提交。</p>
</template>
