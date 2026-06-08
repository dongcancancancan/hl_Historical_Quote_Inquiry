<template>
  <div class="review-shell">
    <header class="topbar">
      <div>
        <h1>铜价区间测算</h1>
        <p>按 BPM 流程号测算各铜段最终售价，不覆盖正式报价数据 · Vue 3</p>
      </div>
      <nav>
        <a href="/static/review-v2/index.html" @click.prevent="openInternalPage('/static/review-v2/index.html')">返回工作台</a>
        <a href="/static/review-v2/batch.html" @click.prevent="openInternalPage('/static/review-v2/batch.html')">批量操作</a>
        <span>{{ reviewerName }} · 审价科</span>
        <el-button size="small" text @click="logout">退出</el-button>
      </nav>
    </header>

    <main class="scenario-page">
      <section class="batch-query">
        <el-form class="batch-query-form" inline @submit.prevent>
          <el-form-item label="BPM流程号">
            <el-input
              v-model.trim="bpmNo"
              class="batch-bpm-input"
              placeholder="EG-B015-26050127"
              clearable
              @keyup.enter="calculateScenario"
              @blur="bpmNo = bpmNo.toUpperCase()"
            />
          </el-form-item>
          <el-button type="primary" :loading="loading" @click="calculateScenario">开始测算</el-button>
          <span class="batch-status">{{ statusText }}</span>
        </el-form>
      </section>

      <section class="scenario-result-panel">
        <div class="batch-panel-head">
          <div>
            <strong>最终售价矩阵</strong>
            <span v-if="result">{{ result.bpm_no }} · {{ result.items.length }} 张成本分析表</span>
          </div>
        </div>

        <div v-if="!result && !loading" class="empty-state scenario-empty">输入 BPM 流程号后开始测算</div>
        <div v-else-if="loading" class="empty-state scenario-empty">测算中...</div>
        <el-empty v-else-if="!result?.items.length" description="暂无结果" />
        <el-scrollbar v-else class="scenario-scroll">
          <table class="scenario-table">
            <thead>
              <tr>
                <th class="scenario-sticky">成本分析号</th>
                <th>当前最终售价</th>
                <th v-for="band in result.bands" :key="band.label">
                  <span class="mono">{{ band.label }}</span>
                  <small>按 {{ band.copper_price }}</small>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in result.items" :key="item.quotation_code">
                <td class="scenario-sticky">
                  <div class="mono strong">{{ item.quotation_code }}</div>
                  <div class="muted scenario-spec">{{ item.product_spec || "-" }}</div>
                  <div v-if="item.errors?.length" class="scenario-error">{{ item.errors.join("；") }}</div>
                </td>
                <td class="mono numeric">{{ item.current_final_selling_price || "-" }}</td>
                <td
                  v-for="(cell, index) in item.bands"
                  :key="item.quotation_code + '-' + index"
                  class="mono numeric"
                  :class="{ 'scenario-error-cell': !!cell.error }"
                >
                  <el-tooltip v-if="cell.error" :content="cell.error" placement="top">
                    <span>{{ cell.error }}</span>
                  </el-tooltip>
                  <span v-else>{{ cell.final_selling_price || "-" }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </el-scrollbar>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { ElMessage } from "element-plus";
import { assertReviewerSession, calculateCopperScenarios, openInternalPage } from "./api";
import type { CopperScenarioResponse } from "./types";

assertReviewerSession();

const reviewerName = sessionStorage.getItem("displayName") || sessionStorage.getItem("userName") || "";
const bpmNo = ref("");
const statusText = ref("");
const loading = ref(false);
const result = ref<CopperScenarioResponse | null>(null);

function logout(): void {
  sessionStorage.clear();
  window.location.href = "/static/login.html";
}

async function calculateScenario(): Promise<void> {
  const code = bpmNo.value.trim().toUpperCase();
  if (!code) {
    ElMessage.warning("请填写 BPM流程号");
    return;
  }
  bpmNo.value = code;
  loading.value = true;
  statusText.value = "测算中...";
  try {
    result.value = await calculateCopperScenarios(code);
    statusText.value = `共 ${result.value.items.length} 张成本分析表`;
  } catch (err: any) {
    result.value = null;
    statusText.value = "";
    ElMessage.error("测算失败：" + err.message);
  } finally {
    loading.value = false;
  }
}
</script>
