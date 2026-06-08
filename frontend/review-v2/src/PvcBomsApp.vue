<template>
  <div class="review-shell">
    <header class="topbar">
      <div>
        <h1>PVC 母料 BOM</h1>
        <p>C 开头母料 BOM 价格维护与重算 · Vue 3</p>
      </div>
      <nav>
        <a href="/static/review-v2/index.html" @click.prevent="openInternalPage('/static/review-v2/index.html')">返回审价科工作台</a>
        <a href="/static/review-v2/pvc-material-prices.html" @click.prevent="openInternalPage('/static/review-v2/pvc-material-prices.html')">PVC 材料价格</a>
        <span>{{ reviewerName }} · 审价科</span>
        <el-button size="small" text @click="logout">退出</el-button>
      </nav>
    </header>

    <main class="pvc-bom-layout">
      <aside class="quote-list">
        <div class="list-head pvc-head">
          <div>
            <strong>C 开头母料</strong>
            <el-tag size="small" effect="plain">{{ boms.length }}</el-tag>
          </div>
        </div>
        <el-input
          v-model.trim="keyword"
          class="list-search"
          size="small"
          clearable
          placeholder="搜索母件代号或名称"
          @input="debouncedLoad"
        />
        <el-scrollbar class="list-scroll">
          <el-empty v-if="!boms.length" description="暂无母料 BOM" :image-size="60" />
          <button
            v-for="item in boms"
            :key="item.bom_no"
            class="quote-card pvc-bom-card"
            :class="{ active: item.bom_no === selectedBomNo }"
            @click="selectBom(item.bom_no)"
          >
            <span class="bpm-line">
              <span>{{ item.bom_no }}</span>
              <b>售价 {{ numberText(item.sale_price, 2) }}</b>
            </span>
            <span class="muted">{{ item.name }}</span>
            <span class="pvc-bom-stats">
              <span>成本 <b>{{ numberText(item.cost, 2) }}</b></span>
              <span>加工费 <b>{{ numberText(item.process_fee, 2) }}</b></span>
              <span>包装费 <b>{{ numberText(item.package_fee, 2) }}</b></span>
              <span>总重 <b>{{ numberText(item.total_weight, 2) }}</b></span>
            </span>
          </button>
        </el-scrollbar>
      </aside>

      <section class="sheet-panel">
        <div class="sheet-head pvc-detail-head">
          <div>
            <h2>{{ detail?.main.bom_no || "选择左侧母料查看 BOM" }}</h2>
            <p>{{ detail?.main.name || "" }}</p>
          </div>
          <div v-if="detail" class="sheet-actions pvc-fee-actions">
            <el-select v-model="processFeeMode" size="small" class="fee-select" @change="syncFeeMode('process')">
              <el-option label="加工费 0.50" value="0.50" />
              <el-option label="加工费 0.65" value="0.65" />
              <el-option label="自定义加工费" value="custom" />
            </el-select>
            <el-input-number v-if="processFeeMode === 'custom'" v-model="processFeeCustom" size="small" :min="0" :step="0.0001" controls-position="right" />
            <el-select v-model="packageFeeMode" size="small" class="fee-select" @change="syncFeeMode('package')">
              <el-option label="包装费 0.04" value="0.04" />
              <el-option label="自定义包装费" value="custom" />
            </el-select>
            <el-input-number v-if="packageFeeMode === 'custom'" v-model="packageFeeCustom" size="small" :min="0" :step="0.0001" controls-position="right" />
            <el-button size="small" type="primary" :loading="saving" @click="saveFees">保存费用</el-button>
            <el-button size="small" type="success" :loading="calculating" @click="calculateCurrentBom">计算并更新</el-button>
            <span class="save-status">{{ saveStatus }}</span>
          </div>
        </div>

        <div v-if="!selectedBomNo" class="empty-state">从左侧选择一个 C 开头母料</div>
        <div v-else-if="detailLoading" class="empty-state">正在加载 BOM...</div>
        <div v-else-if="detail" class="pvc-detail-panel">
          <table class="pvc-bom-table">
            <thead>
              <tr>
                <th>母件代号</th>
                <th>母件名称</th>
                <th>材料代号</th>
                <th>材料名称</th>
                <th>材料单位</th>
                <th>BOM用量</th>
                <th class="price-head">单价</th>
                <th class="price-head">金额</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in detail.details" :key="row.id">
                <td>{{ index === 0 ? detail.main.bom_no : "" }}</td>
                <td>{{ index === 0 ? detail.main.name : "" }}</td>
                <td class="mono">{{ row.material_no }}</td>
                <td>{{ row.material_name }}</td>
                <td class="center">{{ row.unit }}</td>
                <td class="numeric mono">{{ compactNumber(row.quantity) }}</td>
                <td class="price-cell numeric mono">{{ numberText(row.unit_price, 2) }}</td>
                <td class="price-cell numeric mono">{{ numberText(row.amount, 2) }}</td>
              </tr>
              <tr>
                <td colspan="4"></td>
                <td class="numeric">合计</td>
                <td class="numeric mono">{{ numberText(detail.main.total_weight, 2) }}</td>
                <td></td>
                <td class="numeric mono">{{ numberText(detail.main.total_amount, 2) }}</td>
              </tr>
              <tr>
                <td colspan="6"></td>
                <td class="numeric">成本</td>
                <td class="numeric mono">{{ numberText(detail.main.cost, 2) }}</td>
              </tr>
              <tr>
                <td colspan="6"></td>
                <td class="fee-label numeric">加工费</td>
                <td class="fee-label numeric mono">{{ numberText(detail.main.process_fee, 2) }}</td>
              </tr>
              <tr>
                <td colspan="6"></td>
                <td class="numeric">包装费</td>
                <td class="numeric mono">{{ numberText(detail.main.package_fee, 2) }}</td>
              </tr>
              <tr>
                <td colspan="6"></td>
                <td class="numeric">售价</td>
                <td class="numeric mono strong">{{ numberText(detail.main.sale_price, 2) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  assertReviewerSession,
  calculatePvcBom,
  fetchPvcBomDetail,
  fetchPvcBoms,
  openInternalPage,
  savePvcBomFees,
} from "./api";
import type { PvcBomDetailResponse, PvcBomMain } from "./types";

assertReviewerSession();

const reviewerName = sessionStorage.getItem("displayName") || sessionStorage.getItem("userName") || "";
const keyword = ref("");
const boms = ref<PvcBomMain[]>([]);
const selectedBomNo = ref("");
const detail = ref<PvcBomDetailResponse | null>(null);
const detailLoading = ref(false);
const saving = ref(false);
const calculating = ref(false);
const saveStatus = ref("");
const processFeeMode = ref("0.50");
const packageFeeMode = ref("0.04");
const processFeeCustom = ref<number | null>(0.5);
const packageFeeCustom = ref<number | null>(0.04);
let searchTimer: number | null = null;

onMounted(() => {
  loadBoms().catch((err) => ElMessage.error("加载失败：" + err.message));
});

function logout(): void {
  sessionStorage.clear();
  window.location.href = "/static/login.html";
}

async function loadBoms(): Promise<void> {
  boms.value = await fetchPvcBoms(keyword.value);
}

function debouncedLoad(): void {
  if (searchTimer) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => loadBoms(), 300);
}

async function selectBom(bomNo: string): Promise<void> {
  selectedBomNo.value = bomNo;
  detail.value = null;
  saveStatus.value = "";
  detailLoading.value = true;
  try {
    detail.value = await fetchPvcBomDetail(bomNo);
    applyFeeModes(detail.value.main);
  } catch (err: any) {
    ElMessage.error("BOM 加载失败：" + err.message);
  } finally {
    detailLoading.value = false;
  }
}

function applyFeeModes(main: PvcBomMain): void {
  const processValue = normalizedFee(main.process_fee || "0.50");
  processFeeMode.value = ["0.50", "0.65"].includes(processValue) ? processValue : "custom";
  processFeeCustom.value = Number(processValue);
  const packageValue = normalizedFee(main.package_fee || "0.04");
  packageFeeMode.value = packageValue === "0.04" ? "0.04" : "custom";
  packageFeeCustom.value = Number(packageValue);
}

function syncFeeMode(type: "process" | "package"): void {
  if (type === "process" && processFeeMode.value !== "custom") processFeeCustom.value = Number(processFeeMode.value);
  if (type === "package" && packageFeeMode.value !== "custom") packageFeeCustom.value = Number(packageFeeMode.value);
}

function readFee(type: "process" | "package"): string {
  if (type === "process") return processFeeMode.value === "custom" ? String(processFeeCustom.value ?? "") : processFeeMode.value;
  return packageFeeMode.value === "custom" ? String(packageFeeCustom.value ?? "") : packageFeeMode.value;
}

async function saveFees(): Promise<void> {
  if (!selectedBomNo.value) return;
  saving.value = true;
  saveStatus.value = "保存中...";
  try {
    const main = await savePvcBomFees(selectedBomNo.value, {
      process_fee: readFee("process"),
      package_fee: readFee("package"),
    });
    if (detail.value) detail.value.main = main;
    updateBomList(main);
    applyFeeModes(main);
    saveStatus.value = "已保存";
  } catch (err: any) {
    saveStatus.value = "";
    ElMessage.error("保存失败：" + err.message);
  } finally {
    saving.value = false;
  }
}

async function calculateCurrentBom(): Promise<void> {
  if (!selectedBomNo.value) return;
  try {
    await ElMessageBox.confirm("确认根据当前 PVC 材料价格重新计算并更新数据库吗？", "确认计算", { type: "warning" });
  } catch {
    return;
  }
  calculating.value = true;
  saveStatus.value = "计算中...";
  try {
    detail.value = await calculatePvcBom(selectedBomNo.value);
    updateBomList(detail.value.main);
    applyFeeModes(detail.value.main);
    saveStatus.value = "已计算并更新";
  } catch (err: any) {
    saveStatus.value = "";
    ElMessage.error("计算失败：" + err.message);
  } finally {
    calculating.value = false;
  }
}

function updateBomList(main: PvcBomMain): void {
  const index = boms.value.findIndex((item) => item.bom_no === main.bom_no);
  if (index >= 0) boms.value[index] = main;
}

function normalizedFee(value?: string | null): string {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "";
}

function numberText(value?: string | null, digits = 2): string {
  if (value === null || value === undefined || value === "") return "";
  const num = Number(value);
  return Number.isFinite(num) ? num.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : String(value);
}

function compactNumber(value?: string | null): string {
  if (value === null || value === undefined || value === "") return "";
  const num = Number(value);
  return Number.isFinite(num) ? String(Number(num.toFixed(6))) : String(value);
}
</script>
