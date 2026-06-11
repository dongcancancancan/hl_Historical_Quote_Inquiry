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
      <div class="list-actions">
        <el-button size="small" text @click="expandAll">全部展开</el-button>
        <el-button size="small" text @click="collapseAll">全部折叠</el-button>
      </div>
      <el-scrollbar class="list-scroll">
        <el-empty v-if="!groupedItems.length" description="暂无记录" :image-size="60" />
        <template v-else>
          <div v-for="group in groupedItems" :key="group.key" class="bpm-group">
            <div class="bpm-group-header" @click="toggleGroup(group.key)">
              <el-icon class="bpm-group-chevron">
                <ArrowDown v-if="expandedGroups.has(group.key)" />
                <ArrowRight v-else />
              </el-icon>
              <span class="bpm-group-label">{{ group.label }}</span>
              <el-tag size="small" effect="plain" type="info">{{ group.items.length }}</el-tag>
              <el-button
                v-if="group.key !== '__no_bpm__'"
                class="bpm-copy-btn"
                size="small"
                text
                @click.stop="copyBpm(group.key)"
              >
                复制
              </el-button>
            </div>
            <div v-show="expandedGroups.has(group.key)" class="bpm-group-body">
              <button
                v-for="item in group.items"
                :key="cardKey(item)"
                class="quote-card"
                :class="{ active: isActive(item) }"
                @click="$emit('select', item)"
              >
                <span class="code">{{ item.quotation_code }}</span>
                <span class="muted">报价日期：{{ item.quote_date || "-" }}</span>
                <span class="muted ellipsis">{{ [item.customer_name, item.package_method, item.product_spec].filter(Boolean).join(" ") }}</span>
                <span class="muted">上传人：{{ item.upload_user || "-" }}</span>
                <span class="muted mono">上传时间：{{ formatTime(item.create_time) }}</span>
              </button>
            </div>
          </div>
        </template>
      </el-scrollbar>
    </template>
  </aside>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import { ElMessage } from "element-plus";
import { ArrowDown, ArrowLeft, ArrowRight } from "@element-plus/icons-vue";
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

const expandedGroups = reactive(new Set<string>());

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

interface BpmGroup {
  key: string;
  label: string;
  items: QuoteItem[];
}

const groupedItems = computed<BpmGroup[]>(() => {
  const map = new Map<string, QuoteItem[]>();
  for (const item of filteredItems.value) {
    const key = item.bpm_no || "__no_bpm__";
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(item);
  }
  const groups: BpmGroup[] = [];
  for (const [key, items] of map) {
    groups.push({
      key,
      label: key === "__no_bpm__" ? "未关联BPM" : `BPM：${key}`,
      items,
    });
  }
  // 有 BPM 的排前面，"未关联BPM" 排最后
  groups.sort((a, b) => {
    if (a.key === "__no_bpm__") return 1;
    if (b.key === "__no_bpm__") return -1;
    return a.key.localeCompare(b.key);
  });
  return groups;
});

// 数据变化时，新出现的分组自动展开
watch(
  () => props.items,
  () => {
    for (const g of groupedItems.value) {
      if (!expandedGroups.has(g.key)) {
        expandedGroups.add(g.key);
      }
    }
  },
  { immediate: true },
);

function toggleGroup(key: string): void {
  if (expandedGroups.has(key)) {
    expandedGroups.delete(key);
  } else {
    expandedGroups.add(key);
  }
}

function expandAll(): void {
  for (const g of groupedItems.value) {
    expandedGroups.add(g.key);
  }
}

function collapseAll(): void {
  expandedGroups.clear();
}

async function copyBpm(bpmNo: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(bpmNo);
    ElMessage.success("已复制：" + bpmNo);
  } catch {
    const input = document.createElement("textarea");
    input.value = bpmNo;
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    document.body.removeChild(input);
    ElMessage.success("已复制：" + bpmNo);
  }
}

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
