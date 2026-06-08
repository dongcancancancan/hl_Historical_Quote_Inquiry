<template>
  <aside class="quote-list" :class="{ collapsed }">
    <button v-if="collapsed" class="collapsed-button" @click="$emit('toggle')">
      <span>待报价</span>
      <b>{{ items.length }}</b>
    </button>
    <template v-else>
      <div class="list-head">
        <div>
          <strong>待报价</strong>
          <el-tag size="small" type="warning" effect="plain">{{ items.length }}</el-tag>
        </div>
        <el-button size="small" text @click="$emit('toggle')">
          <el-icon><ArrowLeft /></el-icon>
        </el-button>
      </div>
      <el-input
        :model-value="search"
        class="list-search"
        size="small"
        clearable
        placeholder="搜索成本分析号 / BPM流程号..."
        @update:model-value="$emit('update:search', $event)"
      />
      <el-scrollbar class="list-scroll">
        <el-empty v-if="!filteredItems.length" description="暂无记录" :image-size="60" />
        <button
          v-for="item in filteredItems"
          :key="cardKey(item)"
          class="quote-card"
          :class="{ active: isActive(item) }"
          @click="$emit('select', item)"
        >
          <span class="code">{{ item.quotation_code }}</span>
          <span class="bpm-line">
            <span>BPM：{{ item.bpm_no || "-" }}</span>
            <el-button
              v-if="item.bpm_no"
              class="copy-button"
              size="small"
              text
              @click.stop="$emit('copy-bpm', item.bpm_no)"
            >
              复制
            </el-button>
          </span>
          <span class="muted">报价日期：{{ item.quote_date || "-" }}</span>
          <span class="muted ellipsis">{{ [item.customer_name, item.package_method, item.product_spec].filter(Boolean).join(" ") }}</span>
          <span class="muted">上传人：{{ item.upload_user || "-" }}</span>
          <span class="muted mono">上传时间：{{ formatTime(item.create_time) }}</span>
        </button>
      </el-scrollbar>
    </template>
  </aside>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { ArrowLeft } from "@element-plus/icons-vue";
import type { QuoteItem } from "../types";

const props = defineProps<{
  items: QuoteItem[];
  search: string;
  selectedInstanceId: number | null;
  selectedCode: string;
  collapsed: boolean;
}>();

defineEmits<{
  "update:search": [value: string];
  select: [item: QuoteItem];
  toggle: [];
  "copy-bpm": [bpmNo: string];
}>();

const filteredItems = computed(() => {
  const kw = props.search.trim().toUpperCase();
  if (!kw) return props.items;
  return props.items.filter((item) =>
    [
      item.quotation_code,
      item.bpm_no,
      item.customer_name,
      item.package_method,
      item.product_spec,
      item.upload_user,
    ]
      .filter(Boolean)
      .join(" ")
      .toUpperCase()
      .includes(kw),
  );
});

function cardKey(item: QuoteItem): string {
  return String(item.instance_id || item.quotation_code);
}

function isActive(item: QuoteItem): boolean {
  if (item.instance_id && props.selectedInstanceId) return Number(item.instance_id) === props.selectedInstanceId;
  return item.quotation_code === props.selectedCode;
}

function formatTime(value?: string): string {
  return value ? value.replace("T", " ").substring(0, 19) : "-";
}
</script>
