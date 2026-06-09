<template>
  <div class="review-shell">
    <header class="topbar">
      <div>
        <h1>审价科工作台</h1>
        <p>历史成本分析表审价 · Vue 3</p>
      </div>
      <nav>
        <a href="/static/review-v2/quoted.html" @click.prevent="openInternalPage('/static/review-v2/quoted.html')">已报价历史</a>
        <a href="/static/review-v2/batch.html" @click.prevent="openInternalPage('/static/review-v2/batch.html')">批量操作</a>
        <a href="/static/review-v2/copper-scenarios.html" @click.prevent="openInternalPage('/static/review-v2/copper-scenarios.html')">铜价区间测算</a>
        <a href="/static/review-v2/copper-fees.html" @click.prevent="openInternalPage('/static/review-v2/copper-fees.html')">铜加工费</a>
        <a href="/static/review-v2/pvc-material-prices.html" @click.prevent="openInternalPage('/static/review-v2/pvc-material-prices.html')">PVC 材料价格</a>
        <a href="/static/review-v2/pvc-boms.html" @click.prevent="openInternalPage('/static/review-v2/pvc-boms.html')">PVC 母料 BOM</a>
        <span>{{ reviewerName }} · 审价科</span>
        <el-button size="small" text @click="logout">退出</el-button>
      </nav>
    </header>

    <main class="review-layout" :class="{ collapsed: sidebarCollapsed }">
      <QuoteList
        :items="pendingItems"
        v-model:search="pendingSearch"
        :selected-code="selectedCode"
        :selected-instance-id="selectedInstanceId"
        :collapsed="sidebarCollapsed"
        @toggle="sidebarCollapsed = !sidebarCollapsed"
        @select="selectQuote"
        @copy-bpm="copyBpmNo"
      />

      <section class="sheet-panel">
        <div class="sheet-head">
          <div>
            <h2>{{ selectedCode || "选择成本分析号查看" }}</h2>
            <p>{{ selectedCode ? (selectedStatus === "quoted" ? "已报价 · 只读" : "待报价 · 可编辑") : "" }}</p>
          </div>
          <div v-if="selectedCode" class="sheet-actions">
            <span class="save-status">{{ saveStatus }}</span>
            <el-button size="small" text type="primary" @click="exportCurrent">导出 Excel</el-button>
            <el-button v-if="selectedStatus !== 'quoted' && !editing" size="small" text type="primary" @click="enableEditing">
              编辑
            </el-button>
            <el-button v-if="editing" size="small" text type="success" @click="saveChanges">保存</el-button>
            <el-button v-if="editing" size="small" text @click="cancelEditing">取消</el-button>
            <el-button v-if="selectedStatus !== 'quoted'" size="small" text type="warning" @click="quoteCurrent">
              标记已报价
            </el-button>
          </div>
        </div>

        <div v-if="selectedCode" class="calc-toolbar">
          <el-form class="calc-form" inline :model="calcParams" @submit.prevent>
            <el-form-item label="铜价（元/吨）">
              <el-input-number
                v-model="calcParams.copper_price"
                :class="{ 'param-invalid': !!calcInputErrors.copper_price }"
                size="small"
                :min="0"
                :step="0.01"
                controls-position="right"
                @change="handleCalcParamInput"
              />
            </el-form-item>
            <el-form-item label="铜杆加工费">
              <el-input-number
                v-model="calcParams.copper_rod_process_fee"
                :class="{ 'param-invalid': !!calcInputErrors.copper_rod_process_fee }"
                size="small"
                :min="0"
                :step="0.01"
                controls-position="right"
                @change="handleCalcParamInput"
              />
            </el-form-item>
            <el-form-item label="增值税率">
              <el-input-number
                v-model="calcParams.vat_rate"
                :class="{ 'param-invalid': !!calcInputErrors.vat_rate }"
                size="small"
                :min="0"
                :step="0.0001"
                controls-position="right"
                @change="handleCalcParamInput"
              />
            </el-form-item>
          </el-form>

          <div class="calc-actions">
            <el-button size="small" type="primary" @click="saveCalcParamsManual">保存参数</el-button>
            <el-button size="small" type="danger" plain @click="clearPrices">清空计算结果</el-button>
            <el-button size="small" type="warning" :loading="calculationInFlight" @click="calculateFullPrice">
              一键计算最终售价
            </el-button>
            <el-button size="small" type="primary" plain @click="runDiagnosis">AI 辅助分析</el-button>
            <el-button size="small" type="warning" plain @click="showAllTraces">查看计算过程</el-button>
            <el-dropdown trigger="click">
              <el-button size="small">
                高级计算
                <el-icon class="el-icon--right"><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="calculateConductorOnly">计算导体/编织</el-dropdown-item>
                  <el-dropdown-item @click="showTraceGroup('conductor')">查看导体/编织过程</el-dropdown-item>
                  <el-dropdown-item @click="calculateGlueOnly">计算胶料/外购</el-dropdown-item>
                  <el-dropdown-item @click="showTraceGroup('glue')">查看胶料/外购过程</el-dropdown-item>
                  <el-dropdown-item @click="showSkills">查看计算 Skill</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <span class="calc-status" :class="calcStatusTone">{{ calcStatus }}</span>
          </div>
        </div>

        <div v-if="!selectedCode" class="empty-state">从左侧列表中选择一条报价单</div>
        <div v-else-if="sheetLoading" class="empty-state">正在加载数据库内容...</div>
        <iframe v-show="selectedCode && !sheetLoading" ref="sheetFrame" class="sheet-frame" title="成本分析表"></iframe>
      </section>

      <DiagnosisPanel
        :selected-code="selectedCode"
        :diagnosis="currentDiagnosis"
        :loading="diagnosisLoading"
        @diagnose="runDiagnosis"
        @skills="showSkills"
      />
    </main>

    <TraceDialog
      v-model="traceVisible"
      :title="traceTitle"
      :loading="traceLoading"
      :groups="traceGroups"
      :skills="skillRows"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { ArrowDown } from "@element-plus/icons-vue";
import QuoteList from "./components/QuoteList.vue";
import DiagnosisPanel from "./components/DiagnosisPanel.vue";
import TraceDialog from "./components/TraceDialog.vue";
import {
  assertReviewerSession,
  calculate,
  clearUnitPrices as clearUnitPricesApi,
  diagnose,
  exportExcel,
  fetchCalcParams,
  fetchPreview,
  fetchReviewHistory,
  fetchSkills,
  fetchTraces,
  markQuoted,
  openInternalPage,
  saveCalcParams,
  updateQuotation,
} from "./api";
import type { CalcParams, DiagnosisResult, QuoteItem, ReviewStatus, SkillItem, StatusTone, TraceGroup } from "./types";

assertReviewerSession();

const reviewerName = sessionStorage.getItem("displayName") || sessionStorage.getItem("userName") || "";
const pendingItems = ref<QuoteItem[]>([]);
const pendingSearch = ref("");
const sidebarCollapsed = ref(false);
const selectedCode = ref("");
const selectedInstanceId = ref<number | null>(null);
const selectedStatus = ref<ReviewStatus>("pending");
const sheetFrame = ref<HTMLIFrameElement | null>(null);
const sheetLoading = ref(false);
const editing = ref(false);
const saveStatus = ref("");
const calcStatus = ref("");
const calcStatusTone = ref<StatusTone>("info");
const calculationInFlight = ref(false);
const diagnosisLoading = ref(false);
const diagnosisCache = reactive<Record<string, DiagnosisResult>>({});
const lastCalculationError = ref("");
const traceVisible = ref(false);
const traceLoading = ref(false);
const traceTitle = ref("计算过程");
const traceGroups = ref<TraceGroup[]>([]);
const skillRows = ref<SkillItem[]>([]);
let historyRequestSeq = 0;
let searchTimer: number | null = null;
let calcParamAutoSaveTimer: number | null = null;
let calcParamSavingPromise: Promise<boolean> | null = null;

const calcParams = reactive<CalcParams>({
  copper_price: "",
  copper_rod_process_fee: "1055",
  vat_rate: "1.13",
});
const savedCalcParams = reactive<CalcParams>({
  copper_price: "",
  copper_rod_process_fee: "1055",
  vat_rate: "1.13",
});
const calcInputErrors = reactive<Partial<Record<keyof CalcParams, string>>>({
  copper_price: "",
  copper_rod_process_fee: "",
  vat_rate: "",
  update_time: "",
});

const currentDiagnosis = computed(() => (selectedCode.value ? diagnosisCache[selectedCode.value] || null : null));

watch(pendingSearch, () => {
  if (searchTimer) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => loadHistory(), 300);
});

onMounted(() => {
  loadHistory().catch((err) => ElMessage.error("加载失败：" + err.message));
});

function logout(): void {
  sessionStorage.clear();
  window.location.href = "/static/login.html";
}

function selectedQueryValues() {
  return {
    code: selectedCode.value,
    instanceId: selectedInstanceId.value,
  };
}

async function loadHistory(): Promise<void> {
  const requestSeq = ++historyRequestSeq;
  const data = await fetchReviewHistory(pendingSearch.value);
  if (requestSeq !== historyRequestSeq) return;
  pendingItems.value = data.pending || [];
}

async function selectQuote(item: QuoteItem): Promise<void> {
  selectedCode.value = item.quotation_code;
  selectedInstanceId.value = item.instance_id ? Number(item.instance_id) : null;
  selectedStatus.value = (item.review_status || "pending") as ReviewStatus;
  lastCalculationError.value = "";
  editing.value = false;
  saveStatus.value = "";
  sheetLoading.value = true;
  await nextTick();
  await Promise.all([loadPreview(), loadCalcParams()]);
}

async function loadPreview(): Promise<void> {
  if (!selectedCode.value) return;
  try {
    const html = await fetchPreview(selectedCode.value, selectedInstanceId.value);
    await nextTick();
    if (sheetFrame.value) sheetFrame.value.srcdoc = html;
  } catch (err: any) {
    ElMessage.error("预览加载失败：" + err.message);
  } finally {
    sheetLoading.value = false;
  }
}

async function loadCalcParams(): Promise<void> {
  if (!selectedCode.value) return;
  resetCalcErrors();
  setCalcStatus("参数加载中...", "info");
  try {
    const data = await fetchCalcParams(selectedCode.value, selectedInstanceId.value);
    assignCalcParams(data);
    const validation = validateCalcParams();
    setCalcStatus(validation.ok ? (data.update_time ? "参数已加载" : "使用默认参数") : validation.message, validation.ok ? "info" : "danger");
  } catch (err: any) {
    setCalcStatus(err.message || "参数加载失败", "danger");
  }
}

function assignCalcParams(data: Partial<CalcParams>): void {
  calcParams.copper_price = paramText(data.copper_price, "");
  calcParams.copper_rod_process_fee = paramText(data.copper_rod_process_fee, "1055");
  calcParams.vat_rate = paramText(data.vat_rate, "1.13");
  savedCalcParams.copper_price = calcParams.copper_price;
  savedCalcParams.copper_rod_process_fee = calcParams.copper_rod_process_fee;
  savedCalcParams.vat_rate = calcParams.vat_rate;
}

function paramText(value: unknown, fallback = ""): string {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function resetCalcErrors(): void {
  calcInputErrors.copper_price = "";
  calcInputErrors.copper_rod_process_fee = "";
  calcInputErrors.vat_rate = "";
}

function normalizeNumberText(value: unknown): string {
  const text = String(value ?? "").trim();
  if (!text) return "";
  const number = Number(text);
  return Number.isFinite(number) ? String(number) : text;
}

function hasCalcParamChanges(): boolean {
  return ["copper_price", "copper_rod_process_fee", "vat_rate"].some(
    (key) => normalizeNumberText(calcParams[key as keyof CalcParams]) !== normalizeNumberText(savedCalcParams[key as keyof CalcParams]),
  );
}

function validateCalcParams(): { ok: boolean; message: string } {
  resetCalcErrors();
  const copperPrice = Number(calcParams.copper_price);
  const rodFee = Number(calcParams.copper_rod_process_fee);
  const vatRate = Number(calcParams.vat_rate);
  if (!calcParams.copper_price) {
    calcInputErrors.copper_price = "必填";
    return { ok: false, message: "铜价未填写。请填写铜价，系统会自动保存后再计算。" };
  }
  if (!Number.isFinite(copperPrice) || copperPrice <= 0) {
    calcInputErrors.copper_price = "需大于 0";
    return { ok: false, message: "铜价必须是大于 0 的数字。" };
  }
  if (!Number.isFinite(rodFee) || rodFee < 0) {
    calcInputErrors.copper_rod_process_fee = "需大于等于 0";
    return { ok: false, message: "铜杆加工费必须是大于等于 0 的数字。" };
  }
  if (!Number.isFinite(vatRate) || vatRate <= 0) {
    calcInputErrors.vat_rate = "需大于 0";
    return { ok: false, message: "增值税率必须是大于 0 的数字。" };
  }
  return { ok: true, message: "" };
}

function handleCalcParamInput(): void {
  if (!selectedCode.value) return;
  if (calcParamAutoSaveTimer) window.clearTimeout(calcParamAutoSaveTimer);
  const validation = validateCalcParams();
  if (!validation.ok) {
    renderLocalDiagnosis(validation.message);
    setCalcStatus(validation.message, "danger");
    return;
  }
  if (!hasCalcParamChanges()) {
    setCalcStatus("参数已保存", "success");
    return;
  }
  setCalcStatus("参数已修改，正在自动保存...", "warning");
  calcParamAutoSaveTimer = window.setTimeout(() => {
    saveCalcParamsInternal(true);
  }, 700);
}

async function ensureCalcParamsReady(): Promise<boolean> {
  const validation = validateCalcParams();
  if (!validation.ok) {
    renderLocalDiagnosis(validation.message);
    setCalcStatus(validation.message, "danger");
    return false;
  }
  if (calcParamAutoSaveTimer) {
    window.clearTimeout(calcParamAutoSaveTimer);
    calcParamAutoSaveTimer = null;
  }
  if (hasCalcParamChanges()) {
    return saveCalcParamsInternal(true);
  }
  if (calcParamSavingPromise) return calcParamSavingPromise;
  return true;
}

async function saveCalcParamsManual(): Promise<void> {
  await saveCalcParamsInternal(false);
}

async function saveCalcParamsInternal(auto: boolean): Promise<boolean> {
  if (!selectedCode.value) return false;
  const validation = validateCalcParams();
  if (!validation.ok) {
    renderLocalDiagnosis(validation.message);
    setCalcStatus(validation.message, "danger");
    return false;
  }
  const { code, instanceId } = selectedQueryValues();
  setCalcStatus(auto ? "自动保存中..." : "保存中...", "info");
  calcParamSavingPromise = (async () => {
    try {
      const data = await saveCalcParams(code, instanceId, {
        copper_price: paramText(calcParams.copper_price, ""),
        copper_rod_process_fee: paramText(calcParams.copper_rod_process_fee, "1055"),
        vat_rate: paramText(calcParams.vat_rate, "1.13"),
      });
      if (selectedCode.value === code && selectedInstanceId.value === instanceId) {
        assignCalcParams(data);
        if (diagnosisCache[selectedCode.value]?.mode === "local") delete diagnosisCache[selectedCode.value];
        setCalcStatus(auto ? "参数已自动保存" : "参数已保存", "success");
      }
      return true;
    } catch (err: any) {
      setCalcStatus("计算参数保存失败：" + err.message, "danger");
      if (!auto) ElMessage.error("计算参数保存失败：" + err.message);
      return false;
    } finally {
      calcParamSavingPromise = null;
    }
  })();
  return calcParamSavingPromise;
}

async function calculateFullPrice(): Promise<void> {
  if (!selectedCode.value || !(await ensureCalcParamsReady())) return;
  await runCalculation("确认按当前全部逻辑一键计算最终售价吗？系统会依次计算导体/编织、胶料/外购、制程费用和最终售价。", "full-price");
}

async function calculateConductorOnly(): Promise<void> {
  if (!selectedCode.value || !(await ensureCalcParamsReady())) return;
  await runCalculation("确认根据铜价和铜加工费重新计算导体/编织单价、材料金额和制程费用吗？", "conductor");
}

async function calculateGlueOnly(): Promise<void> {
  if (!selectedCode.value) return;
  await runCalculation("确认重新计算 C 开头胶料、外购物料的单价、材料金额，以及绝缘/外被/倒线/集合制程费用吗？", "glue");
}

async function runCalculation(message: string, type: "conductor" | "glue" | "full-price"): Promise<void> {
  if (calculationInFlight.value) {
    setCalcStatus("已有计算正在执行，请稍等...", "warning");
    return;
  }
  try {
    await ElMessageBox.confirm(message, "确认计算", { type: "warning" });
  } catch {
    return;
  }
  calculationInFlight.value = true;
  setCalcStatus(type === "full-price" ? "最终售价计算中..." : type === "conductor" ? "导体/编织计算中..." : "胶料/外购计算中...", "info");
  try {
    const data = await calculate(selectedCode.value, selectedInstanceId.value, type);
    lastCalculationError.value = "";
    await Promise.all([loadHistory(), loadPreview()]);
    setCalcStatus(buildCalculationStatus(data, type), "success");
  } catch (err: any) {
    lastCalculationError.value = err.message || "未知错误";
    if (type === "full-price") {
      await Promise.all([loadHistory(), loadPreview()]);
    }
    renderRuleDiagnosis(lastCalculationError.value);
    if (type === "full-price") {
      setCalcStatus("已保留可计算结果，最终售价未生成；请查看右侧异常提示。", "danger");
    } else {
      setCalcStatus(lastCalculationError.value, "danger");
    }
  } finally {
    calculationInFlight.value = false;
  }
}

function buildCalculationStatus(data: Record<string, any>, type: string): string {
  if (type === "conductor") return `已计算材料 ${data.calculated || 0} 行，制程费用 ${data.process_calculated || 0} 行`;
  if (type === "glue") {
    return `已计算胶料 ${data.c_calculated || 0} 行，外购 ${data.external_calculated || 0} 行，色母 ${data.color_masterbatch_calculated || 0} 行，包带制程 ${data.package_tape_process_calculated || 0} 行`;
  }
  const conductor = data.conductor || {};
  const glue = data.glue || {};
  const price = data.price_summary || {};
  return `最终售价已计算：导体/编织 ${conductor.calculated || 0} 行，胶料 ${glue.c_calculated || 0} 行，外购 ${glue.external_calculated || 0} 行，包带制程 ${glue.package_tape_process_calculated || 0} 行，最终售价 ${price.final_selling_price || "-"}`;
}

async function showAllTraces(): Promise<void> {
  if (!selectedCode.value) return;
  traceVisible.value = true;
  traceLoading.value = true;
  traceTitle.value = "计算过程";
  skillRows.value = [];
  try {
    const [conductor, glue, price] = await Promise.all([
      fetchTraces(selectedCode.value, selectedInstanceId.value, "conductor"),
      fetchTraces(selectedCode.value, selectedInstanceId.value, "glue"),
      fetchTraces(selectedCode.value, selectedInstanceId.value, "price-summary"),
    ]);
    traceGroups.value = [
      { title: "导体/编织", rows: conductor },
      { title: "胶料/外购及制程", rows: glue },
      { title: "售价汇总", rows: price },
    ];
  } catch (err: any) {
    ElMessage.error("计算过程加载失败：" + err.message);
  } finally {
    traceLoading.value = false;
  }
}

async function showTraceGroup(type: "conductor" | "glue" | "price-summary"): Promise<void> {
  if (!selectedCode.value) return;
  const titles = { conductor: "导体/编织计算过程", glue: "胶料/外购计算过程", "price-summary": "售价汇总计算过程" };
  traceVisible.value = true;
  traceLoading.value = true;
  traceTitle.value = titles[type];
  skillRows.value = [];
  try {
    traceGroups.value = [{ title: titles[type], rows: await fetchTraces(selectedCode.value, selectedInstanceId.value, type) }];
  } catch (err: any) {
    ElMessage.error("计算过程加载失败：" + err.message);
  } finally {
    traceLoading.value = false;
  }
}

async function showSkills(): Promise<void> {
  traceVisible.value = true;
  traceLoading.value = true;
  traceTitle.value = "计算 Skill";
  traceGroups.value = [];
  try {
    skillRows.value = await fetchSkills();
  } catch (err: any) {
    ElMessage.error("计算 Skill 加载失败：" + err.message);
  } finally {
    traceLoading.value = false;
  }
}

async function runDiagnosis(errorMessage = ""): Promise<void> {
  if (!selectedCode.value) return;
  const validation = validateCalcParams();
  if (!validation.ok) {
    renderLocalDiagnosis(validation.message);
    setCalcStatus(validation.message, "danger");
    return;
  }
  diagnosisLoading.value = true;
  setCalcStatus("AI 辅助分析中...", "info");
  try {
    diagnosisCache[selectedCode.value] = await diagnose(selectedCode.value, selectedInstanceId.value, errorMessage || lastCalculationError.value);
    setCalcStatus("", "info");
  } catch (err: any) {
    ElMessage.error("AI 辅助分析失败：" + err.message);
  } finally {
    diagnosisLoading.value = false;
  }
}

function renderLocalDiagnosis(message: string): void {
  if (!selectedCode.value) return;
  lastCalculationError.value = message;
  diagnosisCache[selectedCode.value] = {
    mode: "local",
    quotation_code: selectedCode.value,
    summary: `当前问题：${message}\n处理方式：请在上方参数栏补充或修正，系统会自动保存。保存成功后再重新计算。\n系统没有调用 AI。`,
    skills: [],
  };
}

function renderRuleDiagnosis(message: string): void {
  if (!selectedCode.value) return;
  lastCalculationError.value = message;
  diagnosisCache[selectedCode.value] = {
    mode: "rule",
    quotation_code: selectedCode.value,
    summary: `计算未完成：${message}\n处理方式：请根据缺失的材料单价、制程公式或审价参数维护基础数据；已能计算的结果会保留。处理后重新点击一键计算。\n系统没有自动调用 AI；需要自然语言解释时，可点击“AI 辅助分析”。`,
    skills: [],
  };
}

function enableEditing(): void {
  if (selectedStatus.value === "quoted") return;
  const doc = sheetFrame.value?.contentDocument;
  doc?.querySelectorAll<HTMLInputElement>(".sheet-input").forEach((input) => {
    input.disabled = false;
  });
  editing.value = true;
  saveStatus.value = "编辑中";
}

async function cancelEditing(): Promise<void> {
  editing.value = false;
  await loadPreview();
}

async function saveChanges(): Promise<void> {
  const inputs = sheetFrame.value?.contentDocument?.querySelectorAll<HTMLInputElement>(".sheet-input");
  if (!inputs) return;
  const changes = Array.from(inputs)
    .filter((input) => input.value !== input.defaultValue)
    .map((input) => ({
      entity: input.dataset.entity || "",
      id: Number(input.dataset.id),
      field: input.dataset.field || "",
      value: input.value,
    }));
  if (!changes.length) {
    saveStatus.value = "没有修改";
    return;
  }
  try {
    const data = await updateQuotation(selectedCode.value, selectedInstanceId.value, changes);
    selectedCode.value = data.quotation_code;
    editing.value = false;
    saveStatus.value = "已保存";
    await Promise.all([loadHistory(), loadPreview()]);
  } catch (err: any) {
    ElMessage.error("保存失败：" + err.message);
  }
}

async function clearPrices(): Promise<void> {
  if (!selectedCode.value) return;
  if (selectedStatus.value === "quoted") {
    ElMessage.warning("已报价的成本分析表只能查看，不能清空计算结果");
    return;
  }
  try {
    await ElMessageBox.confirm(
      `确认清空 ${selectedCode.value} 的平台计算结果吗？将清空单价、材料金额、制程金额、费用小计和最终售价等结果，不会清空物料编码、用量、固定费用、订单参数和铜价参数。`,
      "确认清空计算结果",
      {
      type: "warning",
      },
    );
  } catch {
    return;
  }
  try {
    setCalcStatus("正在清空计算结果...", "info");
    const data = await clearUnitPricesApi(selectedCode.value, selectedInstanceId.value);
    await loadPreview();
    setCalcStatus(`已清空材料 ${data.cleared_materials ?? data.cleared ?? 0} 行、制程 ${data.cleared_processes ?? 0} 行，可重新一键计算`, "success");
  } catch (err: any) {
    setCalcStatus("", "info");
    ElMessage.error("清空计算结果失败：" + err.message);
  }
}

async function quoteCurrent(): Promise<void> {
  if (!selectedCode.value) return;
  try {
    await ElMessageBox.confirm(`确认将 ${selectedCode.value} 标记为已报价吗？标记后将只能查看。`, "标记已报价", { type: "warning" });
  } catch {
    return;
  }
  try {
    await markQuoted(selectedCode.value, selectedInstanceId.value);
    selectedStatus.value = "quoted";
    await loadHistory();
    await loadPreview();
    ElMessage.success("已标记为已报价，并生成报价快照");
  } catch (err: any) {
    ElMessage.error("标记失败：" + err.message);
  }
}

async function exportCurrent(): Promise<void> {
  if (!selectedCode.value) return;
  try {
    await exportExcel(selectedCode.value, selectedInstanceId.value);
  } catch (err: any) {
    ElMessage.error("导出失败：" + err.message);
  }
}

async function copyBpmNo(bpmNo: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(bpmNo);
    ElMessage.success("已复制 BPM流程号：" + bpmNo);
  } catch {
    const input = document.createElement("textarea");
    input.value = bpmNo;
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    document.body.removeChild(input);
    ElMessage.success("已复制 BPM流程号：" + bpmNo);
  }
}

function setCalcStatus(message: string, tone: StatusTone): void {
  calcStatus.value = message;
  calcStatusTone.value = tone;
}
</script>
