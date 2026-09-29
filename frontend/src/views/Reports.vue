<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const LINE_ID = 1

const trips = ref<any[]>([])
const previewStops = ref<any[]>([])
const reports = ref<any[]>([])
const selected = ref<string[]>([])
const previewing = ref(false)
const committing = ref(false)
const notice = ref<{ kind: 'ok' | 'zero' | 'multi' | 'clean' | 'badline' | 'error'; text: string } | null>(null)

const committedEvents = computed(() => reports.value.flatMap((r) =>
  r.events.map((e: any) => ({ ...e, report_id: r.id, created_at: r.created_at }))))

async function loadReports() {
  reports.value = await api('/reports')
}

async function preview() {
  // 只读预览：任何情况下都不写库，重复点击报告行数保持不变。
  previewing.value = true
  notice.value = null
  try {
    const data = await api(`/reports/preview?line_id=${LINE_ID}`)
    previewStops.value = data.stops || []
  } catch (e: any) {
    notice.value = { kind: 'error', text: errText(e) }
  } finally {
    previewing.value = false
  }
}

function toggle(stop: string) {
  const i = selected.value.indexOf(stop)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(stop)
}

function errText(e: any): string {
  try { return JSON.parse(e.message).detail } catch { return e.message || String(e) }
}

async function commit() {
  notice.value = null
  // 前端先给一版可区分的提示，后端仍会独立校验并返回同一口径的文案。
  if (selected.value.length === 0) {
    notice.value = { kind: 'zero', text: '提交被拒绝：未勾选任何站点。请在预览结果中勾选恰好一个站点后再提交。' }
    return
  }
  if (selected.value.length > 1) {
    notice.value = {
      kind: 'multi',
      text: `提交被拒绝：一次只能勾选一个站点，当前勾选了 ${selected.value.length} 个（${selected.value.join('、')}）。请只保留一个勾选后再提交。`,
    }
    return
  }
  committing.value = true
  try {
    const res = await api('/reports/commit', {
      method: 'POST',
      body: JSON.stringify({ line_id: LINE_ID, stop_names: selected.value }),
    })
    // 提交瞬间由后端按当前到站与阈值重算；成功后三处都从已落库报告读取同一口径。
    await loadReports()
    selected.value = []
    const abnormal = (res.events || []).filter((e: any) => e.status !== 'normal').length
    notice.value = {
      kind: 'ok',
      text: `已落库：站点「${res.stop_name}」单站报告 #${res.id}，含 ${abnormal} 条异常事件。报告、时间轴、建议三处同步为该站口径。`,
    }
  } catch (e: any) {
    const text = errText(e)
    if (text.includes('不存在') || text.includes('非法线路')) {
      notice.value = { kind: 'badline', text }
    } else if (text.includes('未检测到') || text.includes('无异常') || text.includes('拒绝落库')) {
      notice.value = { kind: 'clean', text }
    } else {
      notice.value = { kind: 'error', text }
    }
    // 提交失败：保留此前已成功的单站报告，不用空数据覆盖。
  } finally {
    committing.value = false
  }
}

function stripClass(s: string) {
  return s === 'bunching' ? 'bg-bunch' : s === 'large_gap' ? 'bg-large' : ''
}
function label(s: string) {
  return s === 'bunching' ? '串车' : s === 'large_gap' ? '大间隔' : '正常'
}
function worstLabel(w: any) {
  return w ? `${label(w.status)} · ${w.gap_min}′（${w.earlier_trip} → ${w.later_trip}）` : '无异常'
}

onMounted(async () => {
  trips.value = await api('/trips')
  await loadReports()
  await preview()
})
</script>
<template>
  <h1>串车报告 · 先预览各站，再只落一站</h1>
  <p class="sub">预览只读不落库；勾选恰好一个站点后提交，提交瞬间按当前到站与阈值重算该站。</p>

  <div class="ctl-row">
    <button class="btn" :disabled="previewing" @click="preview">
      {{ previewing ? '预览中…' : '预览各站（只读）' }}
    </button>
    <button class="btn btn-primary" :disabled="committing" @click="commit">
      {{ committing ? '提交中…' : '提交选中站（仅一站）' }}
    </button>
    <span class="muted">已勾选：{{ selected.length ? selected.join('、') : '（未勾选）' }}</span>
  </div>

  <p v-if="notice" class="notice" :class="'n-' + notice.kind">{{ notice.text }}</p>

  <h2 class="sec">各站预览（不落库，重复点击行数不变）</h2>
  <div class="card">
    <table>
      <thead>
        <tr><th>勾选</th><th>站点</th><th>串车条数</th><th>大间隔条数</th><th>最严重一条（间隔 / 状态 / 前后班次）</th></tr>
      </thead>
      <tbody>
        <tr v-for="s in previewStops" :key="s.stop_name">
          <td><input type="checkbox" :checked="selected.includes(s.stop_name)" @change="toggle(s.stop_name)" /></td>
          <td>{{ s.stop_name }}</td>
          <td :class="{ 'cell-bad': s.bunching_count > 0 }">{{ s.bunching_count }}</td>
          <td :class="{ 'cell-warn': s.large_gap_count > 0 }">{{ s.large_gap_count }}</td>
          <td>{{ worstLabel(s.worst) }}</td>
        </tr>
        <tr v-if="!previewStops.length"><td colspan="5" class="muted">点击「预览各站」查看</td></tr>
      </tbody>
    </table>
  </div>

  <h2 class="sec">已落库报告（{{ reports.length }} 份 / {{ committedEvents.length }} 行事件）· 预览不会新增行</h2>
  <div class="bg-strip-col" style="overflow-x:auto">
    <article
      v-for="(e, i) in committedEvents"
      :key="e.report_id + '-' + i"
      class="bg-gap-strip"
      :class="stripClass(e.status)"
    >
      <header>{{ e.stop_name }} <span class="muted" style="font-size:.7rem">#{{ e.report_id }}</span></header>
      <div class="bg-gap-body">
        <div class="bg-gap-val">{{ e.gap_min }}′</div>
        <div>计划 {{ e.planned_headway_min }}′</div>
        <div>{{ e.earlier_trip }} → {{ e.later_trip }}</div>
        <span class="badge" :class="e.status === 'bunching' ? 'badge-bad' : e.status === 'large_gap' ? 'badge-warn' : 'badge-ok'">
          {{ label(e.status) }}
        </span>
      </div>
    </article>
    <p v-if="!committedEvents.length" class="muted">尚未落库任何单站报告</p>
  </div>
</template>
