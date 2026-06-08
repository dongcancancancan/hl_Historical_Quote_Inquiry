<template>
  <div class="review-shell">
    <header class="topbar">
      <div>
        <h1>审价科批量操作</h1>
        <p>按 BPM 流程号批量设置铜价、计算和删除 · Vue 3</p>
      </div>
      <nav>
        <a href="/static/review-v2/index.html" @click.prevent="openInternalPage('/static/review-v2/index.html')">返回工作台</a>
        <a href="/static/review-v2/quoted.html" @click.prevent="openInternalPage('/static/review-v2/quoted.html')">已报价历史</a>
        <a href="/static/review-v2/copper-scenarios.html" @click.prevent="openInternalPage('/static/review-v2/copper-scenarios.html')">铜价区间测算</a>
        <span>{{ reviewerName }} · 审价科</span>
        <el-button size="small" text @click="logout">退出</el-button>
      </nav>
    </header>

    <main class="batch-page">
      <section class="batch-query">
        <el-form class="batch-query-form" inline @submit.prevent>
          <el-form-item label="BPM流程号">
            <el-input
              v-model.trim="bpmNo"
              class="batch-bpm-input"
              placeholder="EG-B015-26050127"
              clearable
              @keyup.enter="loadQuotations"
              @blur="bpmNo = bpmNo.toUpperCase()"
            />
          </el-form-item>
          <el-button type="primary" :loading="loading" @click="loadQuotations">查询</el-button>
          <span class="batch-status">{{ statusText }}</span>
        </el-form>
      </section>

      <section class="batch-layout">
        <div class="batch-table-panel">
          <div class="batch-panel-head">
            <div>
              <strong>成本分析表</strong>
              <span>已选择 {{ selectedRows.length }} 条待报价记录</span>
            </div>
            <el-button size="small" text type="primary" :disabled="!items.length" @click="selectAllPending">选择全部待报价</el-button>
          </div>
          <el-table
            ref="tableRef"
            v-loading="loading"
            class="batch-table"
            :data="items"
            row-key="instance_id"
            height="calc(100vh - 226px)"
            border
            @selection-change="selectedRows = $event"
          >
            <el-table-column type="selection" width="48" :selectable="isSelectable" />
            <el-table-column label="成本分析号" min-width="210">
              <template #default="{ row }">
                <span class="mono strong">{{ row.quotation_code }}</span>
              </template>
            </el-table-column>
            <el-table-column label="BPM" min-width="170">
              <template #default="{ row }">
                <span class="mono link-text">{{ row.bpm_no || "-" }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="95">
              <template #default="{ row }">
                <el-tag size="small" :type="row.review_status === 'quoted' ? 'success' : 'warning'" effect="plain">
                  {{ row.review_status === "quoted" ? "已报价" : "待报价" }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="报价日期" width="115" prop="quote_date" />
            <el-table-column label="上传人" width="110" prop="upload_user" />
            <el-table-column label="上传时间" width="165">
              <template #default="{ row }">{{ formatTime(row.create_time) }}</template>
            </el-table-column>
            <el-table-column label="品名规格" min-width="260" prop="product_spec" show-overflow-tooltip />
          </el-table>
        </div>

        <aside class="batch-actions-panel">
          <h2>批量设置铜价</h2>
          <el-form class="batch-action-form" label-position="top" @submit.prevent>
            <el-form-item label="铜价（元/吨）" :required="true">
              <el-input-number v-model="calcParams.copper_price" :min="0" :step="0.01" controls-position="right" />
            </el-form-item>
            <el-form-item label="铜杆加工费">
              <el-input-number v-model="calcParams.copper_rod_process_fee" :min="0" :step="0.01" controls-position="right" />
            </el-form-item>
            <el-form-item label="增值税率">
              <el-input-number v-model="calcParams.vat_rate" :min="0" :step="0.0001" controls-position="right" />
            </el-form-item>
            <el-checkbox v-model="calculateAfterSave">设置后立即完整计算导体和售价</el-checkbox>
            <el-button class="wide" type="success" :loading="saving" @click="batchSetCopper">批量设置</el-button>
            <el-button class="wide no-margin" type="danger" plain :loading="deleting" @click="batchDelete">批量删除</el-button>
          </el-form>

          <div class="batch-hint">
            <strong>操作范围</strong>
            <p>批量操作按 BPM 实例执行；同一个成本分析号挂在其它 BPM 下时不会被一起修改。</p>
            <p>已报价记录只读展示，不能勾选。</p>
          </div>
        </aside>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  assertReviewerSession,
  batchDeleteQuotations,
  batchSaveCalcParams,
  fetchQuotationsByBpm,
  openInternalPage,
} from "./api";
import type { BatchResult, QuoteItem } from "./types";

assertReviewerSession();

const reviewerName = sessionStorage.getItem("displayName") || sessionStorage.getItem("userName") || "";
const bpmNo = ref("");
const statusText = ref("");
const loading = ref(false);
const saving = ref(false);
const deleting = ref(false);
const items = ref<QuoteItem[]>([]);
const selectedRows = ref<QuoteItem[]>([]);
const tableRef = ref<{
  clearSelection: () => void;
  toggleRowSelection: (row: QuoteItem, selected?: boolean) => void;
} | null>(null);
const calculateAfterSave = ref(false);
const calcParams = reactive({
  copper_price: null as number | null,
  copper_rod_process_fee: 1055,
  vat_rate: 1.13,
});

function logout(): void {
  sessionStorage.clear();
  window.location.href = "/static/login.html";
}

async function loadQuotations(): Promise<void> {
  const code = bpmNo.value.trim().toUpperCase();
  if (!code) {
    ElMessage.warning("请填写 BPM流程号");
    return;
  }
  bpmNo.value = code;
  loading.value = true;
  statusText.value = "查询中...";
  selectedRows.value = [];
  try {
    const data = await fetchQuotationsByBpm(code);
    items.value = data.items || [];
    statusText.value = `共 ${items.value.length} 条，待报价 ${items.value.filter(isSelectable).length} 条`;
  } catch (err: any) {
    statusText.value = "";
    ElMessage.error("查询失败：" + err.message);
  } finally {
    loading.value = false;
  }
}

function isSelectable(row: QuoteItem): boolean {
  return row.review_status !== "quoted";
}

function selectAllPending(): void {
  tableRef.value?.clearSelection();
  items.value.filter(isSelectable).forEach((row) => tableRef.value?.toggleRowSelection(row, true));
}

function selectedInstanceIds(): number[] {
  return selectedRows.value.map((row) => Number(row.instance_id)).filter((id) => Number.isFinite(id) && id > 0);
}

async function batchSetCopper(): Promise<void> {
  const instanceIds = selectedInstanceIds();
  if (!instanceIds.length) {
    ElMessage.warning("请选择待报价成本分析表");
    return;
  }
  if (!calcParams.copper_price || calcParams.copper_price <= 0) {
    ElMessage.warning("请填写大于 0 的铜价");
    return;
  }
  saving.value = true;
  try {
    const data = await batchSaveCalcParams({
      instance_ids: instanceIds,
      quotation_codes: [],
      copper_price: String(calcParams.copper_price),
      copper_rod_process_fee: String(calcParams.copper_rod_process_fee),
      vat_rate: String(calcParams.vat_rate),
      calculate_after_save: calculateAfterSave.value,
    });
    showBatchResult(data, calculateAfterSave.value ? "批量设置并计算完成" : "批量设置完成");
    await loadQuotations();
  } catch (err: any) {
    ElMessage.error("批量设置失败：" + err.message);
  } finally {
    saving.value = false;
  }
}

async function batchDelete(): Promise<void> {
  const instanceIds = selectedInstanceIds();
  if (!instanceIds.length) {
    ElMessage.warning("请选择待报价成本分析表");
    return;
  }
  try {
    await ElMessageBox.confirm(`确认删除选中的 ${instanceIds.length} 个 BPM 实例下的成本分析表吗？已报价记录不会删除。`, "确认批量删除", {
      type: "warning",
    });
  } catch {
    return;
  }
  deleting.value = true;
  try {
    const data = await batchDeleteQuotations({ instance_ids: instanceIds, quotation_codes: [] });
    showBatchResult(data, "批量删除完成");
    await loadQuotations();
  } catch (err: any) {
    ElMessage.error("批量删除失败：" + err.message);
  } finally {
    deleting.value = false;
  }
}

function showBatchResult(data: BatchResult, title: string): void {
  const skipped = data.skipped || [];
  const summary = [
    data.updated !== undefined ? `更新 ${data.updated} 条` : "",
    data.calculated !== undefined ? `计算 ${data.calculated} 条` : "",
    data.deleted !== undefined ? `删除 ${data.deleted} 条` : "",
    `跳过 ${skipped.length} 条`,
  ]
    .filter(Boolean)
    .join("，");
  if (skipped.length) {
    ElMessageBox.alert(
      skipped.map((item) => `${item.quotation_code || "-"}：${item.reason || "未说明"}`).join("\n"),
      `${title}：${summary}`,
      { confirmButtonText: "知道了" },
    );
  } else {
    ElMessage.success(`${title}：${summary}`);
  }
}

function formatTime(value?: string): string {
  return value ? value.replace("T", " ").substring(0, 19) : "-";
}
</script>
