<template>
  <div class="review-shell">
    <header class="topbar">
      <div>
        <h1>铜加工费基础数据</h1>
        <p>维护 BC/TC 线径加工费，供导体/编织计算匹配 · Vue 3</p>
      </div>
      <nav>
        <a href="/static/review-v2/index.html" @click.prevent="openInternalPage('/static/review-v2/index.html')">返回审价科工作台</a>
        <span>{{ reviewerName }} · 审价科</span>
        <el-button size="small" text @click="logout">退出</el-button>
      </nav>
    </header>

    <main class="master-page">
      <section class="master-toolbar">
        <el-select v-model="filters.copper_type" class="master-select" size="small" @change="loadFees">
          <el-option label="全部类型" value="" />
          <el-option label="BC 裸铜" value="BC" />
          <el-option label="TC 镀锡铜" value="TC" />
        </el-select>
        <el-input
          v-model.trim="filters.keyword"
          class="master-keyword"
          size="small"
          clearable
          placeholder="按线径查询，例如 0.196"
          @input="debouncedLoad"
        />
        <el-checkbox v-model="filters.include_disabled" @change="loadFees">显示停用</el-checkbox>
        <el-button size="small" type="primary" @click="openEditor()">新增</el-button>
        <el-upload
          :auto-upload="false"
          :show-file-list="false"
          accept=".xlsx"
          :on-change="handleImport"
        >
          <el-button size="small" type="primary" plain>导入 Excel</el-button>
        </el-upload>
      </section>

      <section class="master-match">
        <strong>物料编码试算</strong>
        <el-input
          v-model.trim="matchCodeText"
          class="master-match-input"
          size="small"
          placeholder="例如 0.196BC"
          clearable
          @keyup.enter="matchCode"
        />
        <el-button size="small" type="primary" plain @click="matchCode">查询</el-button>
        <span class="batch-status">{{ matchResult }}</span>
      </section>

      <section class="master-table-panel">
        <el-table v-loading="loading" :data="fees" height="calc(100vh - 220px)" border>
          <el-table-column label="类型" width="90">
            <template #default="{ row }">
              <span class="strong">{{ row.copper_type }}</span>
            </template>
          </el-table-column>
          <el-table-column label="线径" width="120">
            <template #default="{ row }"><span class="mono">{{ row.diameter }}</span></template>
          </el-table-column>
          <el-table-column label="锡价段" width="120">
            <template #default="{ row }"><span class="mono">{{ row.copper_type === "TC" ? row.tin_price_basis : "-" }}</span></template>
          </el-table-column>
          <el-table-column label="加工费" width="120">
            <template #default="{ row }"><span class="mono">{{ row.processing_fee }}</span></template>
          </el-table-column>
          <el-table-column label="最低加工费" width="120">
            <template #default="{ row }"><span class="mono">{{ row.minimum_fee || "" }}</span></template>
          </el-table-column>
          <el-table-column label="状态" width="95">
            <template #default="{ row }">
              <el-tag size="small" :type="row.enabled ? 'success' : 'info'" effect="plain">{{ row.enabled ? "启用" : "停用" }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="更新人" width="120">
            <template #default="{ row }">{{ row.updater || row.creator || "-" }}</template>
          </el-table-column>
          <el-table-column label="更新时间" width="170">
            <template #default="{ row }">{{ formatTime(row.update_time) }}</template>
          </el-table-column>
          <el-table-column label="备注" min-width="180" prop="remark" show-overflow-tooltip />
          <el-table-column label="操作" width="190" fixed="right">
            <template #default="{ row }">
              <el-button size="small" text type="primary" @click="openEditor(row)">编辑</el-button>
              <el-button size="small" text @click="showLogs(row)">日志</el-button>
              <el-button v-if="row.enabled" size="small" text type="danger" @click="disableFee(row)">停用</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </main>

    <el-dialog v-model="editorVisible" :title="editorForm.id ? '编辑铜加工费' : '新增铜加工费'" width="560px">
      <el-form class="master-editor" label-position="top" @submit.prevent>
        <el-form-item label="类型" required>
          <el-select v-model="editorForm.copper_type" @change="toggleTinBasis">
            <el-option label="BC 裸铜" value="BC" />
            <el-option label="TC 镀锡铜" value="TC" />
          </el-select>
        </el-form-item>
        <el-form-item label="线径" required>
          <el-input v-model.trim="editorForm.diameter" />
        </el-form-item>
        <el-form-item label="锡价段">
          <el-input v-model.trim="editorForm.tin_price_basis" :disabled="editorForm.copper_type !== 'TC'" />
        </el-form-item>
        <el-form-item label="加工费" required>
          <el-input v-model.trim="editorForm.processing_fee" />
        </el-form-item>
        <el-form-item label="最低加工费">
          <el-input v-model.trim="editorForm.minimum_fee" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model.trim="editorForm.remark" />
        </el-form-item>
        <el-checkbox v-model="editorForm.enabled">启用</el-checkbox>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEditor">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="logsVisible" title="操作日志" width="920px" top="7vh">
      <el-empty v-if="!logs.length" description="暂无日志" />
      <div v-else class="log-list">
        <article v-for="log in logs" :key="log.id" class="log-item">
          <div>
            <strong>{{ log.action }}</strong>
            <span>{{ log.operator || "-" }} · {{ formatTime(log.operate_time) }}</span>
          </div>
          <pre>{{ JSON.stringify({ before: log.before_data, after: log.after_data }, null, 2) }}</pre>
        </article>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox, type UploadFile } from "element-plus";
import {
  assertReviewerSession,
  disableCopperFee,
  fetchCopperFeeLogs,
  fetchCopperFees,
  importCopperFeeExcel,
  matchCopperFee,
  openInternalPage,
  saveCopperFee,
} from "./api";
import type { CopperFeeItem, CopperFeeLog } from "./types";

assertReviewerSession();

const reviewerName = sessionStorage.getItem("displayName") || sessionStorage.getItem("userName") || "";
const loading = ref(false);
const saving = ref(false);
const fees = ref<CopperFeeItem[]>([]);
const matchCodeText = ref("");
const matchResult = ref("");
const editorVisible = ref(false);
const logsVisible = ref(false);
const logs = ref<CopperFeeLog[]>([]);
let searchTimer: number | null = null;

const filters = reactive({
  copper_type: "",
  keyword: "",
  include_disabled: false,
});

const editorForm = reactive({
  id: null as number | null,
  copper_type: "BC",
  diameter: "",
  tin_price_basis: "0",
  processing_fee: "",
  minimum_fee: "",
  remark: "",
  enabled: true,
});

onMounted(() => {
  loadFees().catch((err) => ElMessage.error("加载失败：" + err.message));
});

function logout(): void {
  sessionStorage.clear();
  window.location.href = "/static/login.html";
}

async function loadFees(): Promise<void> {
  loading.value = true;
  try {
    fees.value = await fetchCopperFees(filters);
  } catch (err: any) {
    ElMessage.error("加载失败：" + err.message);
  } finally {
    loading.value = false;
  }
}

function debouncedLoad(): void {
  if (searchTimer) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => loadFees(), 300);
}

function openEditor(item?: CopperFeeItem): void {
  editorForm.id = item?.id || null;
  editorForm.copper_type = item?.copper_type || "BC";
  editorForm.diameter = item?.diameter || "";
  editorForm.tin_price_basis = item?.tin_price_basis || "0";
  editorForm.processing_fee = item?.processing_fee || "";
  editorForm.minimum_fee = item?.minimum_fee || "";
  editorForm.remark = item?.remark || "";
  editorForm.enabled = item?.enabled ?? true;
  toggleTinBasis();
  editorVisible.value = true;
}

function toggleTinBasis(): void {
  if (editorForm.copper_type !== "TC") editorForm.tin_price_basis = "0";
  if (editorForm.copper_type === "TC" && editorForm.tin_price_basis === "0") editorForm.tin_price_basis = "350";
}

async function saveEditor(): Promise<void> {
  if (!editorForm.diameter || !editorForm.processing_fee) {
    ElMessage.warning("请填写线径和加工费");
    return;
  }
  saving.value = true;
  try {
    await saveCopperFee(editorForm.id, {
      copper_type: editorForm.copper_type,
      diameter: editorForm.diameter,
      tin_price_basis: editorForm.tin_price_basis,
      processing_fee: editorForm.processing_fee,
      minimum_fee: editorForm.minimum_fee,
      remark: editorForm.remark,
      enabled: editorForm.enabled,
    });
    editorVisible.value = false;
    ElMessage.success("已保存");
    await loadFees();
  } catch (err: any) {
    ElMessage.error("保存失败：" + err.message);
  } finally {
    saving.value = false;
  }
}

async function disableFee(item: CopperFeeItem): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认停用 ${item.copper_type} ${item.diameter} 的铜加工费记录吗？`, "确认停用", { type: "warning" });
  } catch {
    return;
  }
  try {
    await disableCopperFee(item.id);
    ElMessage.success("已停用");
    await loadFees();
  } catch (err: any) {
    ElMessage.error("停用失败：" + err.message);
  }
}

async function matchCode(): Promise<void> {
  const code = matchCodeText.value.trim();
  if (!code) {
    ElMessage.warning("请填写物料编码");
    return;
  }
  try {
    const data = await matchCopperFee(code);
    matchResult.value = data.matched && data.fee
      ? `命中：${data.copper_type} ${data.diameter}，加工费 ${data.fee.processing_fee}`
      : "未命中：请维护该线径档位";
  } catch (err: any) {
    matchResult.value = err.message || "查询失败";
  }
}

async function showLogs(item: CopperFeeItem): Promise<void> {
  logsVisible.value = true;
  try {
    logs.value = await fetchCopperFeeLogs(item.id);
  } catch (err: any) {
    logs.value = [];
    ElMessage.error("日志加载失败：" + err.message);
  }
}

async function handleImport(file: UploadFile): Promise<void> {
  const raw = file.raw;
  if (!raw) return;
  try {
    const data = await importCopperFeeExcel(raw);
    ElMessage.success(`导入完成：新增 ${data.created} 条，更新 ${data.updated} 条`);
    await loadFees();
  } catch (err: any) {
    ElMessage.error("导入失败：" + err.message);
  }
}

function formatTime(value?: string | null): string {
  return value ? value.replace("T", " ").substring(0, 19) : "-";
}
</script>
