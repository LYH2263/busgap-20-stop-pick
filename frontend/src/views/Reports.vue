<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ApiError, api } from '../api'

interface Worst { gap_min: number; status: string; status_label: string; earlier_trip: string; later_trip: string }
interface StopPreview {
  stop_name: string
  bunching_count: number
  large_gap_count: number
  abnormal_count: number
  worst: Worst | null
}

const lines = ref<any[]>([])
const lineId = ref(1)
const stops = ref<StopPreview[]>([])
const previewed = ref(false)
const previewing = ref(false)
const submitting = ref(false)
const checked = ref<string[]>([])
const reports = ref<any[]>([])
const notice = ref<{ kind: 'ok' | 'warn' | 'err'; text: string } | null>(null)

onMounted(async () => {
  lines.value = await api('/lines')
  if (lines.value.length) lineId.value = lines.value[0].id
  await loadReports()
})

async function loadReports() {
  reports.value = await api(`/reports?line_id=${lineId.value}`)
}

// 只读预览：只查不写，连点任意次，已落库报告行数都不增加
async function preview() {
  notice.value = null
  previewing.value = true
  try {
    const data = await api<{ stops: StopPreview[] }>(`/reports/preview?line_id=${lineId.value}`)
    stops.value = data.stops
    previewed.value = true
    checked.value = []
    await loadReports() // 证明预览前后报告行数一致
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  } finally {
    previewing.value = false
  }
}

function toggle(name: string) {
  const i = checked.value.indexOf(name)
  if (i >= 0) checked.value.splice(i, 1)
  else checked.value.push(name)
}

// 单段提交：不持有任何令牌，提交瞬间由后端按当前到站与阈值重算该站
async function commit() {
  notice.value = null
  // 零勾选 与 勾两个及以上，两种拒绝文案必须可区分；两种情况都不发请求、行数不变
  if (checked.value.length === 0) {
    notice.value = { kind: 'warn', text: '未勾选任何站点：请在下方预览结果中勾选且仅勾选一个站点后再提交。' }
    return
  }
  if (checked.value.length > 1) {
    notice.value = { kind: 'warn', text: `一次只能提交一个站点，当前勾选了 ${checked.value.length} 个（${checked.value.join('、')}），请只保留一个。` }
    return
  }
  submitting.value = true
  try {
    const r = await api<any>('/reports/commit', {
      method: 'POST',
      body: JSON.stringify({ line_id: lineId.value, stop_names: checked.value }),
    })
    checked.value = []
    await preview() // 刷新当前口径（内部会重取报告，证明仍是逐站累积）
    await loadReports()
    notice.value = {
      kind: 'ok',
      text: `已落库：站点「${r.stop_name}」新增报告 #${r.id}，含 ${r.events.length} 条间隔事件（时间轴与建议同步只取该站）。`,
    }
  } catch (e) {
    if (e instanceof ApiError) {
      notice.value = { kind: e.status === 409 ? 'warn' : 'err', text: e.message }
    } else {
      notice.value = { kind: 'err', text: (e as Error).message }
    }
    await loadReports() // 失败（如非法线路）不得抹掉此前已成功的单站报告
  } finally {
    submitting.value = false
  }
}

function badgeClass(s: string) {
  return s === 'bunching' ? 'badge-bad' : s === 'large_gap' ? 'badge-warn' : 'badge-ok'
}
function label(s: string) {
  return s === 'bunching' ? '串车' : s === 'large_gap' ? '大间隔' : '正常'
}
function stripClass(s: string) {
  return s === 'bunching' ? 'bg-bunch' : s === 'large_gap' ? 'bg-large' : ''
}
const reportCount = computed(() => reports.value.length)
</script>
<template>
  <h1>串车报告 · 先预览各站，再只落一站</h1>
  <p class="sub">
    预览只读不落库；提交必须且仅勾选一个站点，后端在提交瞬间按当前到站与阈值重算。
  </p>

  <div class="card">
    <div style="display:flex; gap:.6rem; align-items:center; flex-wrap:wrap">
      <label class="muted" for="line-sel">线路</label>
      <select id="line-sel" v-model.number="lineId" @change="previewed = false; stops = []; checked = []; loadReports()">
        <option v-for="l in lines" :key="l.id" :value="l.id">{{ l.code }} · {{ l.name }}</option>
      </select>
      <button class="btn" :disabled="previewing" @click="preview">
        {{ previewing ? '预览中…' : '① 只读预览各站' }}
      </button>
      <button class="btn" :disabled="submitting || !previewed" @click="commit">
        {{ submitting ? '提交中…' : '② 提交勾选的一个站' }}
      </button>
      <span class="muted">已落库报告：{{ reportCount }} 份（预览不会增加）</span>
    </div>
    <p v-if="notice"
       :style="{ color: notice.kind === 'ok' ? 'var(--bg-ok)' : notice.kind === 'warn' ? 'var(--bg-amber)' : 'var(--bg-red)', marginBottom: 0 }"
       class="muted" style="margin-top:.6rem">
      {{ notice.text }}
    </p>
  </div>

  <div v-if="previewed" class="card">
    <table>
      <thead>
        <tr><th>勾选</th><th>站点</th><th>串车条数</th><th>大间隔条数</th><th>最严重间隔</th><th>状态</th><th>前后班次</th></tr>
      </thead>
      <tbody>
        <tr v-for="s in stops" :key="s.stop_name">
          <td><input type="checkbox" :checked="checked.includes(s.stop_name)" @change="toggle(s.stop_name)" /></td>
          <td>{{ s.stop_name }}</td>
          <td :style="{ color: s.bunching_count ? 'var(--bg-red)' : undefined }">{{ s.bunching_count }}</td>
          <td :style="{ color: s.large_gap_count ? 'var(--bg-amber)' : undefined }">{{ s.large_gap_count }}</td>
          <td>{{ s.worst ? s.worst.gap_min + '′' : '—' }}</td>
          <td>
            <span v-if="s.worst" class="badge" :class="badgeClass(s.worst.status)">{{ s.worst.status_label }}</span>
            <span v-else class="badge badge-ok">正常</span>
          </td>
          <td class="muted">{{ s.worst ? s.worst.earlier_trip + ' → ' + s.worst.later_trip : '—' }}</td>
        </tr>
      </tbody>
    </table>
    <p class="muted" style="margin-bottom:0">
      已勾选 {{ checked.length }} 个站：{{ checked.join('、') || '（无）' }}。零勾选或勾选两个及以上都会被拒绝。
    </p>
  </div>

  <h2 style="font-size:1rem; margin:1.1rem 0 .5rem">已落库的单站报告（报告 / 时间轴 / 建议三处同此口径）</h2>
  <div v-for="r in reports" :key="r.id" class="card">
    <div style="display:flex; justify-content:space-between; gap:.5rem; flex-wrap:wrap">
      <strong>#{{ r.id }} · {{ r.stop_name }}</strong>
      <span class="muted">{{ r.created_at }}</span>
    </div>
    <div class="bg-strip-col" style="min-height:auto; margin-top:.6rem">
      <article v-for="(e, i) in r.events" :key="i" class="bg-gap-strip" :class="stripClass(e.status)"
               style="min-height:auto; flex-basis:150px">
        <header>{{ e.stop_name }}</header>
        <div class="bg-gap-body">
          <div class="bg-gap-val">{{ e.gap_min }}′</div>
          <div>计划 {{ e.planned_headway_min }}′</div>
          <div>{{ e.earlier_trip }} → {{ e.later_trip }}</div>
          <span class="badge" :class="badgeClass(e.status)">{{ label(e.status) }}</span>
        </div>
      </article>
    </div>
  </div>
  <p v-if="!reports.length" class="muted">尚无落库报告——请先预览并勾选一个有异常的站提交。</p>
</template>
