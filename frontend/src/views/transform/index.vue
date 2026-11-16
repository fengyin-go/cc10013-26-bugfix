<template>
  <section class="page" data-module="transform">
    <header class="page-head">
      <div>
        <h2>数据转换管理</h2>
        <p class="page-desc">维护数据转换脚本的输入、执行约束与结果记录，为数据转换提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记转换脚本</button>
        <button class="btn" type="button" @click="exportRows">导出数据转换清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDataset(row)">转换数据集</button>
            <button class="link" type="button" @click="openEdit(row)">编辑脚本</button>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据转换数据，可先登记转换脚本</td>
        </tr>
      </tbody>
    </table>

    <section v-if="dataset" class="panel" data-section="dataset">
      <header class="panel-head">
        <div>
          <h3>转换数据集 · {{ dataset.脚本编号 }}</h3>
          <p class="page-desc">
            依赖图 {{ dataset.依赖图 }} · 工作台输入版本
            <span class="version-chip">{{ dataset.工作台输入版本 || '未绑定' }}</span>
            <span :class="['tag', dataset.输入版本一致 ? 'tag-ok' : 'tag-bad']">
              {{ dataset.输入版本一致 ? '节点输入版本一致' : '节点输入版本不一致' }}
            </span>
          </p>
        </div>
        <div class="panel-actions">
          <button class="btn" type="button" @click="loadDataset()">刷新数据</button>
          <button class="btn primary" type="button" :disabled="!dataset.可发布" @click="publishDataset">
            {{ dataset.可发布 ? '发布转换结果' : '暂不可发布' }}
          </button>
          <button class="btn ghost" type="button" @click="closeDataset">收起</button>
        </div>
      </header>

      <div class="stat-row">
        <article class="stat-card"><span class="stat-label">节点数</span><strong class="stat-value">{{ dataset.节点数 }}</strong></article>
        <article class="stat-card"><span class="stat-label">成功节点</span><strong class="stat-value">{{ dataset.成功节点数 }}</strong></article>
        <article class="stat-card"><span class="stat-label">失败节点</span><strong class="stat-value">{{ dataset.失败节点数 }}</strong></article>
        <article class="stat-card"><span class="stat-label">待重试节点</span><strong class="stat-value">{{ dataset.待重试节点数 }}</strong></article>
        <article class="stat-card"><span class="stat-label">待处理审计</span><strong class="stat-value">{{ dataset.待处理审计数 }}</strong></article>
      </div>

      <p v-if="dataset.发布阻塞原因.length" class="blocker">
        发布阻塞：{{ dataset.发布阻塞原因.join('；') }}
      </p>
      <p v-else class="hint">全部节点成功且结果基于一致的依赖版本，可以发布。</p>

      <table class="data-table" data-table="dataset-nodes">
        <thead>
          <tr>
            <th>节点编号</th>
            <th>节点名称</th>
            <th>执行顺序</th>
            <th>输入版本</th>
            <th>状态</th>
            <th>结果版本</th>
            <th>结果输入版本</th>
            <th>重试次数</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="node in dataset.节点" :key="node.节点编号" :data-node="node.节点编号">
            <td>{{ node.节点编号 }}</td>
            <td>{{ node.节点名称 }}</td>
            <td>{{ node.执行顺序 }}</td>
            <td>{{ node.输入版本 || '—' }}</td>
            <td>
              <span :class="['tag', node.显示状态 === '成功' ? 'tag-ok' : 'tag-bad']">{{ node.显示状态 }}</span>
              <span v-if="node.待重试" class="tag tag-warn">待重试</span>
              <span v-if="node.结果已过期" class="tag tag-warn">结果与输入版本不一致</span>
            </td>
            <td>{{ node.结果版本 || '—' }}</td>
            <td>{{ node.结果输入版本 || '—' }}</td>
            <td>{{ node.重试次数 }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="openNode(node.节点编号)">节点详情</button>
              <button
                v-if="node.可重试"
                class="link"
                type="button"
                @click="retryNode(node.节点编号, '')"
              >
                重试
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel" data-section="audits">
      <header class="panel-head">
        <div>
          <h3>转换审计待处理区</h3>
          <p class="page-desc">失败或待重试节点会自动落到这里，记录绑定的输入版本，可直接接受重试。</p>
        </div>
        <div class="panel-actions">
          <label class="inline-field">
            <span>脚本编号</span>
            <input v-model="auditScript" placeholder="如 TRN-268" @change="loadAudits" />
          </label>
          <label class="inline-field">
            <span>状态</span>
            <select v-model="auditStatus" @change="loadAudits">
              <option value="待处理">待处理</option>
              <option value="已处理">已处理</option>
              <option value="">全部</option>
            </select>
          </label>
          <button class="btn" type="button" @click="loadAudits">刷新审计</button>
        </div>
      </header>
      <table class="data-table" data-table="audits">
        <thead>
          <tr>
            <th>审计编号</th>
            <th>脚本编号</th>
            <th>节点编号</th>
            <th>类型</th>
            <th>状态</th>
            <th>输入版本</th>
            <th>上一版结果</th>
            <th>说明</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="audit in audits" :key="audit.id" :data-audit="audit.审计编号">
            <td>{{ audit.审计编号 }}</td>
            <td>{{ audit.脚本编号 }}</td>
            <td>{{ audit.节点编号 }}</td>
            <td>{{ audit.类型 }}</td>
            <td><span :class="['tag', audit.状态 === '待处理' ? 'tag-warn' : 'tag-ok']">{{ audit.状态 }}</span></td>
            <td>{{ audit.输入版本 || '—' }}</td>
            <td>{{ audit.上一版结果版本 || '—' }}</td>
            <td class="wrap-text">{{ audit.说明 }}</td>
            <td>{{ audit.创建时间 }}</td>
            <td class="row-actions">
              <button
                v-if="audit.状态 === '待处理'"
                class="link"
                type="button"
                @click="retryAudit(audit)"
              >
                接受重试
              </button>
              <span v-else class="muted-text">{{ audit.处理说明 || '已处理' }}</span>
            </td>
          </tr>
          <tr v-if="!audits.length">
            <td colspan="10" class="empty-state">转换审计待处理区暂无记录</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据转换记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="successMessage" class="success-text">{{ successMessage }}</span>
    </footer>

    <div v-if="formVisible" class="modal-mask" @click.self="closeForm">
      <form class="modal" @submit.prevent="saveForm">
        <header class="modal-head">
          <h3>{{ formMode === 'create' ? '登记转换脚本' : `编辑转换脚本 · ${formValues.脚本编号}` }}</h3>
          <button class="link" type="button" @click="closeForm">关闭</button>
        </header>
        <div class="form-grid">
          <label v-for="field in formFields" :key="field" class="form-item">
            <span>{{ field }}<em v-if="requiredFields.includes(field)"> *</em></span>
            <input v-model="formValues[field]" :placeholder="`请输入${field}`" />
          </label>
        </div>
        <p class="hint">依赖编排、执行顺序、超时秒数与异常策略会随脚本一起保存，保存后立即生效。</p>
        <p v-if="formError" class="error-text">{{ formError }}</p>
        <footer class="modal-foot">
          <button class="btn" type="button" @click="closeForm">取消</button>
          <button class="btn primary" type="submit" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button>
        </footer>
      </form>
    </div>

    <aside v-if="drawerNode" class="drawer" data-drawer="node">
      <header class="drawer-head">
        <div>
          <h3>{{ drawerNode.节点编号 }} · {{ drawerNode.节点名称 }}</h3>
          <p class="page-desc">依赖图 {{ drawerNode.依赖图 }} · 执行顺序 {{ drawerNode.执行顺序 }}</p>
        </div>
        <button class="link" type="button" @click="closeDrawer">关闭</button>
      </header>
      <div class="drawer-body">
        <dl class="detail-list">
          <dt>节点状态</dt>
          <dd>
            <span :class="['tag', drawerNode.显示状态 === '成功' ? 'tag-ok' : 'tag-bad']">{{ drawerNode.显示状态 }}</span>
            <span v-if="drawerNode.待重试" class="tag tag-warn">待重试</span>
          </dd>
          <dt>输入版本</dt>
          <dd><span class="version-chip">{{ drawerNode.输入版本 || '未绑定' }}</span></dd>
          <dt>结果版本</dt>
          <dd>{{ drawerNode.结果版本 || '无' }}</dd>
          <dt>结果输入版本</dt>
          <dd>{{ drawerNode.结果输入版本 || '无' }}</dd>
          <dt>上一版结果</dt>
          <dd>{{ drawerNode.上一版结果版本 || '无' }}</dd>
          <dt>结果摘要</dt>
          <dd>{{ drawerNode.结果摘要 || '—' }}</dd>
          <dt>错误信息</dt>
          <dd class="wrap-text">{{ drawerNode.错误信息 || '—' }}</dd>
          <dt>发布时间</dt>
          <dd>{{ drawerNode.发布版本 || '未发布' }}</dd>
        </dl>

        <div v-if="drawerNode.依赖输入版本冲突.length" class="conflict-box">
          <strong>依赖输入版本冲突</strong>
          <ul>
            <li v-for="item in drawerNode.依赖输入版本冲突" :key="item">{{ item }}</li>
          </ul>
        </div>

        <div v-if="drawerNode.可重试" class="retry-box">
          <label class="inline-field">
            <span>重试输入版本</span>
            <select v-model="retryVersion">
              <option value="">自动（显式指定 &gt; 节点锁定 &gt; 最新完整版本）</option>
              <option v-for="version in versionOptions" :key="version.输入版本" :value="version.输入版本">
                {{ version.输入版本 }}{{ version.完整性 ? '' : '（不完整）' }}
              </option>
            </select>
          </label>
          <input v-model="retryRemark" class="remark-input" placeholder="重试备注（可选）" />
          <button class="btn primary" type="button" :disabled="retrying" @click="retryNode(drawerNode.节点编号, retryVersion)">
            {{ retrying ? '重试中…' : '执行重试' }}
          </button>
          <p class="hint">重试只绑定一组完整且一致的依赖输入版本；上游出现更新的输入版本时不会自动改变绑定。</p>
        </div>

        <h4>节点审计记录</h4>
        <table class="data-table">
          <thead>
            <tr><th>审计编号</th><th>类型</th><th>状态</th><th>输入版本</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="audit in drawerNode.审计记录" :key="audit.id">
              <td>{{ audit.审计编号 }}</td>
              <td>{{ audit.类型 }}</td>
              <td>{{ audit.状态 }}</td>
              <td>{{ audit.输入版本 }}</td>
              <td class="wrap-text">{{ audit.说明 }}</td>
            </tr>
            <tr v-if="!drawerNode.审计记录.length">
              <td colspan="5" class="empty-state">该节点暂无审计记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </aside>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

type VersionItem = {
  输入版本: string
  完整性: boolean
  生成时间: string
  来源: string
}

type Upstream = {
  节点编号: string
  输入版本: string
  结果输入版本: string
  结果版本: string
  状态: string
}

type NodeItem = {
  节点编号: string
  节点名称: string
  执行顺序: number
  依赖节点: string[]
  输入版本: string
  状态: string
  显示状态: string
  本次结果版本: string
  结果版本: string
  结果输入版本: string
  上一版结果版本: string
  结果摘要: string
  错误信息: string
  重试次数: number
  待重试: boolean
  发布状态: string
  发布版本: string
  最近执行时间: string
  失败: boolean
  结果存在: boolean
  结果与输入版本一致: boolean
  结果已过期: boolean
  依赖输入版本冲突: string[]
  上游节点: Upstream[]
  可重试: boolean
}

type Dataset = {
  脚本编号: string
  脚本主键: number
  依赖图: string
  工作台输入版本: string
  审计待处理输入版本: string[]
  结果输入版本集合: string[]
  输入版本一致: boolean
  节点: NodeItem[]
  节点数: number
  成功节点数: number
  失败节点数: number
  待重试节点数: number
  待处理审计数: number
  可发布: boolean
  发布阻塞原因: string[]
  可用输入版本: VersionItem[]
  发布时间: string
}

type NodeDetail = NodeItem & {
  依赖图: string
  审计记录: Audit[]
}

type Audit = {
  id: number
  审计编号: string
  脚本编号: string
  依赖图: string
  节点编号: string
  节点名称: string
  类型: string
  状态: string
  输入版本: string
  上一版结果版本: string
  说明: string
  创建时间: string
  处理时间: string
  处理说明: string
}

type ActionResult = {
  ok: boolean
  message: string
  entry?: Record<string, unknown> | null
}

const ENDPOINT = '/api/transform'
const columns = ["脚本编号", "输入数据源", "转换表达式", "输出结构", "依赖编排", "执行顺序", "超时秒数", "异常策略"]
const actions = ["提交校验", "启用脚本", "停用脚本"]
const formFields = ["脚本编号", "输入数据源", "转换表达式", "输出结构", "依赖编排", "执行顺序", "超时秒数", "异常策略"]
const requiredFields = ["脚本编号", "输入数据源", "转换表达式"]
const defaultPolicy = '失败保留上一版并写入转换审计'

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const successMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const dataset = ref<Dataset | null>(null)
const audits = ref<Audit[]>([])
const auditScript = ref('')
const auditStatus = ref('待处理')

const drawerNode = ref<NodeDetail | null>(null)
const retryVersion = ref('')
const retryRemark = ref('')
const retrying = ref(false)

const formVisible = ref(false)
const formMode = ref<'create' | 'edit'>('create')
const formId = ref<number | null>(null)
const formValues = ref<Record<string, string>>({})
const formError = ref('')
const saving = ref(false)

const stats = computed(() => [
  { label: '转换脚本总数', value: total.value },
  { label: '已启用脚本', value: rows.value.filter((row) => row.status === '已启用').length },
  { label: '待校验脚本', value: rows.value.filter((row) => row.status === '待校验').length },
])

const versionOptions = computed<VersionItem[]>(() => dataset.value?.可用输入版本 ?? [])

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function emptyForm(): Record<string, string> {
  return {
    脚本编号: '',
    输入数据源: '',
    转换表达式: '',
    输出结构: '',
    依赖编排: '',
    执行顺序: '',
    超时秒数: '',
    异常策略: defaultPolicy,
  }
}

function openCreate() {
  formMode.value = 'create'
  formId.value = null
  formValues.value = emptyForm()
  formError.value = ''
  formVisible.value = true
}

function openEdit(row: Row) {
  formMode.value = 'edit'
  formId.value = Number(row.id)
  formValues.value = Object.fromEntries(
    formFields.map((field) => [field, row[field] === null || row[field] === undefined ? '' : String(row[field])]),
  )
  formError.value = ''
  formVisible.value = true
}

function closeForm() {
  formVisible.value = false
  formError.value = ''
}

async function saveForm() {
  formError.value = ''
  saving.value = true
  try {
    const url = formMode.value === 'create' ? ENDPOINT : `${ENDPOINT}/${formId.value}`
    const response = await request(url, {
      method: formMode.value === 'create' ? 'POST' : 'PUT',
      body: JSON.stringify({ values: formValues.value }),
    })
    const payload = (await response.json()) as ActionResult
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '转换脚本保存失败')
    }
    successMessage.value = payload.message
    formVisible.value = false
    await reload()
    if (dataset.value && formMode.value === 'edit' && formId.value === dataset.value.脚本主键) {
      await loadDataset()
    }
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '转换脚本保存失败'
  } finally {
    saving.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('数据转换动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据转换操作失败'
  }
}

async function openDataset(row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/dataset`)
    if (!response.ok) {
      throw new Error('转换数据集读取失败')
    }
    dataset.value = (await response.json()) as Dataset
    auditScript.value = dataset.value.脚本编号
    await loadAudits()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '转换数据集读取失败'
  }
}

async function loadDataset() {
  if (!dataset.value) return
  const response = await request(`${ENDPOINT}/${dataset.value.脚本主键}/dataset`)
  if (!response.ok) {
    throw new Error('转换数据集读取失败')
  }
  dataset.value = (await response.json()) as Dataset
}

function closeDataset() {
  dataset.value = null
  drawerNode.value = null
}

async function loadAudits() {
  const query = new URLSearchParams()
  if (auditScript.value) query.set('script', auditScript.value)
  if (auditStatus.value) query.set('status', auditStatus.value)
  const response = await request(`${ENDPOINT}/audits?${query.toString()}`)
  if (!response.ok) {
    errorMessage.value = '转换审计读取失败'
    return
  }
  const payload = (await response.json()) as { items?: Audit[] }
  audits.value = payload.items ?? []
}

async function openNode(nodeId: string) {
  if (!dataset.value) return
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${dataset.value.脚本主键}/nodes/${nodeId}`)
    if (!response.ok) {
      throw new Error(`节点 ${nodeId} 详情读取失败`)
    }
    drawerNode.value = (await response.json()) as NodeDetail
    retryVersion.value = ''
    retryRemark.value = ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '节点详情读取失败'
  }
}

function closeDrawer() {
  drawerNode.value = null
}

async function retryNode(nodeId: string, inputVersion: string) {
  if (!dataset.value) return
  retrying.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${dataset.value.脚本主键}/nodes/${nodeId}/retry`, {
      method: 'POST',
      body: JSON.stringify({
        values: {
          输入版本: inputVersion || undefined,
          remark: retryRemark.value || undefined,
        },
      }),
    })
    const payload = (await response.json()) as ActionResult
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '节点重试未生效')
    }
    successMessage.value = payload.message
    await loadDataset()
    await loadAudits()
    await openNode(nodeId)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '节点重试未生效'
    if (dataset.value) {
      await loadDataset()
      await openNode(nodeId)
    }
  } finally {
    retrying.value = false
  }
}

async function retryAudit(audit: Audit) {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/audits/${audit.id}/retry`, {
      method: 'POST',
      body: JSON.stringify({
        values: { 输入版本: audit.输入版本 || undefined },
        remark: retryRemark.value || undefined,
      }),
    })
    const payload = (await response.json()) as ActionResult
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '审计记录重试未生效')
    }
    successMessage.value = payload.message
    if (dataset.value) {
      await loadDataset()
    }
    await loadAudits()
    if (dataset.value) {
      await openNode(audit.节点编号)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '审计记录重试未生效'
    await loadAudits()
  }
}

async function publishDataset() {
  if (!dataset.value) return
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${dataset.value.脚本主键}/publish`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    const payload = (await response.json()) as ActionResult
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '转换结果发布失败')
    }
    successMessage.value = payload.message
    await loadDataset()
    await loadAudits()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '转换结果发布失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('转换脚本列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据转换列表读取失败'
  }
}

onMounted(async () => {
  await reload()
  await loadAudits()
})
</script>

<style scoped>
.panel {
  margin-top: 18px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 10px;
}
.panel-head h3 { margin: 0; font-size: 15px; }
.panel-actions { display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; }
.inline-field { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 2px; }
.inline-field select, .inline-field input { padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; }
.version-chip {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  background: #eef4ff;
  color: #1f6feb;
  font-size: 12px;
}
.tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12px;
  margin-right: 4px;
}
.tag-ok { background: #e7f6ec; color: #16794a; }
.tag-bad { background: #fdeceb; color: #b42318; }
.tag-warn { background: #fff5e6; color: #a15c00; }
.blocker { color: #b42318; font-size: 13px; margin: 6px 0; }
.hint { color: var(--muted); font-size: 12px; margin: 6px 0; }
.muted-text { color: var(--muted); font-size: 12px; }
.success-text { color: #16794a; }
.wrap-text { max-width: 280px; word-break: break-word; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  width: min(720px, 92vw);
  background: #fff;
  border-radius: 10px;
  padding: 16px 18px;
}
.modal-head, .modal-foot { display: flex; justify-content: space-between; align-items: center; }
.modal-head h3 { margin: 0; font-size: 16px; }
.modal-foot { margin-top: 12px; gap: 8px; justify-content: flex-end; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; margin-top: 12px; }
.form-item { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.form-item input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.form-item em { color: #b42318; font-style: normal; }
.drawer {
  position: fixed;
  top: 0;
  right: 0;
  width: min(560px, 96vw);
  height: 100vh;
  background: #fff;
  border-left: 1px solid var(--border);
  box-shadow: -8px 0 24px rgba(15, 23, 42, 0.12);
  z-index: 25;
  display: flex;
  flex-direction: column;
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 16px 18px 8px;
  border-bottom: 1px solid var(--border);
}
.drawer-head h3 { margin: 0; font-size: 15px; }
.drawer-body { padding: 12px 18px 24px; overflow-y: auto; }
.detail-list { display: grid; grid-template-columns: 110px 1fr; gap: 6px 10px; font-size: 13px; margin: 0 0 12px; }
.detail-list dt { color: var(--muted); }
.detail-list dd { margin: 0; }
.conflict-box { border: 1px solid #f2b8b5; background: #fff5f5; border-radius: 8px; padding: 8px 10px; font-size: 12px; color: #b42318; margin-bottom: 10px; }
.conflict-box ul { margin: 4px 0 0; padding-left: 18px; }
.retry-box { border: 1px solid var(--border); border-radius: 8px; padding: 10px; display: flex; gap: 8px; flex-wrap: wrap; align-items: flex-end; margin-bottom: 12px; }
.remark-input { flex: 1; min-width: 160px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; }
.retry-box .hint { flex-basis: 100%; }
.drawer-body h4 { margin: 12px 0 6px; font-size: 13px; }
</style>
