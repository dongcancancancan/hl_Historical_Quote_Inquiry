<template>
  <div class="review-shell">
    <header class="topbar">
      <div>
        <h1>批量审价参数</h1>
        <p>按 BPM 流程号统一维护审价参数 · Vue 3</p>
      </div>
      <nav>
        <a href="/static/review-v2/index.html" @click.prevent="openInternalPage('/static/review-v2/index.html')">返回工作台</a>
        <a href="/static/review-v2/quoted.html" @click.prevent="openInternalPage('/static/review-v2/quoted.html')">已报价历史</a>
        <a href="/static/review-v2/copper-scenarios.html" @click.prevent="openInternalPage('/static/review-v2/copper-scenarios.html')">铜价区间测算</a>
        <span>{{ reviewerName }} · 审价科</span>
        <el-button size="small" text @click="logout">退出</el-button>
      </nav>
    </header>

    <main class="batch-workspace">
      <aside class="batch-side">
        <section class="batch-query-card">
          <h2>BPM流程号</h2>
          <el-input
            v-model.trim="bpmNo"
            class="batch-bpm-input"
            placeholder="EG-B015-26050127"
            clearable
            @keyup.enter="loadQuotations"
            @blur="bpmNo = bpmNo.toUpperCase()"
          />
          <el-button class="wide" type="primary" :loading="loading" @click="loadQuotations">查询</el-button>
          <p>{{ statusText || "输入流程号后加载该流程下全部成本分析表" }}</p>
        </section>

        <section class="batch-selection-card">
          <div class="batch-selection-count">
            <strong>{{ selectedRows.length }}</strong>
            <span>/ {{ selectableRows.length }} 已选择</span>
          </div>
          <el-button class="wide" size="small" plain :disabled="!selectableRows.length" @click="selectAllPending">全选待报价</el-button>
          <el-button class="wide no-margin" size="small" plain :disabled="!selectedRows.length" @click="clearSelection">清空选择</el-button>
          <p>已报价记录只读展示，不能勾选。参数按 BPM 实例保存，不影响其它 BPM 流程复用的同一成本分析表。</p>
        </section>
      </aside>

      <section class="batch-table-panel">
        <div class="batch-panel-head">
          <div>
            <strong>成本分析表</strong>
            <span>查询后默认全选待报价记录</span>
          </div>
          <el-tag size="small" effect="plain">{{ items.length }} 条</el-tag>
        </div>
        <el-table
          ref="tableRef"
          v-loading="loading"
          class="batch-table"
          :data="items"
          row-key="instance_id"
          height="calc(100vh - 154px)"
          border
          @selection-change="selectedRows = $event"
        >
          <el-table-column type="selection" width="48" :selectable="isSelectable" />
          <el-table-column label="成本分析号" min-width="215">
            <template #default="{ row }">
              <span class="mono strong">{{ row.quotation_code }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="105">
            <template #default="{ row }">
              <el-tag v-if="row.review_status === 'quoted'" size="small" type="success" effect="plain">已报价</el-tag>
              <el-tag v-else-if="rowStatus(row)" size="small" :type="rowStatus(row)?.type" effect="plain">
                {{ rowStatus(row)?.text }}
              </el-tag>
              <el-tag v-else size="small" type="warning" effect="plain">待报价</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="BPM" min-width="170">
            <template #default="{ row }">
              <span class="mono link-text">{{ row.bpm_no || "-" }}</span>
            </template>
          </el-table-column>
          <el-table-column label="报价日期" width="115" prop="quote_date" />
          <el-table-column label="上传人" width="110" prop="upload_user" />
          <el-table-column label="上传时间" width="165">
            <template #default="{ row }">{{ formatTime(row.create_time) }}</template>
          </el-table-column>
          <el-table-column label="品名规格" min-width="260" prop="product_spec" show-overflow-tooltip />
        </el-table>
      </section>

      <aside class="batch-param-panel">
        <div class="batch-param-head">
          <h2>批量审价参数</h2>
          <p>将覆盖已选 {{ selectedRows.length }} 张成本分析表的本 BPM 实例参数</p>
        </div>
        <el-form class="batch-param-form" label-position="top" @submit.prevent>
          <div class="param-group">
            <h3>基础价格</h3>
            <div class="param-grid">
              <el-form-item label="铜价" required>
                <div class="param-input-with-unit">
                  <el-input-number v-model="form.copper_price" :min="0" :step="100" :controls="false" />
                  <span class="param-unit">元/吨</span>
                </div>
              </el-form-item>
              <el-form-item label="铜杆加工费">
                <div class="param-input-with-unit">
                  <el-input-number v-model="form.copper_rod_process_fee" :min="0" :step="1" :controls="false" />
                  <span class="param-unit">元/吨</span>
                </div>
              </el-form-item>
              <el-form-item label="标签费">
                <div class="param-input-with-unit">
                  <el-input-number v-model="form.ul_label_fee" :min="0" :step="0.0001" :controls="false" />
                  <span class="param-unit">RMB/M</span>
                </div>
              </el-form-item>
              <el-form-item label="运输费">
                <div class="param-input-with-unit">
                  <el-input-number v-model="form.transport_fee" :min="0" :step="0.0001" :controls="false" />
                  <span class="param-unit">RMB/KG</span>
                </div>
              </el-form-item>
              <el-form-item label="其他费用(送货费)">
                <div class="param-input-with-unit">
                  <el-input-number v-model="form.other_fee" :min="0" :step="0.01" :controls="false" />
                  <span class="param-unit">RMB/次</span>
                </div>
              </el-form-item>
            </div>
          </div>

          <div class="param-group">
            <h3>报价参数</h3>
            <div class="param-grid">
              <el-form-item label="净利率">
                <div class="param-input-with-unit">
                  <el-input-number v-model="form.net_profit_rate" :min="0" :max="100" :step="0.01" :controls="false" />
                  <span class="param-unit">%</span>
                </div>
              </el-form-item>
              <el-form-item label="增值税率">
                <div class="param-input-with-unit">
                  <el-input-number v-model="form.vat_rate" :min="0" :max="100" :step="0.01" :controls="false" />
                  <span class="param-unit">%</span>
                </div>
              </el-form-item>
              <el-form-item label="订单米数" required>
                <div class="param-input-with-unit">
                  <el-input-number v-model="form.order_meterage" :min="0" :step="1000" :controls="false" />
                  <span class="param-unit">M</span>
                </div>
              </el-form-item>
              <el-form-item label="营业费用率">
                <div class="param-input-with-unit">
                  <el-input-number v-model="form.operating_expense_rate" :min="0" :max="100" :step="0.01" :controls="false" />
                  <span class="param-unit">%</span>
                </div>
              </el-form-item>
              <el-form-item label="月结利息">
                <div class="param-input-with-unit">
                  <el-input-number v-model="form.monthly_interest" :min="0" :max="100" :step="0.001" :controls="false" />
                  <span class="param-unit">%</span>
                </div>
              </el-form-item>
              <el-form-item label="企税税率">
                <div class="param-input-with-unit">
                  <el-input-number v-model="form.corporate_tax_rate" :min="0" :max="100" :step="0.01" :controls="false" />
                  <span class="param-unit">%</span>
                </div>
              </el-form-item>
            </div>
          </div>

          <div class="param-group">
            <h3>通关/附加</h3>
            <div class="param-grid single">
              <el-form-item label="报关费">
                <div class="param-input-with-unit">
                  <el-input-number v-model="form.customs_fee" :min="0" :step="1" :controls="false" />
                  <span class="param-unit">RMB/次</span>
                </div>
              </el-form-item>
            </div>
          </div>
        </el-form>

        <div class="batch-action-buttons">
          <el-button class="wide" type="primary" plain :loading="saving" @click="applyParams(false)">仅保存参数</el-button>
          <el-button class="wide no-margin" type="success" :loading="saving" @click="applyParams(true)">保存参数并一键计算</el-button>
          <el-button class="wide no-margin" type="warning" plain :loading="exporting" @click="openExportTemplateDialog">导出报价单</el-button>
          <el-button class="wide no-margin" type="danger" plain :loading="deleting" @click="batchDelete">删除已选待报价</el-button>
        </div>
      </aside>
    </main>

    <el-dialog v-model="templateDialogVisible" title="选择报价单模板" width="520px" destroy-on-close>
      <div class="template-dialog-body">
        <el-radio-group v-model="selectedTemplateId" class="template-options">
          <el-radio-button
            v-for="template in quoteTemplates"
            :key="template.id"
            :label="template.id"
            class="template-option"
          >
            <span class="template-name">{{ template.name }}</span>
            <span class="template-desc">{{ template.filename }}</span>
          </el-radio-button>
        </el-radio-group>
        <p class="template-hint">将按所选模板导出已选 {{ selectedRows.length }} 张成本分析表。</p>
      </div>
      <template #footer>
        <el-button @click="templateDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="exporting" @click="confirmExportQuoteSheet">导出</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  assertReviewerSession,
  batchDeleteQuotations,
  batchSaveCalcParams,
  exportBatchQuote,
  fetchBatchQuoteTemplates,
  fetchQuotationsByBpm,
  openInternalPage,
} from "./api";
import type { BatchResult, QuoteItem } from "./types";

assertReviewerSession();

type RowStatus = { type: "success" | "warning" | "danger" | "info"; text: string };

const reviewerName = sessionStorage.getItem("displayName") || sessionStorage.getItem("userName") || "";
const bpmNo = ref("");
const statusText = ref("");
const loading = ref(false);
const saving = ref(false);
const deleting = ref(false);
const exporting = ref(false);
const templateDialogVisible = ref(false);
const selectedTemplateId = ref("general_quote_xls");
const quoteTemplates = ref<Array<{ id: string; name: string; filename: string; description?: string }>>([
  {
    id: "general_quote_xls",
    name: "通用报价单格式",
    filename: "通用报价单格式.xlsx",
    description: "鸿林通用报价单模板",
  },
]);
const items = ref<QuoteItem[]>([]);
const selectedRows = ref<QuoteItem[]>([]);
const rowStatusMap = reactive<Record<number, RowStatus>>({});
const tableRef = ref<{
  clearSelection: () => void;
  toggleRowSelection: (row: QuoteItem, selected?: boolean) => void;
} | null>(null);

const form = reactive({
  copper_price: null as number | null,
  copper_rod_process_fee: 1055,
  ul_label_fee: 0.0025,
  transport_fee: 0,
  other_fee: 0,
  net_profit_rate: 8,
  customs_fee: 0,
  vat_rate: 13,
  order_meterage: null as number | null,
  operating_expense_rate: 1,
  monthly_interest: 0.2,
  corporate_tax_rate: 15,
});

const selectableRows = computed(() => items.value.filter(isSelectable));

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
  Object.keys(rowStatusMap).forEach((key) => delete rowStatusMap[Number(key)]);
  try {
    const data = await fetchQuotationsByBpm(code);
    items.value = data.items || [];
    statusText.value = `共 ${items.value.length} 条，待报价 ${selectableRows.value.length} 条`;
    await nextTick();
    selectAllPending();
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
  selectableRows.value.forEach((row) => tableRef.value?.toggleRowSelection(row, true));
}

function clearSelection(): void {
  tableRef.value?.clearSelection();
  selectedRows.value = [];
}

function selectedInstanceIds(): number[] {
  return selectedRows.value.map((row) => Number(row.instance_id)).filter((id) => Number.isFinite(id) && id > 0);
}

function rowStatus(row: QuoteItem): RowStatus | null {
  const id = Number(row.instance_id);
  return Number.isFinite(id) ? rowStatusMap[id] || null : null;
}

function validateForm(calculateAfterSave: boolean): boolean {
  if (!selectedInstanceIds().length) {
    ElMessage.warning("请选择待报价成本分析表");
    return false;
  }
  if (!form.copper_price || form.copper_price <= 0) {
    ElMessage.warning("请填写大于 0 的铜价");
    return false;
  }
  if (calculateAfterSave && (!form.order_meterage || form.order_meterage <= 0)) {
    ElMessage.warning("保存并计算需要填写大于 0 的订单米数");
    return false;
  }
  return true;
}

async function applyParams(calculateAfterSave: boolean): Promise<void> {
  if (!validateForm(calculateAfterSave)) return;
  const instanceIds = selectedInstanceIds();
  saving.value = true;
  markSelected({ type: "info", text: "处理中" });
  try {
    const data = await batchSaveCalcParams({
      instance_ids: instanceIds,
      quotation_codes: [],
      copper_price: String(form.copper_price),
      copper_rod_process_fee: String(form.copper_rod_process_fee),
      ul_label_fee: String(form.ul_label_fee),
      transport_fee: String(form.transport_fee),
      other_fee: String(form.other_fee),
      net_profit_rate: percentToRatio(form.net_profit_rate),
      customs_fee: String(form.customs_fee),
      vat_rate: percentToRatio(form.vat_rate),
      order_meterage: form.order_meterage === null ? null : String(form.order_meterage),
      operating_expense_rate: percentToRatio(form.operating_expense_rate),
      monthly_interest: percentToRatio(form.monthly_interest),
      corporate_tax_rate: percentToRatio(form.corporate_tax_rate),
      calculate_after_save: calculateAfterSave,
    });
    applyResultStatus(data, calculateAfterSave);
    showBatchResult(data, calculateAfterSave ? "保存参数并计算完成" : "保存参数完成");
    // 计算成功后刷新列表，更新 final_selling_price
    if (calculateAfterSave) await loadQuotations();
  } catch (err: any) {
    markSelected({ type: "danger", text: "失败" });
    ElMessage.error("批量保存失败：" + err.message);
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

async function openExportTemplateDialog(): Promise<void> {
  const instanceIds = selectedInstanceIds();
  if (!instanceIds.length) {
    ElMessage.warning("请选择需要导出的成本分析表");
    return;
  }
  const missingFinalPrice = selectedRows.value.filter((row) => !row.final_selling_price);
  if (missingFinalPrice.length) {
    ElMessage.warning("请先确保已选成本分析表都已成功生成最终售价，再导出报价单");
    return;
  }
  templateDialogVisible.value = true;
  try {
    const templates = await fetchBatchQuoteTemplates();
    if (templates.length) {
      quoteTemplates.value = templates;
      if (!templates.some((item) => item.id === selectedTemplateId.value)) {
        selectedTemplateId.value = templates[0].id;
      }
    }
  } catch (err: any) {
    ElMessage.warning("模板列表加载失败，已使用默认模板：" + err.message);
  }
}

async function confirmExportQuoteSheet(): Promise<void> {
  const instanceIds = selectedInstanceIds();
  if (!instanceIds.length) {
    ElMessage.warning("请选择需要导出的成本分析表");
    return;
  }
  exporting.value = true;
  try {
    await exportBatchQuote(instanceIds, selectedTemplateId.value);
    ElMessage.success("报价单导出成功");
    templateDialogVisible.value = false;
  } catch (err: any) {
    ElMessage.error("报价单导出失败：" + err.message);
  } finally {
    exporting.value = false;
  }
}

function markSelected(status: RowStatus): void {
  selectedInstanceIds().forEach((id) => {
    rowStatusMap[id] = status;
  });
}

function applyResultStatus(data: BatchResult, calculateAfterSave: boolean): void {
  const skippedCodes = new Set((data.skipped || []).map((item) => item.quotation_code || ""));
  selectedRows.value.forEach((row) => {
    const id = Number(row.instance_id);
    if (!Number.isFinite(id)) return;
    if (skippedCodes.has(row.quotation_code)) {
      rowStatusMap[id] = { type: "danger", text: "失败" };
    } else {
      rowStatusMap[id] = calculateAfterSave ? { type: "success", text: "计算成功" } : { type: "success", text: "已更新" };
    }
  });
}

function showBatchResult(data: BatchResult, title: string): void {
  const skipped = data.skipped || [];
  const failedCount = skipped.length;
  const successCount = data.calculated !== undefined
    ? Number(data.calculated || 0)
    : data.updated !== undefined
      ? Number(data.updated || 0)
      : data.deleted !== undefined
        ? Number(data.deleted || 0)
        : 0;
  const summary = [
    data.updated !== undefined ? `更新 ${data.updated} 条` : "",
    data.calculated !== undefined ? `计算 ${data.calculated} 条` : "",
    data.deleted !== undefined ? `删除 ${data.deleted} 条` : "",
    `成功 ${successCount} 条`,
    `失败 ${failedCount} 条`,
  ]
    .filter(Boolean)
    .join("，");
  if (skipped.length) {
    ElMessageBox.alert(
      "部分成本分析表计算失败。批量页仅保留结果状态，请回到对应成本分析表单张查看出错原因并处理。",
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

function percentToRatio(value: number | null | undefined): string {
  if (value === null || value === undefined || value === ("" as any)) return "";
  return String(Number(value) / 100);
}

</script>
