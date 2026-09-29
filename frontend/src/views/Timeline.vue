<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'

interface Mark { trip_no: string; actual_arrive: string; pct: number; status: string }
interface StopTimeline { report_id: number; stop_name: string; marks: Mark[] }

const stops = ref<StopTimeline[]>([])
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const data = await api<{ stops: StopTimeline[] }>('/reports/timeline?line_id=1')
    stops.value = data.stops
  } finally {
    loading.value = false
  }
}
onMounted(load)

function color(status: string) {
  return status === 'bunching' ? 'var(--bg-red)' : status === 'large_gap' ? 'var(--bg-amber)' : 'var(--bg-cyan)'
}
function statusLabel(s: string) {
  return s === 'bunching' ? '串车' : s === 'large_gap' ? '大间隔' : '正常'
}
</script>
<template>
  <h1>时间轴明细</h1>
  <p class="sub">仅展示已落库单站报告的到站分布；提交哪个站，就只为哪个站新增/刷新对应点</p>
  <button class="btn" :disabled="loading" @click="load">刷新时间轴</button>

  <div class="card" style="margin-top:1rem">
    <p v-if="!stops.length" class="muted" style="margin:0">
      尚无已落库站点。请在「串车报告」页预览并勾选一个站提交。
    </p>

    <div v-for="s in stops" :key="s.report_id + s.stop_name" class="tl-stop">
      <h2 style="font-size:.9rem; margin:.2rem 0 .35rem">
        站点「{{ s.stop_name }}」
        <span class="muted" style="font-weight:400">· 报告 #{{ s.report_id }}</span>
      </h2>
      <div class="tl-track">
        <div v-for="m in s.marks" :key="m.trip_no" class="tl-mark"
             :style="{ left: m.pct + '%', background: color(m.status) }"
             :title="`${m.trip_no} ${m.actual_arrive} ${statusLabel(m.status)}`" />
      </div>
      <table>
        <thead><tr><th>班次</th><th>到站时间</th><th>相对位置</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="m in s.marks" :key="m.trip_no">
            <td>{{ m.trip_no }}</td>
            <td>{{ m.actual_arrive }}</td>
            <td>{{ m.pct }}%</td>
            <td>
              <span class="badge"
                    :class="m.status === 'bunching' ? 'badge-bad' : m.status === 'large_gap' ? 'badge-warn' : 'badge-ok'">
                {{ statusLabel(m.status) }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
