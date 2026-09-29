<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const rows = ref<any[]>([])
const editing = ref<Record<number, string>>({})
const saving = ref<number | null>(null)
const msg = ref('')

async function load() { rows.value = await api('/arrivals?line_id=1') }

function localInput(iso: string) {
  // 2026-09-17T07:06:00 -> datetime-local 控件值
  return iso.slice(0, 16)
}

async function save(r: any) {
  const v = editing.value[r.id]
  if (!v) return
  saving.value = r.id
  msg.value = ''
  try {
    await api(`/arrivals/${r.id}`, { method: 'PATCH', body: JSON.stringify({ actual_arrive: v + ':00' }) })
    await load()
    msg.value = `已修改 ${r.trip_no} 在「${r.stop_name}」的到站时刻；下次提交将按新时刻重算。`
  } catch {
    msg.value = '修改失败'
  } finally {
    saving.value = null
  }
}

onMounted(load)
</script>
<template>
  <h1>到站</h1>
  <p class="sub">各班次实际到站记录（修改时刻后，在「串车报告」重新预览/提交即可按新时刻重算）</p>
  <p v-if="msg" class="notice n-ok">{{ msg }}</p>
  <div class="card">
    <table>
      <thead><tr><th>班次</th><th>站序</th><th>站点</th><th>实际到站</th><th>改时刻</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td>{{ r.trip_no }}</td><td>{{ r.stop_seq }}</td><td>{{ r.stop_name }}</td>
          <td>{{ r.actual_arrive }}</td>
          <td>
            <input
              type="datetime-local"
              :value="editing[r.id] ?? localInput(r.actual_arrive)"
              @input="editing[r.id] = ($event.target as HTMLInputElement).value"
            />
            <button class="btn" style="padding:.2rem .5rem;font-size:.75rem;margin-left:.4rem"
                    :disabled="saving === r.id" @click="save(r)">保存</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
