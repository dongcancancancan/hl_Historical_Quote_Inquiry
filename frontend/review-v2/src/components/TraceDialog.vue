<template>
  <el-dialog v-model="visibleModel" :title="title" width="980px" top="6vh" class="trace-dialog">
    <div v-if="loading" class="trace-loading">正在加载计算过程...</div>
    <el-empty v-else-if="!groups.length && !skills.length" description="暂无计算过程" />
    <div v-else class="trace-groups">
      <section v-for="group in groups" :key="group.title" class="trace-group">
        <div class="group-title">{{ group.title }}</div>
        <el-empty v-if="!group.rows.length" description="暂无记录" :image-size="48" />
        <article v-for="row in group.rows" :key="row.id" class="trace-row">
          <div class="trace-result">{{ row.display_label || row.field_name }} = {{ row.result_value || "" }}</div>
          <div class="trace-formula">{{ row.formula || "" }}</div>
          <pre>{{ row.process_text || "" }}</pre>
        </article>
      </section>

      <section v-for="skill in skills" :key="skill.id" class="trace-group">
        <div class="group-title">{{ skill.order }}. {{ skill.name }}</div>
        <div class="trace-formula">{{ skill.description }}</div>
        <div class="skill-tags">
          <el-tag v-for="capability in skill.capabilities" :key="capability" size="small" effect="plain">
            {{ capability }}
          </el-tag>
        </div>
      </section>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { SkillItem, TraceGroup } from "../types";

const props = defineProps<{
  modelValue: boolean;
  title: string;
  loading: boolean;
  groups: TraceGroup[];
  skills: SkillItem[];
}>();

const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();

const visibleModel = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit("update:modelValue", value),
});
</script>
