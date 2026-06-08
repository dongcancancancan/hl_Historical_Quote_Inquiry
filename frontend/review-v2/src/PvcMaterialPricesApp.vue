<template>
  <div class="review-shell">
    <header class="topbar">
      <div>
        <h1>PVC 材料价格</h1>
        <p>维护 PVC BOM 明细材料最新单价 · Vue 3</p>
      </div>
      <nav>
        <a href="/static/review-v2/index.html" @click.prevent="openInternalPage('/static/review-v2/index.html')">返回审价科工作台</a>
        <a href="/static/review-v2/pvc-boms.html" @click.prevent="openInternalPage('/static/review-v2/pvc-boms.html')">PVC 母料 BOM</a>
        <span>{{ reviewerName }} · 审价科</span>
        <el-button size="small" text @click="logout">退出</el-button>
      </nav>
    </header>

    <main class="master-page">
      <section class="master-toolbar">
        <el-input
          v-model.trim="keyword"
          class="master-keyword wide-keyword"
          size="small"
          clearable
          placeholder="按材料代号或名称查询"
          @input="debouncedLoad"
        />
        <el-button size="small" type="primary" @click="openEditor()">新增/更新材料</el-button>
        <el-upload :auto-upload="false" :show-file-list="false" accept=".xlsx" :on-change="handleImport">
          <el-button size="small" type="primary" plain>导入最新单价</el-button>
        </el-upload>
        <span class="batch-status">{{ resultText }}</span>
      </section>

      <section class="master-table-panel">
        <el-table
          v-loading="loading"
          :data="prices"
          height="calc(100vh - 178px)"
          border
          :row-class-name="rowClassName"
        >
          <el-table-column label="材料代号" min-width="150">
            <template #default="{ row }"><span class="mono strong">{{ row.prd_no }}</span></template>
          </el-table-column>
          <el-table-column label="材料名称" min-width="240" prop="name" show-overflow-tooltip />
          <el-table-column label="单位" width="90" prop="unit" />
          <el-table-column label="单价" width="120">
            <template #default="{ row }"><span class="mono">{{ row.unit_price || "-" }}</span></template>
          </el-table-column>
          <el-table-column label="状态" width="95">
            <template #default="{ row }">
              <el-tag size="small" :type="row.has_price ? 'success' : 'danger'" effect="plain">
                {{ row.has_price ? "已维护" : "缺价" }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="BOM 使用次数" width="125">
            <template #default="{ row }"><span class="mono">{{ row.used_count ?? 0 }}</span></template>
          </el-table-column>
          <el-table-column label="更新人" width="120" prop="operator" />
          <el-table-column label="更新时间" width="170">
            <template #default="{ row }">{{ formatTime(row.update_time || row.create_time) }}</template>
          </el-table-column>
          <el-table-column label="备注" min-width="180" prop="remark" show-overflow-tooltip />
          <el-table-column label="操作" width="145" fixed="right">
            <template #default="{ row }">
              <el-button size="small" text type="primary" @click="openEditor(row)">编辑</el-button>
              <el-button size="small" text @click="showLogs(row)">日志</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </main>

    <el-dialog v-model="editorVisible" title="维护 PVC 材料价格" width="620px">
      <el-form class="master-editor" label-position="top" @submit.prevent>
        <el-form-item label="材料代号" required>
          <el-input v-model.trim="editorForm.prd_no" />
        </el-form-item>
        <el-form-item label="材料单位" required>
          <el-input v-model.trim="editorForm.unit" placeholder="KG" />
        </el-form-item>
        <el-form-item class="span-2" label="材料名称" required>
          <el-input v-model.trim="editorForm.name" />
        </el-form-item>
        <el-form-item label="单价" required>
          <el-input-number v-model="editorForm.unit_price" :min="0" :step="0.000001" controls-position="right" />
        </el-form-item>
        <el-form-item label="生效日期">
          <el-date-picker v-model="editorForm.effective_date" type="date" value-format="YYYY-MM-DD" clearable />
        </el-form-item>
        <el-form-item class="span-2" label="备注">
          <el-input v-model.trim="editorForm.remark" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEditor">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="logsVisible" :title="logTitle" width="920px" top="7vh">
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
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox, type UploadFile } from "element-plus";
import {
  assertReviewerSession,
  fetchPvcMaterialPriceLogs,
  fetchPvcMaterialPrices,
  importPvcMaterialPriceExcel,
  openInternalPage,
  savePvcMaterialPrice,
} from "./api";
import type { PvcMaterialPriceItem, PvcMaterialPriceLog } from "./types";

assertReviewerSession();

const reviewerName = sessionStorage.getItem("displayName") || sessionStorage.getItem("userName") || "";
const keyword = ref("");
const loading = ref(false);
const saving = ref(false);
const prices = ref<PvcMaterialPriceItem[]>([]);
const editorVisible = ref(false);
const logsVisible = ref(false);
const logs = ref<PvcMaterialPriceLog[]>([]);
const logTitle = ref("操作日志");
let searchTimer: number | null = null;

const editorForm = reactive({
  id: null as number | null,
  prd_no: "",
  name: "",
  unit: "KG",
  unit_price: null as number | null,
  effective_date: "" as string | null,
  remark: "",
});

const resultText = computed(() => {
  const missing = prices.value.filter((item) => !item.has_price).length;
  return `${prices.value.length} 条材料价格，缺价 ${missing} 条`;
});

onMounted(() => {
  loadPrices().catch((err) => ElMessage.error("加载失败：" + err.message));
});

function logout(): void {
  sessionStorage.clear();
  window.location.href = "/static/login.html";
}

async function loadPrices(): Promise<void> {
  loading.value = true;
  try {
    prices.value = await fetchPvcMaterialPrices(keyword.value);
  } catch (err: any) {
    ElMessage.error("加载失败：" + err.message);
  } finally {
    loading.value = false;
  }
}

function debouncedLoad(): void {
  if (searchTimer) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => loadPrices(), 300);
}

function openEditor(item?: PvcMaterialPriceItem): void {
  editorForm.id = item?.id || null;
  editorForm.prd_no = item?.prd_no || "";
  editorForm.name = item?.name || "";
  editorForm.unit = item?.unit || "KG";
  editorForm.unit_price = item?.unit_price ? Number(item.unit_price) : null;
  editorForm.effective_date = item?.effective_date || "";
  editorForm.remark = item?.remark || "";
  editorVisible.value = true;
}

async function saveEditor(): Promise<void> {
  if (!editorForm.prd_no || !editorForm.name || !editorForm.unit || editorForm.unit_price === null) {
    ElMessage.warning("请填写材料代号、名称、单位和单价");
    return;
  }
  saving.value = true;
  try {
    await savePvcMaterialPrice(editorForm.id, {
      prd_no: editorForm.prd_no,
      name: editorForm.name,
      unit: editorForm.unit,
      unit_price: String(editorForm.unit_price),
      effective_date: editorForm.effective_date || null,
      remark: editorForm.remark,
    });
    editorVisible.value = false;
    ElMessage.success("已保存");
    await loadPrices();
  } catch (err: any) {
    ElMessage.error("保存失败：" + err.message);
  } finally {
    saving.value = false;
  }
}

async function showLogs(item: PvcMaterialPriceItem): Promise<void> {
  logTitle.value = `操作日志：${item.prd_no}`;
  logsVisible.value = true;
  try {
    logs.value = await fetchPvcMaterialPriceLogs(item.prd_no);
  } catch (err: any) {
    logs.value = [];
    ElMessage.error("日志加载失败：" + err.message);
  }
}

async function handleImport(file: UploadFile): Promise<void> {
  const raw = file.raw;
  if (!raw) return;
  try {
    const data = await importPvcMaterialPriceExcel(raw);
    const message = `导入完成：新增 ${data.created} 条，更新 ${data.updated} 条，跳过 ${data.skipped} 条`;
    if (data.errors?.length) {
      await ElMessageBox.alert(data.errors.join("\n"), message, { confirmButtonText: "知道了" });
    } else {
      ElMessage.success(message);
    }
    await loadPrices();
  } catch (err: any) {
    ElMessage.error("导入失败：" + err.message);
  }
}

function rowClassName({ row }: { row: PvcMaterialPriceItem }): string {
  return row.has_price ? "" : "missing-price-row";
}

function formatTime(value?: string | null): string {
  return value ? value.replace("T", " ").substring(0, 19) : "-";
}
</script>
