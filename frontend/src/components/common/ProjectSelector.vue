<template>
  <n-popselect
    v-model:value="selectedId"
    :options="options"
    trigger="click"
    scrollable
    :render-label="renderLabel"
    @update:value="handleSelect"
  >
    <button class="project-selector" type="button">
      <span class="project-selector__icon">
        <span class="i-carbon-folder" />
      </span>
      <span class="project-selector__label">
        <span class="project-selector__hint">当前项目</span>
        <n-ellipsis class="project-selector__name" :tooltip="false">
          {{ currentLabel }}
        </n-ellipsis>
      </span>
      <span class="i-carbon-chevron-down project-selector__caret" />
    </button>
  </n-popselect>
</template>

<script setup lang="ts">
import { computed, onMounted, h } from "vue";
import { NPopselect, NEllipsis, NTag } from "naive-ui";
import type { SelectOption } from "naive-ui";
import { useProjectStore } from "@/stores/project";

const projectStore = useProjectStore();

const selectedId = computed({
  get: () => projectStore.currentProjectId,
  set: () => {},
});

const options = computed<SelectOption[]>(() =>
  projectStore.projects.map((p) => ({
    label: p.name,
    value: p.id,
    status: p.status,
  })),
);

const currentLabel = computed(() => projectStore.currentProject?.name || "选择项目");

function renderLabel(option: SelectOption) {
  return h("div", { class: "flex items-center justify-between w-full gap-2" }, [
    h("span", { class: "truncate" }, option.label as string),
    (option as SelectOption & { status?: string }).status === "archived"
      ? h(NTag, { size: "tiny", type: "warning" }, () => "归档")
      : null,
  ]);
}

function handleSelect(value: string) {
  projectStore.setCurrentProject(value);
}

onMounted(() => {
  if (projectStore.projects.length === 0) {
    projectStore.fetchProjects();
  }
});
</script>

<style scoped>
.project-selector {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 10px 5px 6px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-default);
  background-color: var(--bg-card);
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--easing-standard),
    background-color var(--duration-fast) var(--easing-standard);
  max-width: 240px;
  font: inherit;
}

.project-selector:hover {
  border-color: var(--brand-primary-border);
  background-color: var(--bg-active);
}

.project-selector__icon {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--brand-gradient-soft);
  color: var(--brand-primary);
  border-radius: var(--radius-sm);
  font-size: 14px;
  flex-shrink: 0;
}

.project-selector__label {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  line-height: 1.1;
  min-width: 0;
}

.project-selector__hint {
  font-size: 10px;
  color: var(--text-tertiary);
  font-weight: 500;
  letter-spacing: 0.4px;
  text-transform: uppercase;
}

.project-selector__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  max-width: 160px;
}

.project-selector__caret {
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
