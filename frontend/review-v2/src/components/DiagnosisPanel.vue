<template>
  <aside class="diagnosis-panel">
    <div class="panel-head">
      <div>
        <strong>计算状态</strong>
        <p>{{ selectedCode ? selectedCode : "选择报价单后查看计算状态" }}</p>
      </div>
    </div>
    <el-scrollbar class="diagnosis-body">
      <el-empty v-if="!selectedCode" description="暂无计算状态" :image-size="68" />
      <div v-else-if="!diagnosis" class="diagnosis-placeholder">
        <el-alert title="还没有计算异常。计算失败后这里会直接展示规则提示。" type="info" :closable="false" />
        <p>批量计算失败时，请回到对应成本分析表查看这里的规则提示。</p>
      </div>
      <div v-else class="diagnosis-result">
        <div class="diagnosis-card">
          <div class="diagnosis-card-head">
            <span>{{ modeText }}</span>
            <el-tag size="small" :type="diagnosis.mode === 'llm' ? 'primary' : 'info'" effect="plain">
              {{ diagnosis.mode === "llm" ? "AI" : "规则" }}
            </el-tag>
          </div>
          <div class="diagnosis-summary">
            <p
              v-for="(line, index) in summaryLines"
              :key="index"
              :class="line.className"
            >
              {{ line.text }}
            </p>
          </div>
        </div>
        <div class="skill-list">
          <div class="skill-list-head">已注册 Skill</div>
          <div class="skill-tags">
            <el-tag v-for="skill in diagnosis.skills || []" :key="skill.id" size="small" effect="plain">
              {{ skill.name }}
            </el-tag>
          </div>
        </div>
      </div>
    </el-scrollbar>
    <div class="panel-foot">
      <el-button class="wide" plain type="primary" @click="$emit('skills')">查看计算 Skill</el-button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { DiagnosisResult } from "../types";

const props = defineProps<{
  selectedCode: string;
  diagnosis: DiagnosisResult | null;
}>();

defineEmits<{
  skills: [];
}>();

const modeText = computed(() => {
  if (props.diagnosis?.mode === "local") return "前端即时提示";
  return "规则异常提示";
});

const summaryLines = computed(() => {
  const raw = props.diagnosis?.summary || "";
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const cleaned = line
        .replace(/^[-•]\s*/, "")
        .replace(/\*\*/g, "")
        .trim();
      const isHeading = /^\d+[.、]/.test(cleaned) || /^【.+】$/.test(cleaned);
      return {
        text: cleaned,
        className: isHeading ? "summary-line heading" : "summary-line",
      };
    });
});
</script>
