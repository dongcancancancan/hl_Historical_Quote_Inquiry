<template>
  <aside class="diagnosis-panel">
    <div class="panel-head">
      <div>
        <strong>智能诊断</strong>
        <p>{{ selectedCode ? selectedCode : "选择报价单后可诊断计算异常" }}</p>
      </div>
      <el-button size="small" type="primary" plain @click="$emit('diagnose')">
        {{ diagnosis ? "重新诊断" : "智能诊断" }}
      </el-button>
    </div>
    <el-scrollbar class="diagnosis-body">
      <div v-if="loading" class="diagnosis-loading">诊断中，请稍候...</div>
      <el-empty v-else-if="!selectedCode" description="暂无诊断结果" :image-size="68" />
      <div v-else-if="!diagnosis" class="diagnosis-placeholder">
        <el-alert title="还没有诊断结果，点击智能诊断会调用诊断服务。" type="info" :closable="false" />
        <p>铜价未填、参数未保存、格式错误这类输入态问题会直接在前端提示；材料、制程或公式链路问题才会进入智能诊断。</p>
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
  loading: boolean;
}>();

defineEmits<{
  diagnose: [];
  skills: [];
}>();

const modeText = computed(() => {
  if (props.diagnosis?.mode === "llm") return "LLM 辅助诊断";
  if (props.diagnosis?.mode === "local") return "前端即时提示";
  return "规则诊断";
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
