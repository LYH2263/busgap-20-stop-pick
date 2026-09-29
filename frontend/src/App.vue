<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { api } from './api'

const marks = ref<any[]>([])
const stopName = ref('')

onMounted(async () => {
  try {
    // 头部间隔轴与报告同口径：只用已落库单站报告的点（多站按全局时间统一归一化）。
    const data = await api('/reports/timeline?line_id=1')
    const stops: any[] = data.stops || []
    const all = stops.flatMap((s) => s.marks.map((m: any) => ({ ...m, stop_name: s.stop_name })))
    all.sort((a, b) => a.actual_arrive.localeCompare(b.actual_arrive))
    if (all.length) {
      const t0 = new Date(all[0].actual_arrive).getTime()
      const tn = new Date(all[all.length - 1].actual_arrive).getTime()
      const span = Math.max(tn - t0, 1)
      marks.value = all.map((m) => ({
        ...m,
        pct: Math.round(((new Date(m.actual_arrive).getTime() - t0) / span) * 10000) / 100,
      }))
      stopName.value = stops.map((s) => s.stop_name).join('、')
    } else {
      marks.value = []
      stopName.value = ''
    }
  } catch {
    marks.value = []
  }
})
</script>
<template>
  <div class="bg-shell">
    <header class="bg-headway">
      <div class="bg-headway-meta">
        <span class="bg-brand">BusGap · 串车检测</span>
        <span class="bg-stop">发车间隔轴 · {{ stopName || '主站' }}</span>
      </div>
      <div class="bg-rail">
        <div class="bg-rail-ticks">
          <span v-for="t in 11" :key="t">{{ (t - 1) * 10 }}%</span>
        </div>
        <div class="bg-rail-track">
          <div
            v-for="m in marks"
            :key="m.stop_name + '-' + m.trip_no"
            class="bg-bus-dot"
            :class="{ 'bg-bus-tight': m.gap_from_prev && m.gap_from_prev.status !== 'normal' }"
            :style="{ left: m.pct + '%', background: m.gap_from_prev?.status === 'large_gap' ? 'var(--bg-amber)' : undefined }"
            :title="`${m.stop_name} ${m.trip_no} ${m.actual_arrive}`"
          >
            <span class="bg-bus-label">{{ m.trip_no }}</span>
          </div>
        </div>
      </div>
      <nav class="bg-segments">
        <RouterLink to="/timeline">时间轴</RouterLink>
        <RouterLink to="/trips">班次</RouterLink>
        <RouterLink to="/arrivals">到站</RouterLink>
        <RouterLink to="/reports">串车报告</RouterLink>
        <RouterLink to="/lines">线路</RouterLink>
        <RouterLink to="/suggestions">建议</RouterLink>
      </nav>
    </header>
    <div class="bg-deck">
      <RouterView />
    </div>
  </div>
</template>
