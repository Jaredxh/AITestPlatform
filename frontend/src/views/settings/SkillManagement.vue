<template>
  <div class="sk">
    <page-header
      title="技能包管理"
      subtitle="项目级 SKILL.md 治理：导入 / 安全扫描 / 触发词召回 / Chat 激活"
      icon="i-carbon-tools-alt"
    >
      <template #extra>
        <n-space :size="8">
          <n-button quaternary :disabled="!projectId" @click="goToUsageStats">
            <template #icon><span class="i-carbon-analytics" /></template>
            使用统计
          </n-button>
          <n-button :disabled="!projectId" @click="openImport">
            <template #icon><span class="i-carbon-upload" /></template>
            导入技能包
          </n-button>
          <n-button type="primary" :disabled="!projectId" @click="handleCreate">
            <template #icon><span class="i-carbon-add" /></template>
            新建技能
          </n-button>
        </n-space>
      </template>
    </page-header>

    <div class="sk__layout" :class="{ 'sk__layout--collapsed': collapsed }">
      <!-- 左侧：可折叠筛选栏 -->
      <aside class="sk__side">
        <div class="sk__side-head">
          <span v-if="!collapsed" class="sk__side-title">筛选</span>
          <n-button
            quaternary
            size="tiny"
            class="sk__side-toggle"
            :title="collapsed ? '展开筛选' : '收起筛选'"
            @click="toggleCollapsed"
          >
            <template #icon>
              <span :class="collapsed ? 'i-carbon-side-panel-open' : 'i-carbon-side-panel-close'" />
            </template>
          </n-button>
        </div>

        <!-- 展开态：三段筛选 -->
        <div v-if="!collapsed" class="sk__side-body">
          <div class="sk__group">
            <div class="sk__group-label">来源</div>
            <button
              v-for="opt in sourceOptions"
              :key="`src-${opt.value}`"
              type="button"
              class="sk__chip-row"
              :class="{ 'sk__chip-row--active': filters.source === opt.value }"
              @click="onFilter('source', opt.value)"
            >
              <span class="sk__chip-icon" :class="opt.icon" />
              <span class="sk__chip-label">{{ opt.label }}</span>
              <span class="sk__chip-count">{{ sourceCounts[opt.value] }}</span>
            </button>
          </div>

          <div class="sk__group">
            <div class="sk__group-label">激活模式</div>
            <button
              v-for="opt in modeOptions"
              :key="`mode-${opt.value}`"
              type="button"
              class="sk__chip-row"
              :class="{ 'sk__chip-row--active': filters.activation_mode === opt.value }"
              @click="onFilter('activation_mode', opt.value)"
            >
              <span class="sk__chip-label">{{ opt.label }}</span>
              <span class="sk__chip-count">{{ modeCounts[opt.value] }}</span>
            </button>
          </div>

          <div class="sk__group">
            <div class="sk__group-label">状态</div>
            <button
              v-for="opt in enabledOptions"
              :key="`en-${opt.value}`"
              type="button"
              class="sk__chip-row"
              :class="{ 'sk__chip-row--active': enabledKey === opt.value }"
              @click="onEnabledChange(opt.value)"
            >
              <span class="sk__chip-label">{{ opt.label }}</span>
              <span class="sk__chip-count">{{ enabledCounts[opt.value] }}</span>
            </button>
          </div>

          <n-button
            text
            size="tiny"
            class="sk__reset"
            :disabled="!hasFilter"
            @click="resetFilters"
          >
            <template #icon><span class="i-carbon-reset" /></template>
            重置筛选
          </n-button>
        </div>

        <!-- 折叠态：仅 icon -->
        <div v-else class="sk__side-icons">
          <button
            v-for="opt in sourceOptions.filter((o) => o.value)"
            :key="`isrc-${opt.value}`"
            type="button"
            class="sk__icon-btn"
            :class="{ 'sk__icon-btn--active': filters.source === opt.value }"
            :title="`来源 · ${opt.label}`"
            @click="onFilter('source', opt.value)"
          >
            <span :class="opt.icon" />
            <span v-if="sourceCounts[opt.value]" class="sk__icon-badge">
              {{ sourceCounts[opt.value] }}
            </span>
          </button>
          <div class="sk__icon-divider" />
          <button
            type="button"
            class="sk__icon-btn"
            :class="{ 'sk__icon-btn--active': enabledFilter === false }"
            title="仅看已禁用"
            @click="onEnabledChange(enabledFilter === false ? 'all' : 'disabled')"
          >
            <span class="i-carbon-pause-outline" />
            <span v-if="enabledCounts.disabled" class="sk__icon-badge">
              {{ enabledCounts.disabled }}
            </span>
          </button>
        </div>
      </aside>

      <!-- 右侧：主区 -->
      <section class="sk__main">
        <div class="sk__toolbar">
          <n-input
            v-model:value="filters.search"
            placeholder="搜索名称 / slug / 描述"
            clearable
            class="sk__search"
            @input="onSearchInput"
          >
            <template #prefix><span class="i-carbon-search" /></template>
          </n-input>
          <div class="sk__toolbar-meta">
            <span class="sk__count">
              共 <strong>{{ rows.length }}</strong> 条
              <template v-if="hasFilter">（已筛选）</template>
            </span>
            <n-button quaternary size="small" :loading="loading" @click="fetchList">
              <template #icon><span class="i-carbon-renew" /></template>
              刷新
            </n-button>
          </div>
        </div>

        <!-- 已激活筛选 chips -->
        <div v-if="hasFilter" class="sk__chips">
          <n-tag
            v-for="chip in activeChips"
            :key="chip.key"
            closable
            size="small"
            :type="chip.type as any"
            @close="chip.clear"
          >
            <span class="sk__chip-key">{{ chip.dim }}</span>
            <span>{{ chip.label }}</span>
          </n-tag>
          <n-button text size="tiny" type="primary" @click="resetFilters">清除全部</n-button>
        </div>

        <n-spin :show="loading">
          <div v-if="rows.length > 0" class="sk__cards">
            <article
              v-for="row in rows"
              :key="row.id"
              class="sk__card"
              :class="{
                'sk__card--disabled': !row.is_enabled,
                'sk__card--builtin': row.source === 'built_in',
              }"
              @click="goToEdit(row.id)"
            >
              <header class="sk__card-head">
                <div class="sk__card-title">
                  <span class="i-carbon-package sk__card-icon" />
                  <h3 class="sk__card-name">{{ row.name }}</h3>
                  <safety-badge :status="row.safety_scan_status" />
                  <n-tag
                    v-if="row.source === 'built_in'"
                    size="tiny"
                    type="warning"
                    :bordered="false"
                  >
                    内置
                  </n-tag>
                  <n-tag
                    v-else-if="row.source === 'imported'"
                    size="tiny"
                    type="info"
                    :bordered="false"
                  >
                    导入
                  </n-tag>
                  <n-tag v-else size="tiny" :bordered="false">自定义</n-tag>
                  <n-tag
                    v-if="!row.is_enabled"
                    size="tiny"
                    type="default"
                    :bordered="false"
                    class="sk__card-disabled-tag"
                  >
                    已禁用
                  </n-tag>
                </div>
                <div class="sk__card-stats">
                  <div class="sk__card-stat-label">近 7 天</div>
                  <div class="sk__card-stat-value">
                    <strong>{{ usageMap[row.id]?.count ?? 0 }}</strong>
                    <span class="sk__card-stat-rate">
                      / {{ formatRate(usageMap[row.id]?.success_rate) }}
                    </span>
                  </div>
                </div>
              </header>

              <div class="sk__card-meta">
                <code class="sk__card-slug">{{ row.slug }}</code>
                <span class="sk__card-meta-sep">·</span>
                <span class="sk__card-meta-item">
                  <span class="i-carbon-version" />
                  v{{ row.semantic_version }}
                </span>
                <span class="sk__card-meta-sep">·</span>
                <span class="sk__card-meta-item">
                  {{ activationLabel(row.activation_mode) }}
                </span>
                <span v-if="row.category" class="sk__card-meta-sep">·</span>
                <span v-if="row.category" class="sk__card-meta-item">
                  {{ row.category }}
                </span>
              </div>

              <p v-if="row.description" class="sk__card-desc">
                {{ row.description }}
              </p>

              <div v-if="row.triggers && row.triggers.length > 0" class="sk__card-tags">
                <span class="sk__card-tags-label">触发词</span>
                <n-tag
                  v-for="t in row.triggers.slice(0, 5)"
                  :key="t"
                  size="tiny"
                  type="info"
                  :bordered="false"
                >
                  {{ t }}
                </n-tag>
                <n-tag
                  v-if="row.triggers.length > 5"
                  size="tiny"
                  :bordered="false"
                >
                  +{{ row.triggers.length - 5 }}
                </n-tag>
              </div>

              <footer class="sk__card-actions" @click.stop>
                <n-button size="small" type="primary" ghost @click="goToEdit(row.id)">
                  <template #icon><span class="i-carbon-edit" /></template>
                  编辑
                </n-button>
                <n-button
                  size="small"
                  :type="row.is_enabled ? 'warning' : 'success'"
                  ghost
                  :loading="togglingId === row.id"
                  @click="quickToggle(row)"
                >
                  <template #icon>
                    <span :class="row.is_enabled ? 'i-carbon-pause' : 'i-carbon-play'" />
                  </template>
                  {{ row.is_enabled ? "禁用" : "启用" }}
                </n-button>
                <n-button
                  size="small"
                  quaternary
                  :loading="exportingId === row.id"
                  @click="exportSkill(row)"
                >
                  <template #icon><span class="i-carbon-download" /></template>
                  导出
                </n-button>
                <n-popconfirm
                  v-if="row.source !== 'built_in'"
                  :positive-text="'确认删除'"
                  :negative-text="'取消'"
                  :show-icon="false"
                  @positive-click="deleteRow(row)"
                >
                  <template #trigger>
                    <n-button size="small" type="error" ghost>
                      <template #icon><span class="i-carbon-trash-can" /></template>
                      删除
                    </n-button>
                  </template>
                  <div class="sk__danger-tip">
                    <strong>此操作不可逆。</strong>
                    确认删除技能「{{ row.name }}」吗？相关版本历史与扫描记录将一并清除。
                  </div>
                </n-popconfirm>
                <span v-else class="sk__card-locked" title="内置技能受平台保护，不可删除">
                  <span class="i-carbon-locked" />
                  内置不可删
                </span>
              </footer>
            </article>
          </div>

          <app-empty
            v-else-if="!loading"
            icon="i-carbon-tools-alt"
            :title="hasFilter ? '当前筛选下没有匹配的技能' : '该项目还没有技能'"
            :description="hasFilter ? '尝试调整左侧筛选或清空搜索关键词' : '点击右上角「导入技能包」「新建技能」开始添加'"
          />
        </n-spin>
      </section>
    </div>

    <skill-import-dialog
      v-model:show="importVisible"
      :project-id="projectId"
      @imported="fetchList"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  NButton,
  NInput,
  NPopconfirm,
  NSpace,
  NSpin,
  NTag,
  useMessage,
} from "naive-ui";
import PageHeader from "@/components/common/PageHeader.vue";
import AppEmpty from "@/components/common/AppEmpty.vue";
import SafetyBadge from "@/components/skills/SafetyBadge.vue";
import SkillImportDialog from "@/components/skills/SkillImportDialog.vue";
import { useProjectStore } from "@/stores/project";
import {
  deleteSkillApi,
  downloadSkillZip,
  getSkillUsageStatsApi,
  listSkillsApi,
  toggleSkillApi,
  SKILL_ACTIVATION_LABEL,
  type SkillActivationMode,
  type SkillListItem,
  type SkillSource,
} from "@/services/skills";

const router = useRouter();
const projectStore = useProjectStore();
const message = useMessage();

const projectId = computed(() => projectStore.currentProjectId || "");
const loading = ref(false);
const allRows = ref<SkillListItem[]>([]);
const usageMap = ref<Record<string, { count: number; success_rate: number }>>({});

const importVisible = ref(false);
const togglingId = ref<string | null>(null);
const exportingId = ref<string | null>(null);

const COLLAPSE_KEY = "skills.filterCollapsed";
const collapsed = ref<boolean>(localStorage.getItem(COLLAPSE_KEY) === "1");

const filters = reactive<{
  source: "" | SkillSource;
  activation_mode: "" | SkillActivationMode;
  search: string;
}>({
  source: "",
  activation_mode: "",
  search: "",
});

const enabledFilter = ref<boolean | null>(null);
const searchTimer = ref<ReturnType<typeof setTimeout> | null>(null);

interface Option<V extends string> {
  label: string;
  value: V;
  icon?: string;
}

const sourceOptions: Option<"" | SkillSource>[] = [
  { label: "全部", value: "", icon: "i-carbon-list-boxes" },
  { label: "内置", value: "built_in", icon: "i-carbon-cube" },
  { label: "自定义", value: "custom", icon: "i-carbon-edit" },
  { label: "导入", value: "imported", icon: "i-carbon-upload" },
];

const modeOptions: Option<"" | SkillActivationMode>[] = [
  { label: "全部", value: "" },
  { label: "Agent 调用", value: "agent_callable" },
  { label: "触发词", value: "trigger" },
  { label: "始终激活", value: "always" },
  { label: "手动", value: "manual" },
];

const enabledOptions: Option<"all" | "enabled" | "disabled">[] = [
  { label: "全部", value: "all" },
  { label: "已启用", value: "enabled" },
  { label: "已禁用", value: "disabled" },
];

const enabledKey = computed<"all" | "enabled" | "disabled">(() => {
  if (enabledFilter.value === true) return "enabled";
  if (enabledFilter.value === false) return "disabled";
  return "all";
});

const hasFilter = computed(
  () =>
    !!filters.source ||
    !!filters.activation_mode ||
    !!filters.search ||
    enabledFilter.value !== null,
);

// 客户端按 search/source/activation_mode/enabled 过滤一份；为统计准确度，sourceCounts 等基于全量 allRows 计算
const rows = computed<SkillListItem[]>(() => {
  let list = allRows.value;
  if (filters.source) list = list.filter((r) => r.source === filters.source);
  if (filters.activation_mode) {
    list = list.filter((r) => r.activation_mode === filters.activation_mode);
  }
  if (enabledFilter.value !== null) {
    list = list.filter((r) => r.is_enabled === enabledFilter.value);
  }
  const kw = filters.search.trim().toLowerCase();
  if (kw) {
    list = list.filter(
      (r) =>
        r.name.toLowerCase().includes(kw) ||
        r.slug.toLowerCase().includes(kw) ||
        (r.description || "").toLowerCase().includes(kw),
    );
  }
  return [...list].sort((a, b) => {
    const ua = usageMap.value[a.id]?.count ?? 0;
    const ub = usageMap.value[b.id]?.count ?? 0;
    if (ua !== ub) return ub - ua;
    return a.name.localeCompare(b.name);
  });
});

const sourceCounts = computed<Record<string, number>>(() => {
  const acc: Record<string, number> = { "": allRows.value.length };
  for (const r of allRows.value) acc[r.source] = (acc[r.source] || 0) + 1;
  return acc;
});

const modeCounts = computed<Record<string, number>>(() => {
  const acc: Record<string, number> = { "": allRows.value.length };
  for (const r of allRows.value) {
    acc[r.activation_mode] = (acc[r.activation_mode] || 0) + 1;
  }
  return acc;
});

const enabledCounts = computed(() => {
  const total = allRows.value.length;
  const enabled = allRows.value.filter((r) => r.is_enabled).length;
  return { all: total, enabled, disabled: total - enabled };
});

interface ActiveChip {
  key: string;
  dim: string;
  label: string;
  type: "default" | "info" | "warning" | "success" | "error";
  clear: () => void;
}

const activeChips = computed<ActiveChip[]>(() => {
  const list: ActiveChip[] = [];
  if (filters.source) {
    list.push({
      key: "source",
      dim: "来源",
      label: sourceOptions.find((o) => o.value === filters.source)?.label || filters.source,
      type: "info",
      clear: () => onFilter("source", ""),
    });
  }
  if (filters.activation_mode) {
    list.push({
      key: "mode",
      dim: "激活",
      label:
        modeOptions.find((o) => o.value === filters.activation_mode)?.label ||
        filters.activation_mode,
      type: "info",
      clear: () => onFilter("activation_mode", ""),
    });
  }
  if (enabledFilter.value !== null) {
    list.push({
      key: "enabled",
      dim: "状态",
      label: enabledFilter.value ? "已启用" : "已禁用",
      type: enabledFilter.value ? "success" : "warning",
      clear: () => onEnabledChange("all"),
    });
  }
  if (filters.search) {
    list.push({
      key: "search",
      dim: "搜索",
      label: filters.search,
      type: "default",
      clear: () => {
        filters.search = "";
      },
    });
  }
  return list;
});

function toggleCollapsed() {
  collapsed.value = !collapsed.value;
  localStorage.setItem(COLLAPSE_KEY, collapsed.value ? "1" : "0");
}

function onFilter(field: "source" | "activation_mode", v: string) {
  if (field === "source") filters.source = v as SkillSource | "";
  else filters.activation_mode = v as SkillActivationMode | "";
}

function onEnabledChange(v: "all" | "enabled" | "disabled") {
  enabledFilter.value = v === "enabled" ? true : v === "disabled" ? false : null;
}

function onSearchInput() {
  if (searchTimer.value) clearTimeout(searchTimer.value);
  searchTimer.value = setTimeout(() => {
    /* 触发 computed 重算 */
  }, 200);
}

function resetFilters() {
  filters.source = "";
  filters.activation_mode = "";
  filters.search = "";
  enabledFilter.value = null;
}

function activationLabel(mode: SkillActivationMode): string {
  return SKILL_ACTIVATION_LABEL[mode] ?? mode;
}

function formatRate(rate: number | undefined): string {
  if (rate === undefined || rate === null) return "—";
  return `${(rate * 100).toFixed(0)}%`;
}

async function fetchList() {
  if (!projectId.value) {
    allRows.value = [];
    return;
  }
  loading.value = true;
  try {
    const [listRes, statsRes] = await Promise.all([
      listSkillsApi(projectId.value, { page_size: 200 }),
      getSkillUsageStatsApi(projectId.value, 7).catch(() => null),
    ]);

    if (listRes.success) {
      allRows.value = listRes.data.items;
    }
    if (statsRes && statsRes.success) {
      usageMap.value = statsRes.data || {};
    } else {
      usageMap.value = {};
    }
  } catch {
    message.error("获取技能列表失败");
  } finally {
    loading.value = false;
  }
}

function openImport() {
  importVisible.value = true;
}

function handleCreate() {
  router.push({ name: "SkillEditor", params: { id: "new" } });
}

function goToUsageStats() {
  router.push({ name: "SkillUsageStats" });
}

function goToEdit(skillId: string) {
  router.push({ name: "SkillEditor", params: { id: skillId } });
}

async function quickToggle(row: SkillListItem) {
  togglingId.value = row.id;
  try {
    const res = await toggleSkillApi(row.id);
    if (res.success) {
      message.success(res.data.is_enabled ? "已启用" : "已禁用");
      // 局部更新避免整体闪烁
      const idx = allRows.value.findIndex((r) => r.id === row.id);
      if (idx >= 0) allRows.value[idx] = { ...allRows.value[idx], is_enabled: res.data.is_enabled };
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : "操作失败");
  } finally {
    togglingId.value = null;
  }
}

async function exportSkill(row: SkillListItem) {
  exportingId.value = row.id;
  try {
    await downloadSkillZip(row.id, row.slug);
  } catch (e) {
    message.error(e instanceof Error ? e.message : "导出失败");
  } finally {
    exportingId.value = null;
  }
}

async function deleteRow(row: SkillListItem) {
  try {
    const res = await deleteSkillApi(row.id);
    if (res.success) {
      message.success(`已删除技能「${row.name}」`);
      allRows.value = allRows.value.filter((r) => r.id !== row.id);
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : "删除失败");
  }
}

watch(() => projectId.value, fetchList);
onMounted(fetchList);
</script>

<style scoped>
.sk__layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 16px;
  align-items: start;
  transition: grid-template-columns var(--duration-fast, 0.18s) var(--easing-standard, ease);
}

.sk__layout--collapsed {
  grid-template-columns: 56px 1fr;
}

/* 侧栏 */
.sk__side {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
  position: sticky;
  top: 16px;
}

.sk__side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border-subtle);
  min-height: 40px;
}

.sk__side-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.6px;
}

.sk__side-toggle {
  margin-left: auto;
}

.sk__side-body {
  padding: 6px 8px 12px;
}

.sk__group + .sk__group {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--border-subtle);
}

.sk__group-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 6px 10px 4px;
}

.sk__chip-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border: none;
  background: transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  color: var(--text-secondary);
  text-align: left;
  transition: background var(--duration-fast, 0.18s) ease;
}

.sk__chip-row:hover {
  background: var(--bg-page-soft);
}

.sk__chip-row--active {
  background: var(--brand-primary-soft);
  color: var(--brand-primary);
  font-weight: 500;
}

.sk__chip-icon {
  font-size: 14px;
}

.sk__chip-label {
  flex: 1;
}

.sk__chip-count {
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--bg-page-soft);
  padding: 1px 6px;
  border-radius: 10px;
  min-width: 20px;
  text-align: center;
}

.sk__chip-row--active .sk__chip-count {
  background: rgba(255, 255, 255, 0.6);
  color: var(--brand-primary);
}

.sk__reset {
  width: 100%;
  margin-top: 12px;
  justify-content: center;
}

.sk__side-icons {
  padding: 6px 6px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: center;
}

.sk__icon-btn {
  position: relative;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-md);
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 16px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.sk__icon-btn:hover {
  background: var(--bg-page-soft);
  color: var(--text-primary);
}

.sk__icon-btn--active {
  background: var(--brand-primary-soft);
  color: var(--brand-primary);
}

.sk__icon-badge {
  position: absolute;
  top: 2px;
  right: 2px;
  font-size: 10px;
  background: var(--brand-primary);
  color: #fff;
  border-radius: 8px;
  padding: 0 4px;
  min-width: 14px;
  height: 14px;
  line-height: 14px;
  text-align: center;
}

.sk__icon-divider {
  width: 24px;
  height: 1px;
  background: var(--border-subtle);
  margin: 4px 0;
}

/* 主区 */
.sk__main {
  min-width: 0;
}

.sk__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0 12px;
}

.sk__search {
  max-width: 360px;
}

.sk__toolbar-meta {
  display: flex;
  align-items: center;
  gap: 12px;
}

.sk__count {
  font-size: 13px;
  color: var(--text-tertiary);
}

.sk__count strong {
  color: var(--text-primary);
  font-weight: 600;
}

.sk__chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 0 0 12px;
}

.sk__chip-key {
  color: var(--text-tertiary);
  margin-right: 4px;
}

/* 卡片栈 — 关键：用 ``minmax(min(100%, 420px), 1fr)`` 保证容器窄于 420px 时
 * 单列回退而不是把卡片硬撑到 420px 撑出整个页面横向滚动 */
.sk__cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 420px), 1fr));
  gap: 14px;
}

.sk__card {
  position: relative;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px 14px;
  cursor: pointer;
  transition:
    transform var(--duration-fast, 0.18s) ease,
    box-shadow var(--duration-fast, 0.18s) ease,
    border-color var(--duration-fast, 0.18s) ease;
}

.sk__card:hover {
  border-color: var(--brand-primary-soft-strong, var(--brand-primary));
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

.sk__card--disabled {
  opacity: 0.7;
}

.sk__card--builtin::before {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: linear-gradient(
    to bottom,
    var(--color-warning, #f0a020),
    var(--color-warning-2, #d0021b)
  );
  border-radius: var(--radius-lg) 0 0 var(--radius-lg);
}

.sk__card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.sk__card-title {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}

.sk__card-icon {
  font-size: 16px;
  color: var(--brand-primary);
  flex-shrink: 0;
}

.sk__card-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
  line-height: 1.4;
  word-break: break-all;
}

.sk__card-disabled-tag {
  opacity: 0.7;
}

.sk__card-stats {
  text-align: right;
  flex-shrink: 0;
  padding-left: 10px;
  border-left: 1px solid var(--border-subtle);
}

.sk__card-stat-label {
  font-size: 10px;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  line-height: 1.2;
}

.sk__card-stat-value {
  font-size: 14px;
  color: var(--text-primary);
  margin-top: 2px;
}

.sk__card-stat-value strong {
  font-size: 18px;
  font-weight: 600;
  color: var(--brand-primary);
}

.sk__card-stat-rate {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-left: 2px;
}

.sk__card-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.sk__card-meta-item {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.sk__card-meta-sep {
  color: var(--border-default);
}

.sk__card-slug {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-page-soft);
  padding: 1px 6px;
  border-radius: 3px;
}

.sk__card-desc {
  margin: 10px 0 12px;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.sk__card-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-bottom: 12px;
}

.sk__card-tags-label {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-right: 4px;
}

.sk__card-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding-top: 10px;
  border-top: 1px solid var(--border-subtle);
}

.sk__card-locked {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text-tertiary);
  margin-left: auto;
  padding: 4px 8px;
}

.sk__danger-tip {
  max-width: 280px;
  font-size: 13px;
  line-height: 1.5;
}

@media (max-width: 900px) {
  .sk__layout {
    grid-template-columns: 1fr;
  }
  .sk__layout--collapsed {
    grid-template-columns: 1fr;
  }
  .sk__side {
    position: static;
  }
  .sk__cards {
    grid-template-columns: 1fr;
  }
}
</style>
