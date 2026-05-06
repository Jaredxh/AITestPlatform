<template>
  <n-modal
    v-model:show="visible"
    preset="card"
    :title="modalTitle"
    :style="{ width: viewMode !== 'config' ? '900px' : '520px' }"
    :mask-closable="!generating && viewMode !== 'generating'"
    :closable="!generating && viewMode !== 'generating'"
    display-directive="show"
  >
    <!-- Step 1: Configuration (新建 AI 生成任务)。
         注意：只有 viewMode === 'config' 才会渲染本块；这样无论 defineModel /
         reactivity 时序如何导致 generating/cases 看起来"像新建"，只要浮窗点击
         或 restoreTask 明确切到了 "task" 视图，用户都不会再看到选项窗。 -->
    <div v-if="viewMode === 'config'">
      <n-form label-placement="top">
        <n-form-item label="需求文档" required>
          <n-select
            v-model:value="selectedDocId"
            :options="documentOptions"
            :loading="loadingDocs"
            placeholder="选择一份需求文档"
            filterable
          />
        </n-form-item>

        <n-form-item label="目标模块">
          <n-tree-select
            v-model:value="targetModuleId"
            :options="moduleTreeOptions"
            placeholder="生成的用例放入哪个模块（可选）"
            clearable
            default-expand-all
          />
        </n-form-item>

        <n-form-item label="LLM 配置">
          <n-select
            v-model:value="llmConfigId"
            :options="llmOptions"
            :loading="loadingLLM"
            placeholder="使用默认配置"
            clearable
          />
        </n-form-item>
      </n-form>

      <div class="flex justify-end gap-2">
        <n-button @click="visible = false">取消</n-button>
        <n-button type="primary" :disabled="!selectedDocId" @click="startGenerate">
          <template #icon><span class="i-carbon-magic-wand" /></template>
          开始生成
        </n-button>
      </div>
    </div>

    <!-- Step 2: Generating (streaming) -->
    <div v-else-if="viewMode === 'generating'" class="min-h-40">
      <div class="flex items-center gap-2 mb-4">
        <n-spin :size="16" />
        <n-text>正在生成测试用例，请稍候...</n-text>
      </div>
      <!-- 长时间没有任何 delta 时给一条提示，提示用户可以"强制结束" -->
      <n-alert
        v-if="stuckReason"
        type="warning"
        size="small"
        class="mb-3"
        :show-icon="true"
      >
        {{ stuckReason }}
      </n-alert>
      <n-card size="small" content-style="padding: 12px;">
        <pre class="text-xs leading-relaxed whitespace-pre-wrap max-h-96 overflow-auto">{{ streamContent || "等待 AI 响应..." }}</pre>
      </n-card>
      <div class="flex justify-end gap-2 mt-3">
        <n-button @click="handleMinimize">
          <template #icon><span class="i-carbon-minimize" /></template>
          后台运行
        </n-button>
        <n-popconfirm @positive-click="handleForceCancel" :disabled="cancelling">
          <template #trigger>
            <n-button type="error" :loading="cancelling">
              <template #icon><span class="i-carbon-stop" /></template>
              强制结束任务
            </n-button>
          </template>
          <div style="max-width: 280px;">
            <p class="font-medium">确认强制结束本次 AI 生成？</p>
            <p class="text-xs mt-1" style="color: var(--text-tertiary);">
              已生成的内容会被丢弃，浮窗会消失。即使后端任务还在跑，下次刷新也会被自动清理。
            </p>
          </div>
        </n-popconfirm>
      </div>
    </div>

    <!-- Step 3: Preview results -->
    <generate-preview
      v-else-if="viewMode === 'preview'"
      :cases="generatedCases"
      :batch-id="batchId"
      :module-id="targetModuleId"
      :accepting="accepting"
      @accept="handleAccept"
      @accept-all="handleAcceptAll"
      @remove="handleRemove"
      @edit="handleEdit"
      @close="handleClose"
    />

    <!-- Step 4: 任务已结束 / 失败 的占位视图。
         如果 errorMsg 有值（生成失败、JSON 解析失败、completed-but-empty 等），
         这里会以醒目的错误样式呈现，并引导用户重新发起，而不是给一段
         "任务已结束"这种歧义文案。 -->
    <div v-else-if="viewMode === 'finished'" class="flex flex-col items-center justify-center py-6 gap-3">
      <span
        :class="errorMsg ? 'i-carbon-warning-alt' : 'i-carbon-checkmark-outline'"
        class="text-4xl"
        :style="{ color: errorMsg ? 'var(--error, #d03050)' : 'var(--text-tertiary)' }"
      />
      <n-text v-if="errorMsg" type="error" style="text-align: center; max-width: 520px;">
        {{ errorMsg }}
      </n-text>
      <n-text v-else depth="2">该 AI 生成任务已结束，没有待处理的候选用例。</n-text>
      <div class="flex gap-2 mt-2">
        <n-button @click="visible = false">关闭</n-button>
        <n-button type="primary" @click="startAuthoring">
          <template #icon>
            <span :class="errorMsg ? 'i-carbon-restart' : 'i-carbon-add'" />
          </template>
          {{ errorMsg ? '重新发起生成' : '新建生成任务' }}
        </n-button>
      </div>
    </div>

    <!-- Error -->
    <n-alert v-if="errorMsg" type="error" class="mt-3" closable @close="errorMsg = ''">
      {{ errorMsg }}
    </n-alert>
  </n-modal>
</template>

<script setup lang="ts">
import { ref, computed, watch } from "vue";
import {
  NAlert,
  NButton,
  NCard,
  NForm,
  NFormItem,
  NModal,
  NPopconfirm,
  NSelect,
  NSpin,
  NText,
  NTreeSelect,
  useMessage,
} from "naive-ui";
import type { TreeSelectOption } from "naive-ui";
import { useProjectStore } from "@/stores/project";
import {
  getModuleTreeApi,
  batchAcceptApi,
  startGenerationTaskApi,
  getGenerationBatchApi,
  cancelGenerationBatchApi,
} from "@/services/testcases";
import { getLLMConfigsApi } from "@/services/llm";
import type { ModuleTreeNode, GeneratedTestcase, GenerateParams } from "@/services/testcases";
import { getDocumentsApi } from "@/services/requirements";
import { useSSE } from "@/composables/useSSE";
import GeneratePreview from "./GeneratePreview.vue";

const props = defineProps<{
  moduleId?: string | null;
}>();

const emit = defineEmits<{
  (e: "accepted", payload?: { module_id: string | null }): void;
  (e: "background", payload: { generating: boolean; documentName?: string }): void;
  (e: "task-snapshot", payload: GenerateTaskSnapshot): void;
}>();

const visible = defineModel<boolean>("show", { default: false });
const message = useMessage();
const projectStore = useProjectStore();
const { fetchSSE } = useSSE();
let pollTimer: ReturnType<typeof window.setInterval> | null = null;
let sseHandle: { abort: () => void } | null = null;

const selectedDocId = ref<string | null>(null);
const targetModuleId = ref<string | null>(null);
const llmConfigId = ref<string | null>(null);

const loadingDocs = ref(false);
const loadingLLM = ref(false);
const generating = ref(false);
const accepting = ref(false);
const cancelling = ref(false);
const streamContent = ref("");
const batchId = ref("");
const generatedCases = ref<(GeneratedTestcase & { _selected: boolean })[]>([]);
const errorMsg = ref("");
// "卡住"提示文案：连续多次重连/SSE 立即结束但拿不到 delta 时显示，给用户
// 一个判断依据 + 引导他点"强制结束"。
const stuckReason = ref("");

// SSE 重连次数计数。每次成功收到 delta 时归零。短时间内连续 N 次拿到 done
// 又没拿到 delta，说明后端任务可能孤儿了，自动停止重连并提示用户。
let reconnectCount = 0;
let receivedAnyDelta = false;
const MAX_RECONNECT_WITHOUT_DELTA = 3;
let stuckHintTimer: ReturnType<typeof window.setTimeout> | null = null;
const STUCK_HINT_AFTER_MS = 30_000;

/**
 * Dialog 的视图模式。历史上我们通过 `!generating && cases.length === 0`
 * 反推"当前应该显示选项窗"，这个启发式在很多时序上会误判（例如 SSE 瞬断、
 * restoreTask 一个 failed 任务、defineModel 微任务错位等），导致用户点浮窗
 * 时掉回 Step 1 的"选择文档"。改为显式 view mode 驱动后：
 *   - 'config'     只有用户显式调用 startAuthoring() 才会进入
 *   - 'generating' 后台任务在跑
 *   - 'preview'    已生成候选用例，等待用户审核/入库
 *   - 'finished'   任务已结束/失败，没有可处理的内容（保留 dialog 壳子
 *                 显示提示，避免掉回 Step 1）
 * 关键不变量：浮窗点击路径永远不会把 viewMode 置为 'config'。
 */
type GenerateDialogView = "config" | "generating" | "preview" | "finished";
const viewMode = ref<GenerateDialogView>("config");

const modalTitle = computed(() => {
  switch (viewMode.value) {
    case "generating":
      return "AI 正在生成测试用例";
    case "preview":
      return "AI 生成测试用例 · 审核候选";
    case "finished":
      return "AI 生成任务";
    default:
      return "AI 生成测试用例";
  }
});

const documentOptions = ref<{ label: string; value: string }[]>([]);
const llmOptions = ref<{ label: string; value: string }[]>([]);
const moduleTree = ref<ModuleTreeNode[]>([]);

export interface GenerateTaskSnapshot {
  id: string;
  documentName?: string;
  generating: boolean;
  streamContent: string;
  batchId: string;
  cases: (GeneratedTestcase & { _selected: boolean })[];
  moduleId: string | null;
  errorMsg: string;
}

function currentSnapshot(): GenerateTaskSnapshot | null {
  // We require a real batchId before emitting a snapshot. This avoids the old
  // bug where a user-triggered "minimize" between clicking Generate and the
  // backend responding with an id produced a placeholder snapshot keyed on
  // selectedDocId; once batchId landed, a second snapshot (keyed on batchId)
  // was unshifted and the user saw two floating buttons for the same task,
  // the stale one restoring into a broken-stream Step 2.
  if (!batchId.value) return null;
  // We still want to emit an "empty" snapshot (cases=[], generating=false) after
  // a user accepts all candidates — that's how the parent view knows to remove the
  // floating button.
  const docOption = documentOptions.value.find((d) => d.value === selectedDocId.value);
  return {
    id: batchId.value,
    documentName: docOption?.label,
    generating: generating.value,
    streamContent: streamContent.value,
    batchId: batchId.value,
    cases: [...generatedCases.value],
    moduleId: targetModuleId.value,
    errorMsg: errorMsg.value,
  };
}

function _resolveViewFromTask(task: {
  generating: boolean;
  cases: { length: number };
}): GenerateDialogView {
  if (task.generating) return "generating";
  if (task.cases.length > 0) return "preview";
  return "finished";
}

/**
 * 浮窗点击的入口：根据 task 的实际状态决定进入哪个视图。
 * **绝不会**把 viewMode 置为 'config' —— 这是"浮窗点击绝对不弹配置窗"
 * 这个不变量的最终保障。
 */
function restoreTask(task: GenerateTaskSnapshot) {
  resetStuckState();
  streamContent.value = task.streamContent;
  batchId.value = task.batchId;
  generatedCases.value = [...task.cases];
  targetModuleId.value = task.moduleId;
  errorMsg.value = task.errorMsg;
  generating.value = task.generating;
  viewMode.value = _resolveViewFromTask(task);
  visible.value = true;
  if (task.generating && task.batchId) {
    openStream(task.batchId);
  }
}

/**
 * 进入"新建 AI 生成"视图。由父组件在用户显式点"AI 生成用例"按钮时调用；
 * 其它入口（浮窗点击、onActivated 恢复等）都不走这里，因此 viewMode 永远
 * 不会被意外切到 'config'。
 */
function startAuthoring() {
  resetStuckState();
  viewMode.value = "config";
  generating.value = false;
  generatedCases.value = [];
  streamContent.value = "";
  batchId.value = "";
  errorMsg.value = "";
  targetModuleId.value = props.moduleId || null;
  selectedDocId.value = null;
  llmConfigId.value = null;
  visible.value = true;
  fetchDocuments();
  fetchLLMConfigs();
  fetchModuleTree();
}

function buildTreeSelectOptions(nodes: ModuleTreeNode[]): TreeSelectOption[] {
  return nodes.map((n) => ({
    key: n.id,
    label: n.name,
    children: n.children.length > 0 ? buildTreeSelectOptions(n.children) : undefined,
  }));
}

const moduleTreeOptions = computed(() => buildTreeSelectOptions(moduleTree.value));

async function fetchDocuments() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;
  loadingDocs.value = true;
  try {
    const res = await getDocumentsApi(projectId, { page_size: 100 });
    if (res.success) {
      documentOptions.value = res.data.items
        .filter((d) => d.status === "parsed")
        .map((d) => ({ label: d.filename, value: d.id }));
    }
  } finally {
    loadingDocs.value = false;
  }
}

async function fetchLLMConfigs() {
  loadingLLM.value = true;
  try {
    const res = await getLLMConfigsApi();
    if (res.success) {
      llmOptions.value = res.data.map((c) => ({
        label: `${c.name} (${c.model})${c.is_default ? " [默认]" : ""}`,
        value: c.id,
      }));
    }
  } catch {
    /* ignore */
  } finally {
    loadingLLM.value = false;
  }
}

async function fetchModuleTree() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;
  try {
    const res = await getModuleTreeApi(projectId);
    if (res.success) {
      moduleTree.value = res.data;
    }
  } catch {
    /* ignore */
  }
}

async function startGenerate() {
  if (!selectedDocId.value) return;
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;

  resetStuckState();
  generating.value = true;
  viewMode.value = "generating";
  streamContent.value = "AI 生成任务已提交，后端正在分析文档并生成用例...\n";
  generatedCases.value = [];
  batchId.value = "";
  errorMsg.value = "";

  const body: GenerateParams = {
    document_id: selectedDocId.value,
  };
  if (targetModuleId.value) body.module_id = targetModuleId.value;
  if (llmConfigId.value) body.llm_config_id = llmConfigId.value;

  try {
    const res = await startGenerationTaskApi(projectId, body);
    if (res.success) {
      batchId.value = res.data.id;
      streamContent.value = `[${res.data.model_used || "默认模型"}] 开始生成…\n`;
      emitSnapshot();
      openStream(res.data.id);
    }
  } catch (err: unknown) {
    generating.value = false;
    viewMode.value = "finished";
    errorMsg.value = err instanceof Error ? err.message : "启动生成任务失败";
  }
}

/**
 * 强制结束当前 AI 生成任务。
 *
 * 与之前"中断生成"不同，这里会先调用后端 cancel API，确保后端 batch 立刻
 * 标 failed、in-process hub 立刻断流。这样：
 *   - 即便后端进程已死/任务已是孤儿，DB 状态也会被回收，浮窗不会再次出现；
 *   - 立即断开本地 SSE / 停止 polling，防止 finalizeFromServer 看到
 *     status=generating 又自动重连，形成死循环。
 */
async function handleForceCancel() {
  resetStuckState();
  closeStream();
  stopPolling();
  generating.value = false;

  if (batchId.value) {
    cancelling.value = true;
    try {
      await cancelGenerationBatchApi(batchId.value);
      message.success("任务已强制结束");
    } catch {
      message.warning("调用结束接口失败，但本地已停止追踪该任务");
    } finally {
      cancelling.value = false;
    }
  }

  // 视图收尾：进入"已结束"占位，给用户一个清晰的"重新发起"按钮
  if (generatedCases.value.length > 0) {
    viewMode.value = "preview";
  } else {
    if (!errorMsg.value) errorMsg.value = "任务已被强制结束";
    viewMode.value = "finished";
  }

  // 通知父组件清理浮窗。emit empty snapshot (cases=[], generating=false) →
  // TestcaseView.handleTaskSnapshot 会把它从 generateTasks 里删掉。
  emitSnapshot();
  emit("background", { generating: false });
}

function resetStuckState() {
  reconnectCount = 0;
  receivedAnyDelta = false;
  stuckReason.value = "";
  if (stuckHintTimer) {
    window.clearTimeout(stuckHintTimer);
    stuckHintTimer = null;
  }
}

function armStuckHint() {
  if (stuckHintTimer) return;
  stuckHintTimer = window.setTimeout(() => {
    if (!receivedAnyDelta && generating.value) {
      stuckReason.value =
        "AI 服务超过 30 秒没有任何输出。如果一直没动静，请点右下方"
        + "「强制结束任务」释放该批次。";
    }
  }, STUCK_HINT_AFTER_MS);
}

/**
 * Open a live SSE stream that tails the backend generation task. The stream
 * survives refresh (backend keeps the task running + retains an in-memory
 * buffer), so restoration also uses this exact channel.
 */
function openStream(id: string) {
  closeStream();
  let receivedDelta = false;
  armStuckHint();
  sseHandle = fetchSSE(
    `/api/testcases/generation-batches/${id}/stream`,
    null,
    {
      onEvent: (event) => {
        const type = event.type as string;
        if (type === "batch_start") {
          const docName = (event.document as string) || "";
          const modelName = (event.model as string) || "";
          if (docName || modelName) {
            streamContent.value = `[${modelName}] 正在分析「${docName}」并生成用例…\n`;
          }
          const moduleName = (event.module_name as string) || "";
          if (moduleName) streamContent.value += `目标模块：${moduleName}\n`;
          emitSnapshot();
        } else if (type === "delta") {
          if (!receivedDelta) {
            streamContent.value = "";
            receivedDelta = true;
          }
          // 一旦真有 delta 流入，就归零重连计数 / stuck 提示
          receivedAnyDelta = true;
          reconnectCount = 0;
          stuckReason.value = "";
          if (stuckHintTimer) {
            window.clearTimeout(stuckHintTimer);
            stuckHintTimer = null;
          }
          const piece = (event.content as string) || "";
          streamContent.value += piece;
          emitSnapshot();
        } else if (type === "info") {
          const msg = (event.message as string) || "";
          if (msg) {
            streamContent.value += `\n[${msg}]\n`;
            emitSnapshot();
          }
        } else if (type === "generated") {
          const list = (event.testcases as GeneratedTestcase[]) || [];
          generatedCases.value = list.map((tc) => ({ ...tc, _selected: true }));
          // 已经拿到候选用例 → 预览视图；如果 dialog 还开着，用户会直接看到
          // Step 3。如果在后台，viewMode 也会同步更新，下次点浮窗就进预览。
          if (generatedCases.value.length > 0) {
            viewMode.value = "preview";
          }
          emitSnapshot();
          if (!visible.value && generatedCases.value.length > 0) {
            message.success(`AI 已完成生成 ${generatedCases.value.length} 条用例，点击悬浮窗查看。`);
          }
        } else if (type === "error") {
          errorMsg.value = (event.message as string) || "生成失败";
        }
      },
      onError: (msg) => {
        // SSE transport failure — fall back to polling so we eventually finalize.
        if (msg && !errorMsg.value) errorMsg.value = msg;
        startPolling(id);
      },
      onDone: () => {
        sseHandle = null;
        // 关键：绝不在这里擅自把 viewMode 切到 'finished' 或 'preview'。
        //
        // SSE 结束有三种可能：
        //   (a) 后端任务真的完成了（completed / failed）；
        //   (b) 网络瞬断，后端任务其实还在跑；
        //   (c) 浮窗重新 subscribe，后端任务已完成，hub 已快速把 buffer 放完 done。
        //
        // 过去的版本在这里"看到 generatedCases=0 就切 finished"，对于 (b) 场景
        // 会错误地把一个还在生成中的任务显示成"已结束无用例"——这正是用户报告的
        // "任务才刚开始生成，点浮窗却提示已结束"的根因。
        //
        // 现在统一由 finalizeFromServer 以后端 batch.status 为权威来决定视图：
        //   - status=generating → 恢复 generating 视图，让 SSE 重连 / poll 继续
        //   - status=completed 有用例 → preview
        //   - status=completed 无用例 / failed → finished + errorMsg 明确说明原因
        finalizeFromServer(id);
      },
    },
    { method: "GET" },
  );
}

function closeStream() {
  if (sseHandle) {
    sseHandle.abort();
    sseHandle = null;
  }
}

/** Lightweight polling fallback when SSE is interrupted. */
function startPolling(id: string) {
  stopPolling();
  pollTimer = window.setInterval(() => finalizeFromServer(id), 2000);
  finalizeFromServer(id);
}

function stopPolling() {
  if (pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function finalizeFromServer(id: string) {
  try {
    const res = await getGenerationBatchApi(id);
    if (!res.success) return;
    const batch = res.data;
    if (batch.module_id) targetModuleId.value = batch.module_id;

    if (batch.status === "generating") {
      // 后端还在生成 — SSE 可能是被网络瞬断打掉了。保持 generating 视图，
      // 并重新订阅 SSE 拿最新事件；绝不切到 finished（这是根治"任务还在生成
      // 却被提示已结束"这个 bug 的关键）。
      //
      // 但需要"重连防死循环"：如果短时间内多次重连，每次都立刻收到 done 而
      // 没有任何 delta（典型场景：后端孤儿任务，list 自愈尚未触发），就别
      // 再继续重连了——给用户一条提示 + 强制结束按钮兜底。
      if (!receivedAnyDelta) {
        reconnectCount += 1;
        if (reconnectCount >= MAX_RECONNECT_WITHOUT_DELTA) {
          stopPolling();
          stuckReason.value =
            "后端任务无任何输出（已连续 "
            + reconnectCount
            + " 次重连失败），可能是后端进程已重启或任务卡死。"
            + "建议点击「强制结束任务」释放该批次后重新发起。";
          // 保持 viewMode=generating 但不再自动重连，避免 hammering。
          return;
        }
      }
      generating.value = true;
      if (viewMode.value !== "generating") viewMode.value = "generating";
      if (!sseHandle) openStream(id);
      return;
    }

    stopPolling();
    generating.value = false;

    if (batch.status === "completed") {
      if (generatedCases.value.length === 0) {
        generatedCases.value = batch.testcases.map((tc) => ({ ...tc, _selected: true }));
      }
      if (generatedCases.value.length > 0) {
        viewMode.value = "preview";
      } else {
        // 后端说 completed 但实际没解析出用例 —— LLM 输出格式不对或内容太空。
        // 给用户一个清晰的错误文案 + 重试按钮，而不是"任务已结束"这种歧义说法。
        if (!errorMsg.value) {
          errorMsg.value =
            "AI 已完成生成，但未能解析出有效的测试用例。可能是模型输出格式不符合要求，请检查文档内容或换一个 LLM 后重试。";
        }
        viewMode.value = "finished";
      }
      emitSnapshot();
    } else if (batch.status === "failed") {
      errorMsg.value = errorMsg.value || "AI 生成失败，请重新发起。";
      viewMode.value = "finished";
      emitSnapshot();
    }
  } catch {
    /* transient network errors — keep polling */
  }
}

function handleMinimize() {
  minimizeToBackground();
}

function minimizeToBackground() {
  if (!generating.value && generatedCases.value.length === 0) return;
  // If the batch hasn't been created yet, we don't have a stable id to put on
  // a floating button. Refuse to minimize for a beat rather than losing the task.
  if (!batchId.value) {
    message.info("任务正在初始化，请稍候再切到后台");
    return;
  }
  emitSnapshot();
  const docOption = documentOptions.value.find((d) => d.value === selectedDocId.value);
  emit("background", {
    generating: generating.value,
    documentName: docOption?.label,
  });
  visible.value = false;
}

function emitSnapshot() {
  const snapshot = currentSnapshot();
  if (snapshot) {
    emit("task-snapshot", snapshot);
  }
}

function handleRemove(idx: number) {
  generatedCases.value.splice(idx, 1);
}

function handleEdit(idx: number, updated: GeneratedTestcase) {
  generatedCases.value[idx] = { ...updated, _selected: true };
}

async function handleAccept(indices: number[]) {
  if (!batchId.value) {
    message.error("缺少生成批次信息");
    return;
  }
  accepting.value = true;
  try {
    const selected = indices.map((i) => generatedCases.value[i]);
    const payload = selected.map((tc) => ({
      title: tc.title,
      precondition: tc.precondition,
      priority: tc.priority || "medium",
      steps: tc.steps,
    }));

    const res = await batchAcceptApi({
      batch_id: batchId.value,
      testcases: payload,
      module_id: targetModuleId.value,
    });
    if (res.success) {
      message.success(`已入库 ${res.data.accepted_count} 条用例`);
      for (const i of indices.sort((a, b) => b - a)) {
        generatedCases.value.splice(i, 1);
      }
      emit("accepted", { module_id: targetModuleId.value });
      emitSnapshot();
      if (generatedCases.value.length === 0) {
        // 全部入库完毕 → 视图转成"已结束"并关闭。关闭后即便 visible 再次
        // 被切到 true，viewMode 也不是 config，不会掉回选项窗。
        viewMode.value = "finished";
        visible.value = false;
      }
    }
  } catch {
    message.error("入库失败");
  } finally {
    accepting.value = false;
  }
}

async function handleAcceptAll() {
  const allIndices = generatedCases.value.map((_, i) => i);
  await handleAccept(allIndices);
}

function handleClose() {
  // 关闭弹窗不等于丢弃任务；只要还有未处理候选用例，就转为后台悬浮。
  if (generatedCases.value.length > 0) {
    minimizeToBackground();
    return;
  }
  visible.value = false;
}

watch(visible, (val) => {
  if (!val) {
    // 关闭 dialog：如果还有未完成/未入库的任务，转为后台浮窗；否则静默关闭。
    if (generatedCases.value.length > 0 || generating.value) {
      minimizeToBackground();
    }
    return;
  }
  // visible 变 true：不再在这里做任何"重置 / reset"的事。viewMode 由显式
  // 调用 startAuthoring / restoreTask 决定；这里只负责"被动打开"时给一个
  // 合理的默认值（比如 parent 把 showGenerateDialog 置为 true，却没通过
  // startAuthoring 走）——这种情况必然是"新建"意图。
  if (viewMode.value === "config") {
    if (documentOptions.value.length === 0) fetchDocuments();
    if (llmOptions.value.length === 0) fetchLLMConfigs();
    if (moduleTree.value.length === 0) fetchModuleTree();
  }
});

defineExpose({ minimizeToBackground, restoreTask, startAuthoring, currentSnapshot });
</script>
