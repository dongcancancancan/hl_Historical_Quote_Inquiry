<template>
  <div class="review-shell">
    <header class="topbar">
      <div>
        <h1>已报价历史</h1>
        <p>查看已完成报价的成本分析表 · Vue 3</p>
      </div>
      <nav>
        <a href="/static/review-v2/index.html" @click.prevent="openInternalPage('/static/review-v2/index.html')">返回待报价工作台</a>
        <a href="/static/review-v2/batch.html" @click.prevent="openInternalPage('/static/review-v2/batch.html')">批量操作</a>
        <a href="/static/review-v2/copper-scenarios.html" @click.prevent="openInternalPage('/static/review-v2/copper-scenarios.html')">铜价区间测算</a>
        <span>{{ reviewerName }} · 审价科</span>
        <el-button size="small" text @click="logout">退出</el-button>
      </nav>
    </header>

    <main class="quoted-layout">
      <aside class="quote-list">
        <div class="list-head quoted-head">
          <div>
            <strong>已报价</strong>
            <el-tag size="small" type="success" effect="plain">{{ filteredItems.length }} / {{ quotedItems.length }}</el-tag>
          </div>
        </div>
        <el-input
          v-model="quotedSearch"
          class="list-search"
          size="small"
          clearable
          placeholder="搜索成本分析号 / BPM流程号 / 上传人..."
        />
        <el-scrollbar class="list-scroll">
          <el-empty v-if="!filteredItems.length" description="暂无已报价记录" :image-size="60" />
          <button
            v-for="item in filteredItems"
            :key="cardKey(item)"
            class="quote-card quoted-card"
            :class="{ active: isActive(item) }"
            @click="selectQuotation(item)"
          >
            <span class="code">{{ item.quotation_code }}</span>
            <span class="bpm-line">
              <span>BPM：{{ item.bpm_no || "-" }}</span>
              <el-button v-if="item.bpm_no" class="copy-button" size="small" text @click.stop="copyBpmNo(item.bpm_no)">
                复制
              </el-button>
            </span>
            <span class="muted">报价日期：{{ item.quote_date || "-" }}</span>
            <span class="muted">最终售价：{{ item.final_selling_price || "-" }}</span>
            <span class="muted ellipsis">{{ [item.customer_name, item.package_method, item.product_spec].filter(Boolean).join(" ") }}</span>
            <span class="muted">上传人：{{ item.upload_user || "-" }}</span>
            <span class="muted mono">上传时间：{{ formatTime(item.create_time) }}</span>
          </button>
        </el-scrollbar>
      </aside>

      <section class="sheet-panel">
        <div class="sheet-head">
          <div>
            <h2>{{ selectedCode || "选择已报价记录查看" }}</h2>
            <p>{{ selectedCode ? "已报价 · 快照只读" : "" }}</p>
          </div>
          <div v-if="selectedCode" class="sheet-actions">
            <el-button size="small" text type="warning" @click="showAllTraces">查看计算过程</el-button>
            <el-button size="small" text type="primary" @click="exportCurrent">导出 Excel</el-button>
          </div>
        </div>
        <div v-if="!selectedCode" class="empty-state">从左侧选择一条已报价记录</div>
        <div v-else-if="sheetLoading" class="empty-state">正在加载数据库内容...</div>
        <iframe v-show="selectedCode && !sheetLoading" ref="sheetFrame" class="sheet-frame" title="已报价成本分析表"></iframe>
      </section>
    </main>

    <TraceDialog
      v-model="traceVisible"
      :title="traceTitle"
      :loading="traceLoading"
      :groups="traceGroups"
      :skills="[]"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import TraceDialog from "./components/TraceDialog.vue";
import {
  assertReviewerSession,
  exportExcel,
  fetchPreview,
  fetchReviewHistory,
  fetchTraces,
  openInternalPage,
} from "./api";
import type { QuoteItem, TraceGroup } from "./types";

assertReviewerSession();

const reviewerName = sessionStorage.getItem("displayName") || sessionStorage.getItem("userName") || "";
const quotedItems = ref<QuoteItem[]>([]);
const quotedSearch = ref("");
const selectedCode = ref("");
const selectedInstanceId = ref<number | null>(null);
const sheetFrame = ref<HTMLIFrameElement | null>(null);
const sheetLoading = ref(false);
const traceVisible = ref(false);
const traceLoading = ref(false);
const traceTitle = ref("计算过程");
const traceGroups = ref<TraceGroup[]>([]);

const filteredItems = computed(() => {
  const kw = quotedSearch.value.trim().toUpperCase();
  if (!kw) return quotedItems.value;
  return quotedItems.value.filter((item) =>
    [
      item.quotation_code,
      item.bpm_no,
      item.customer_name,
      item.package_method,
      item.product_spec,
      item.upload_user,
      item.quote_date,
      item.final_selling_price,
    ]
      .filter(Boolean)
      .join(" ")
      .toUpperCase()
      .includes(kw),
  );
});

onMounted(() => {
  loadHistory().catch((err) => ElMessage.error("加载失败：" + err.message));
});

function logout(): void {
  sessionStorage.clear();
  window.location.href = "/static/login.html";
}

async function loadHistory(): Promise<void> {
  const data = await fetchReviewHistory();
  quotedItems.value = data.quoted || [];
}

async function selectQuotation(item: QuoteItem): Promise<void> {
  selectedCode.value = item.quotation_code;
  selectedInstanceId.value = item.instance_id ? Number(item.instance_id) : null;
  sheetLoading.value = true;
  await nextTick();
  try {
    const html = await fetchPreview(selectedCode.value, selectedInstanceId.value);
    if (sheetFrame.value) sheetFrame.value.srcdoc = html;
  } catch (err: any) {
    ElMessage.error("预览加载失败：" + err.message);
  } finally {
    sheetLoading.value = false;
  }
}

async function showAllTraces(): Promise<void> {
  if (!selectedCode.value) return;
  traceVisible.value = true;
  traceLoading.value = true;
  traceTitle.value = "计算过程";
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

async function exportCurrent(): Promise<void> {
  if (!selectedCode.value) return;
  try {
    await exportExcel(selectedCode.value, selectedInstanceId.value);
  } catch (err: any) {
    ElMessage.error("导出失败：" + err.message);
  }
}

async function copyBpmNo(bpmNo: string | undefined): Promise<void> {
  if (!bpmNo) return;
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

function cardKey(item: QuoteItem): string {
  return String(item.instance_id || item.quotation_code);
}

function isActive(item: QuoteItem): boolean {
  if (item.instance_id && selectedInstanceId.value) return Number(item.instance_id) === selectedInstanceId.value;
  return item.quotation_code === selectedCode.value;
}

function formatTime(value?: string): string {
  return value ? value.replace("T", " ").substring(0, 19) : "-";
}
</script>
