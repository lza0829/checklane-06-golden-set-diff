<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const rows = ref([])
const detail = ref(null)
const err = ref('')
async function load() {
  rows.value = await getJSON('/api/batches')
}
async function show(id) {
  detail.value = await getJSON(`/api/batches/${id}`)
}
const statusTag = (s) => (s === 'released' ? 'ok' : s === 'golden_failed' ? 'danger' : '')
onMounted(async () => {
  try { await load() }
  catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <header>
      <h1>批次</h1>
      <p class="muted">历史入检批次与报告；金标差分失败批次不是成功发布态</p>
    </header>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <table>
        <thead><tr><th>ID</th><th>数据集</th><th>状态</th><th>扫描</th><th>通过</th><th>隔离</th><th>签发</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id" style="cursor:pointer" @click="show(r.id)">
            <td>{{ r.id }}</td>
            <td>{{ r.dataset_id }}</td>
            <td><span class="tag" :class="statusTag(r.status)">{{ r.status }}</span></td>
            <td>{{ r.scanned_count }}</td>
            <td>{{ r.passed_count }}</td>
            <td>{{ r.quarantined_count }}</td>
            <td>{{ r.released_count }}</td>
            <td>{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel" v-if="detail">
      <h3>批次 #{{ detail.id }} 明细</h3>
      <div v-if="detail.golden_diff" class="panel" style="background:#fbfaf5">
        <h4>
          金标差分
          <span class="tag" :class="detail.golden_diff.result === 'pass' ? 'ok' : 'danger'">{{ detail.golden_diff.result }}</span>
        </h4>
        <p class="muted">
          金标缺 {{ detail.golden_diff.golden_missing_count }} · 精选缺 {{ detail.golden_diff.curated_missing_count }} ·
          字段不一致 {{ detail.golden_diff.field_mismatch_count }} · 撤回 {{ detail.golden_diff.rolled_back_count }}
        </p>
        <div v-if="detail.golden_diff.result === 'fail'">
          <h4>精选缺</h4>
          <table v-if="detail.golden_diff.report.categories.curated_missing.count">
            <thead><tr><th>实体键</th><th>金标载荷</th></tr></thead>
            <tbody>
              <tr v-for="i in detail.golden_diff.report.categories.curated_missing.items" :key="i.entity_key">
                <td>{{ i.entity_key }}</td>
                <td><code>{{ JSON.stringify(i.golden_payload) }}</code></td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">无</p>
          <h4>同键字段不一致</h4>
          <table v-if="detail.golden_diff.report.categories.field_mismatch.count">
            <thead><tr><th>实体键</th><th>字段</th><th>金标值</th><th>精选值</th></tr></thead>
            <tbody>
              <tr v-for="i in detail.golden_diff.report.categories.field_mismatch.items" :key="i.entity_key">
                <td>{{ i.entity_key }}</td>
                <td>
                  <div v-for="f in i.fields" :key="f.field"><code>{{ f.field }}</code></div>
                </td>
                <td><div v-for="f in i.fields" :key="f.field">{{ JSON.stringify(f.golden_value) }}</div></td>
                <td><div v-for="f in i.fields" :key="f.field">{{ JSON.stringify(f.curated_value) }}</div></td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">无</p>
          <h4>金标缺</h4>
          <table v-if="detail.golden_diff.report.categories.golden_missing.count">
            <thead><tr><th>实体键</th><th>精选字段</th></tr></thead>
            <tbody>
              <tr v-for="i in detail.golden_diff.report.categories.golden_missing.items" :key="i.entity_key">
                <td>{{ i.entity_key }}</td>
                <td class="muted">{{ i.fields.join('、') }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">无</p>
        </div>
      </div>
      <pre class="muted" style="white-space:pre-wrap">{{ JSON.stringify(detail.report, null, 2) }}</pre>
    </div>
  </div>
</template>
