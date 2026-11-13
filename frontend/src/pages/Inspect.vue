<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'
const datasets = ref([])
const goldenIds = ref(new Set())
const datasetId = ref(1)
const busy = ref(false)
const diffBusy = ref(false)
const err = ref('')
const notice = ref('')
const last = ref(null)

const statusLabel = (s) =>
  ({ released: '已发布', inspected: '已入检', golden_failed: '金标差分失败（已撤回）' }[s] || s)
const statusTag = (s) => (s === 'released' ? 'ok' : s === 'golden_failed' ? 'danger' : '')

onMounted(async () => {
  datasets.value = await getJSON('/api/datasets')
  const sets = await getJSON('/api/golden')
  goldenIds.value = new Set(sets.map((g) => g.dataset_id))
  if (datasets.value.length) datasetId.value = datasets.value[0].id
})

async function run() {
  busy.value = true
  err.value = ''
  notice.value = ''
  try {
    last.value = await postJSON('/api/inspect', { dataset_id: Number(datasetId.value) })
    const g = last.value.report.golden || {}
    if (!g.registered) notice.value = '该数据集未登记金标，仅按规则入检签发'
    else if (g.result === 'pass') notice.value = `金标差分通过，批次已发布（签发 ${last.value.released_count} 行）`
    else notice.value = `金标差分失败：已撤回 ${g.rolled_back} 行，批次发布失败`
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function rerunDiff() {
  if (!last.value) return
  diffBusy.value = true
  err.value = ''
  notice.value = ''
  try {
    const out = await postJSON(`/api/batches/${last.value.id}/golden-diff`, {})
    const fresh = await getJSON(`/api/batches/${last.value.id}`)
    last.value = fresh
    notice.value =
      out.result === 'pass'
        ? `重跑差分通过，签发 ${out.batch_released_count} 行`
        : `重跑差分失败，撤回 ${out.rolled_back} 行`
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    diffBusy.value = false
  }
}
</script>
<template>
  <div class="page">
    <header>
      <h1>入检台</h1>
      <p class="muted">装载夹具 → 执行规则 → 隔离失败行 → 金标差分闸门 → 放行通过行到精选区</p>
    </header>
    <div class="panel row">
      <label>数据集
        <select v-model="datasetId">
          <option v-for="d in datasets" :key="d.id" :value="d.id">
            {{ d.id }} · {{ d.name }}{{ goldenIds.has(d.id) ? '（金标）' : '' }}
          </option>
        </select>
      </label>
      <button :disabled="busy" @click="run">{{ busy ? '入检中…' : '开始入检' }}</button>
      <router-link class="btn" to="/golden">金标登记与差分 →</router-link>
    </div>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="notice" class="tag" :class="last && last.report.golden && last.report.golden.result === 'fail' ? 'danger' : 'ok'">
      {{ notice }}
    </p>
    <div class="panel" v-if="last">
      <h3>
        批次 #{{ last.id }}
        <span class="tag" :class="statusTag(last.status)">{{ statusLabel(last.status) }}</span>
      </h3>
      <div class="tiles">
        <div class="tile"><div class="hero-num">{{ last.scanned_count }}</div><div class="muted">扫描</div></div>
        <div class="tile"><div class="hero-num">{{ last.passed_count }}</div><div class="muted">规则通过</div></div>
        <div class="tile"><div class="hero-num">{{ last.quarantined_count }}</div><div class="muted">隔离</div></div>
        <div class="tile"><div class="hero-num">{{ last.released_count }}</div><div class="muted">签发</div></div>
      </div>

      <div v-if="last.golden_diff" class="panel" style="background:#fbfaf5">
        <h4>
          金标差分
          <span class="tag" :class="last.golden_diff.result === 'pass' ? 'ok' : 'danger'">
            {{ last.golden_diff.result === 'pass' ? '通过' : '失败' }}
          </span>
        </h4>
        <div class="tiles">
          <div class="tile"><div class="hero-num">{{ last.golden_diff.golden_count }}</div><div class="muted">金标键</div></div>
          <div class="tile"><div class="hero-num">{{ last.golden_diff.curated_count }}</div><div class="muted">精选键</div></div>
          <div class="tile"><div class="hero-num">{{ last.golden_diff.golden_missing_count }}</div><div class="muted">金标缺</div></div>
          <div class="tile"><div class="hero-num">{{ last.golden_diff.curated_missing_count }}</div><div class="muted">精选缺</div></div>
          <div class="tile"><div class="hero-num">{{ last.golden_diff.field_mismatch_count }}</div><div class="muted">字段不一致</div></div>
          <div class="tile"><div class="hero-num">{{ last.golden_diff.rolled_back_count }}</div><div class="muted">撤回行</div></div>
        </div>
        <div class="row">
          <span class="muted">精选区本批现存：{{ last.curated_batch_count }} 行</span>
          <button :disabled="diffBusy" @click="rerunDiff">{{ diffBusy ? '重跑中…' : '重跑差分' }}</button>
        </div>
      </div>

      <h4>对账</h4>
      <table>
        <thead><tr><th>校验</th><th>结果</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="c in last.report.checks" :key="c.name">
            <td>{{ c.name }}</td>
            <td><span class="tag" :class="c.ok ? 'ok' : 'danger'">{{ c.ok ? '通过' : '失败' }}</span></td>
            <td class="muted">{{ c.detail }}</td>
          </tr>
          <tr v-for="c in (last.report.golden && last.report.golden.checks) || []" :key="'g-' + c.name">
            <td>{{ c.name }}</td>
            <td><span class="tag" :class="c.ok ? 'ok' : 'danger'">{{ c.ok ? '通过' : '失败' }}</span></td>
            <td class="muted">{{ c.detail }}</td>
          </tr>
        </tbody>
      </table>
      <h4>行清单</h4>
      <table>
        <thead><tr><th>#</th><th>键</th><th>处置</th><th>命中</th></tr></thead>
        <tbody>
          <tr v-for="r in last.rows" :key="r.id">
            <td>{{ r.row_no }}</td>
            <td>{{ r.entity_key }}</td>
            <td><span class="tag" :class="r.disposition === 'pass' ? 'ok' : 'danger'">{{ r.disposition }}</span></td>
            <td class="muted">{{ r.rule_hits.map(h => h.name).join('；') || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
