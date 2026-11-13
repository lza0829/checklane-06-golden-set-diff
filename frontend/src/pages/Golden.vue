<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON, putJSON } from '../api'

const datasets = ref([])
const goldenSets = ref([])
const diffs = ref([])
const datasetId = ref(null)
const fixturePath = ref('')
const busy = ref(false)
const err = ref('')
const notice = ref('')
const current = ref(null)
const runningBatchId = ref(null)
const lastRun = ref(null)

const registeredIds = computed(() => new Set(goldenSets.value.map((g) => g.dataset_id)))
const datasetName = (id) => datasets.value.find((d) => d.id === id)?.name || `#${id}`

async function load() {
  datasets.value = await getJSON('/api/datasets')
  goldenSets.value = await getJSON('/api/golden')
  diffs.value = await getJSON('/api/golden-diffs')
  if (datasetId.value === null && datasets.value.length) datasetId.value = datasets.value[0].id
  await loadCurrent()
}

async function loadCurrent() {
  if (datasetId.value === null) return
  try {
    current.value = await getJSON(`/api/golden/${datasetId.value}`)
    fixturePath.value = current.value.fixture_path
  } catch (e) {
    current.value = null
    if (String(e.message || '').includes('404') || String(e.message || '').includes('not registered')) {
      fixturePath.value ||= ''
    }
  }
}

async function chooseDataset() {
  lastRun.value = null
  notice.value = ''
  err.value = ''
  await loadCurrent()
}

async function register() {
  busy.value = true
  err.value = ''
  notice.value = ''
  try {
    const out = await putJSON(`/api/golden/${datasetId.value}`, { fixture_path: fixturePath.value.trim() })
    current.value = out
    notice.value = `已登记金标：${out.fixture_path}，${out.row_count} 行`
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function runDiff(batchId) {
  runningBatchId.value = batchId
  err.value = ''
  notice.value = ''
  try {
    lastRun.value = await postJSON(`/api/batches/${batchId}/golden-diff`, {})
    const result = lastRun.value.result
    notice.value =
      result === 'pass'
        ? `批次 #${batchId} 差分通过，已发布；签发 ${lastRun.value.batch_released_count} 行`
        : `批次 #${batchId} 差分失败，已撤回 ${lastRun.value.rolled_back} 行`
    diffs.value = await getJSON('/api/golden-diffs')
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    runningBatchId.value = null
  }
}

onMounted(async () => {
  try {
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div class="page">
    <header>
      <h1>金标差分</h1>
      <p class="muted">登记金标夹具 → 入检签发后自动按实体键差分 → 任一差异则批次发布失败并撤回精选区</p>
    </header>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="notice" class="tag ok">{{ notice }}</p>

    <div class="panel">
      <h3>登记金标</h3>
      <div class="row">
        <label>数据集
          <select v-model="datasetId" @change="chooseDataset">
            <option v-for="d in datasets" :key="d.id" :value="d.id">
              {{ d.id }} · {{ d.name }}{{ registeredIds.has(d.id) ? '（已有金标）' : '' }}
            </option>
          </select>
        </label>
        <label style="flex:1; min-width:280px">金标夹具路径
          <input v-model="fixturePath" placeholder="如 refunds_golden.jsonl 或绝对路径" style="width:100%" />
        </label>
        <button :disabled="busy || !fixturePath.trim()" @click="register">
          {{ busy ? '登记中…' : current ? '重新登记' : '登记金标' }}
        </button>
      </div>
      <p class="muted">相对路径从后端 fixtures 目录解析；每行须含唯一 <code>entity_key</code>。</p>
      <div v-if="current" class="tiles">
        <div class="tile"><div class="hero-num">{{ current.row_count_live }}</div><div class="muted">金标行数</div></div>
        <div class="tile"><div class="hero-num">{{ current.registered_at }}</div><div class="muted">登记时间</div></div>
        <div class="tile"><div class="hero-num">{{ current.fixture_path }}</div><div class="muted">夹具</div></div>
      </div>
    </div>

    <div class="panel" v-if="current">
      <h3>金标内容</h3>
      <table>
        <thead><tr><th>实体键</th><th>字段</th></tr></thead>
        <tbody>
          <tr v-for="r in current.rows" :key="r.entity_key">
            <td>{{ r.entity_key }}</td>
            <td><code>{{ JSON.stringify(r.payload) }}</code></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel" v-if="lastRun">
      <h3>
        本次差分：批次 #{{ lastRun.batch_id }}
        <span class="tag" :class="lastRun.result === 'pass' ? 'ok' : 'danger'">{{ lastRun.result }}</span>
        <span class="tag" :class="lastRun.batch_status === 'released' ? 'ok' : 'danger'">{{ lastRun.batch_status }}</span>
      </h3>
      <div class="tiles">
        <div class="tile"><div class="hero-num">{{ lastRun.counts.golden }}</div><div class="muted">金标键</div></div>
        <div class="tile"><div class="hero-num">{{ lastRun.counts.curated }}</div><div class="muted">精选键</div></div>
        <div class="tile"><div class="hero-num">{{ lastRun.counts.golden_missing }}</div><div class="muted">金标缺</div></div>
        <div class="tile"><div class="hero-num">{{ lastRun.counts.curated_missing }}</div><div class="muted">精选缺</div></div>
        <div class="tile"><div class="hero-num">{{ lastRun.counts.field_mismatch }}</div><div class="muted">字段不一致</div></div>
        <div class="tile"><div class="hero-num">{{ lastRun.rolled_back }}</div><div class="muted">撤回行</div></div>
      </div>

      <template v-if="lastRun.result === 'fail'">
        <h4>金标缺（{{ lastRun.categories.golden_missing.count }}）</h4>
        <table v-if="lastRun.categories.golden_missing.count">
          <thead><tr><th>实体键</th><th>精选字段</th></tr></thead>
          <tbody>
            <tr v-for="i in lastRun.categories.golden_missing.items" :key="i.entity_key">
              <td>{{ i.entity_key }}</td>
              <td class="muted">{{ i.fields.join('、') }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">无</p>

        <h4>精选缺（{{ lastRun.categories.curated_missing.count }}）</h4>
        <table v-if="lastRun.categories.curated_missing.count">
          <thead><tr><th>实体键</th><th>金标载荷</th></tr></thead>
          <tbody>
            <tr v-for="i in lastRun.categories.curated_missing.items" :key="i.entity_key">
              <td>{{ i.entity_key }}</td>
              <td><code>{{ JSON.stringify(i.golden_payload) }}</code></td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">无</p>

        <h4>同键字段不一致（{{ lastRun.categories.field_mismatch.count }}）</h4>
        <table v-if="lastRun.categories.field_mismatch.count">
          <thead><tr><th>实体键</th><th>字段</th><th>金标值</th><th>精选值</th></tr></thead>
          <tbody>
            <tr v-for="i in lastRun.categories.field_mismatch.items" :key="i.entity_key">
              <td>{{ i.entity_key }}</td>
              <td>
                <div v-for="f in i.fields" :key="f.field"><code>{{ f.field }}</code></div>
              </td>
              <td>
                <div v-for="f in i.fields" :key="f.field">{{ JSON.stringify(f.golden_value) }}</div>
              </td>
              <td>
                <div v-for="f in i.fields" :key="f.field">{{ JSON.stringify(f.curated_value) }}</div>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">无</p>
      </template>

      <h4>对账</h4>
      <table>
        <thead><tr><th>校验</th><th>结果</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="c in lastRun.checks" :key="c.name">
            <td>{{ c.name }}</td>
            <td><span class="tag" :class="c.ok ? 'ok' : 'danger'">{{ c.ok ? '通过' : '失败' }}</span></td>
            <td class="muted">{{ c.detail }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>差分历史</h3>
      <p v-if="!diffs.length" class="muted">暂无差分记录；入检已登记金标的数据集后自动产生。</p>
      <table v-else>
        <thead>
          <tr>
            <th>ID</th><th>批次</th><th>数据集</th><th>结果</th>
            <th>金标缺</th><th>精选缺</th><th>字段不一致</th><th>撤回</th><th>时间</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in diffs" :key="d.id">
            <td>{{ d.id }}</td>
            <td>{{ d.batch_id }}</td>
            <td>{{ datasetName(d.dataset_id) }}</td>
            <td><span class="tag" :class="d.result === 'pass' ? 'ok' : 'danger'">{{ d.result }}</span></td>
            <td>{{ d.golden_missing_count }}</td>
            <td>{{ d.curated_missing_count }}</td>
            <td>{{ d.field_mismatch_count }}</td>
            <td>{{ d.rolled_back_count }}</td>
            <td class="muted">{{ d.created_at }}</td>
            <td><button :disabled="runningBatchId === d.batch_id" @click="runDiff(d.batch_id)">
              {{ runningBatchId === d.batch_id ? '重跑中…' : '重跑差分' }}
            </button></td>
          </tr>
        </tbody>
      </table>
      <p class="muted">重跑差分会对该批次重新执行同一发布闸门：通过则补签发，失败则再次撤回。</p>
    </div>
  </div>
</template>
