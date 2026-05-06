<template>
  <n-modal
    v-model:show="visible"
    preset="card"
    :style="{ width: '640px' }"
    title="批量导入物料"
    :mask-closable="!importing"
  >
    <!-- 模式选择 -->
    <div class="imp-section">
      <div class="imp-section__label">
        <span class="i-carbon-document" />
        导入方式
      </div>
      <n-tabs v-model:value="source" type="segment" :disabled="importing">
        <n-tab-pane name="csv" tab="CSV 文件">
          <div class="imp-csv">
            <div
              class="imp-dropzone"
              :class="{ 'imp-dropzone--active': dragOver, 'imp-dropzone--file': !!csvFile }"
              @click="triggerPicker"
              @dragover.prevent="dragOver = true"
              @dragleave.prevent="dragOver = false"
              @drop.prevent="handleDrop"
            >
              <template v-if="!csvFile">
                <span class="i-carbon-document-import imp-dropzone__icon" />
                <div class="imp-dropzone__hint">
                  <strong>点击选择</strong> 或拖拽 CSV 文件到此处
                </div>
                <div class="imp-dropzone__sub">
                  UTF-8，首行表头，单文件 ≤ 10MB / ≤ 10000 行
                </div>
              </template>
              <template v-else>
                <span class="i-carbon-document imp-dropzone__icon" />
                <div class="imp-dropzone__file">
                  <div class="imp-dropzone__file-name">{{ csvFile.name }}</div>
                  <div class="imp-dropzone__sub">
                    {{ formatFileSize(csvFile.size) }}
                  </div>
                </div>
                <n-button size="tiny" quaternary @click.stop="csvFile = null">
                  <template #icon><span class="i-carbon-close" /></template>
                </n-button>
              </template>
            </div>
            <input
              ref="pickerRef"
              type="file"
              class="imp-input"
              accept=".csv,text/csv"
              @change="handlePickerChange"
            />
            <n-collapse class="imp-help">
              <n-collapse-item name="columns" title="CSV 列说明">
                <div class="imp-help__body">
                  <p><strong>必需列：</strong></p>
                  <ul>
                    <li><code>key</code>（或 <code>名称</code>、<code>键</code>）— 变量名</li>
                    <li><code>value_type</code>（或 <code>类型</code>）— 6 种之一</li>
                  </ul>
                  <p><strong>可选列：</strong></p>
                  <ul>
                    <li><code>description</code>（或 <code>描述</code>）</li>
                    <li><code>value_text</code>（或 <code>值</code>、<code>内容</code>）— string/multiline/random 用</li>
                    <li><code>value_secret</code>（或 <code>密值</code>）— secret 用</li>
                    <li><code>value_json</code>（或 <code>json</code>）— dataset 用，需合法 JSON</li>
                    <li><code>sort_order</code>（或 <code>顺序</code>）— 0-10000</li>
                  </ul>
                  <p class="imp-help__note">
                    提示：<code>file</code> 类型不能从 CSV 导入（需走文件上传）；其它列会被忽略。
                  </p>
                </div>
              </n-collapse-item>
              <n-collapse-item name="template" title="示例模板">
                <pre class="imp-help__csv">key,value_type,value_text,description
login_username,string,alice@example.com,登录用户名
login_password,secret,MyP@ss123,登录密码（自动加密）
phone_number,random,phone:CN,随机国内手机号
users,dataset,"[{""name"":""alice""}]",参数化用户列表
</pre>
              </n-collapse-item>
            </n-collapse>
          </div>
        </n-tab-pane>

        <n-tab-pane name="json" tab="JSON 粘贴">
          <div class="imp-json">
            <n-input
              v-model:value="jsonText"
              type="textarea"
              placeholder='粘贴 JSON，示例：&#10;[&#10;  {"key":"username","value_type":"string","value_text":"alice"},&#10;  {"key":"password","value_type":"secret","value_secret":"My$ecret"}&#10;]'
              :autosize="{ minRows: 8, maxRows: 14 }"
              :disabled="importing"
              class="imp-json__textarea"
            />
            <div class="imp-json__meta">
              <n-tag v-if="jsonParseError" type="error" :bordered="false" size="small">
                <template #icon><span class="i-carbon-warning" /></template>
                {{ jsonParseError }}
              </n-tag>
              <n-tag
                v-else-if="jsonItems && jsonItems.length > 0"
                type="success"
                :bordered="false"
                size="small"
              >
                <template #icon><span class="i-carbon-checkmark-filled" /></template>
                已识别 {{ jsonItems.length }} 条
              </n-tag>
              <n-tag v-else :bordered="false" size="small">
                <template #icon><span class="i-carbon-information" /></template>
                等待粘贴 JSON 数组
              </n-tag>
              <n-button
                size="tiny"
                quaternary
                :disabled="importing || !jsonText.trim()"
                @click="formatJson"
              >
                <template #icon><span class="i-carbon-clean" /></template>
                格式化
              </n-button>
            </div>
          </div>
        </n-tab-pane>
      </n-tabs>
    </div>

    <!-- 冲突策略 -->
    <div class="imp-section">
      <div class="imp-section__label">
        <span class="i-carbon-compare" />
        冲突策略（同 key 已存在时）
      </div>
      <n-radio-group v-model:value="mode" :disabled="importing">
        <n-radio value="skip_existing">
          <strong>跳过</strong> — 保留现有值，不动（推荐）
        </n-radio>
        <n-radio value="upsert">
          <strong>覆盖</strong> — 用新值覆盖。<span class="imp-warn">value_type 不可变</span>
        </n-radio>
      </n-radio-group>
    </div>

    <!-- 报告 -->
    <n-alert v-if="report" :type="reportType" class="imp-report">
      <template #header>
        <strong>导入结果</strong>
      </template>
      <div class="imp-report__stats">
        <n-tag type="success" :bordered="false" size="small">
          创建 {{ report.created }}
        </n-tag>
        <n-tag type="info" :bordered="false" size="small">
          更新 {{ report.updated }}
        </n-tag>
        <n-tag :bordered="false" size="small">
          跳过 {{ report.skipped }}
        </n-tag>
        <n-tag
          :type="report.errors.length > 0 ? 'error' : 'default'"
          :bordered="false"
          size="small"
        >
          失败 {{ report.errors.length }}
        </n-tag>
        <n-tag :bordered="false" size="small">共 {{ report.total }} 条</n-tag>
      </div>
      <n-collapse v-if="report.errors.length > 0" class="imp-report__errors">
        <n-collapse-item name="errors" :title="`失败行详情（${report.errors.length}）`">
          <ul class="imp-report__error-list">
            <li v-for="(err, i) in report.errors" :key="i">
              <strong>第 {{ err.row }} 行</strong>
              <span v-if="err.key">（key = <code>{{ err.key }}</code>）</span>：
              {{ err.message }}
            </li>
          </ul>
        </n-collapse-item>
      </n-collapse>
    </n-alert>

    <template #footer>
      <div class="imp-footer">
        <n-button :disabled="importing" @click="handleClose">
          {{ report ? "关闭" : "取消" }}
        </n-button>
        <n-button
          type="primary"
          :loading="importing"
          :disabled="!canImport"
          @click="handleImport"
        >
          {{ report ? "再导一次" : "开始导入" }}
        </n-button>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { ref, computed, watch } from "vue";
import {
  NAlert,
  NButton,
  NCollapse,
  NCollapseItem,
  NInput,
  NModal,
  NRadio,
  NRadioGroup,
  NTabPane,
  NTabs,
  NTag,
  useMessage,
} from "naive-ui";

import {
  importItemsCsvApi,
  importItemsJsonApi,
  formatFileSize,
  VALUE_TYPES,
} from "@/services/testData";
import type {
  ImportItem,
  ImportMode,
  ImportReport,
  ValueType,
} from "@/services/testData";

/**
 * ImportDialog — 物料批量导入弹窗。
 *
 * 两条路径：
 * - CSV（multipart）：拖放 / 选择 → `importItemsCsvApi`
 * - JSON（粘贴）：前端 JSON.parse + 字段校验 → `importItemsJsonApi`
 *
 * 两者都复用后端 `ImportReport`：创建 / 更新 / 跳过 / 错误行列表。部分失败不
 * 阻塞整体，错误展示在弹窗下半部分的 alert 里。
 */
const props = defineProps<{
  setId: string;
  setName?: string;
}>();

const visible = defineModel<boolean>("show", { default: false });

const emit = defineEmits<{
  /** 导入完成并且至少有一条变更时发出，父组件可据此刷新列表 */
  imported: [report: ImportReport];
}>();

const message = useMessage();

const source = ref<"csv" | "json">("csv");
const mode = ref<ImportMode>("skip_existing");
const importing = ref(false);
const report = ref<ImportReport | null>(null);

// ─── CSV 状态 ─────────────────────────────────────────────────────────

const csvFile = ref<File | null>(null);
const dragOver = ref(false);
const pickerRef = ref<HTMLInputElement | null>(null);

function triggerPicker() {
  if (importing.value) return;
  pickerRef.value?.click();
}

function handlePickerChange(ev: Event) {
  const tgt = ev.target as HTMLInputElement;
  const f = tgt.files?.[0];
  if (f) setCsv(f);
  tgt.value = "";
}

function handleDrop(ev: DragEvent) {
  dragOver.value = false;
  if (importing.value) return;
  const f = ev.dataTransfer?.files?.[0];
  if (f) setCsv(f);
}

function setCsv(f: File) {
  const lower = f.name.toLowerCase();
  if (!lower.endsWith(".csv") && f.type !== "text/csv") {
    message.warning("仅支持 .csv 文件");
    return;
  }
  if (f.size > 10 * 1024 * 1024) {
    message.error(`CSV 超过 10MB 上限（${formatFileSize(f.size)}）`);
    return;
  }
  csvFile.value = f;
  report.value = null;
}

// ─── JSON 状态 ────────────────────────────────────────────────────────

const jsonText = ref("");
const jsonParseError = ref<string>("");

const jsonItems = computed<ImportItem[] | null>(() => {
  const t = jsonText.value.trim();
  if (!t) {
    jsonParseError.value = "";
    return null;
  }
  try {
    const parsed = JSON.parse(t);
    if (!Array.isArray(parsed)) {
      jsonParseError.value = "顶层必须是数组，例如 [{...},{...}]";
      return null;
    }
    const validated: ImportItem[] = [];
    for (let i = 0; i < parsed.length; i++) {
      const row = parsed[i];
      if (!row || typeof row !== "object") {
        jsonParseError.value = `第 ${i + 1} 条不是对象`;
        return null;
      }
      const key = row.key;
      const vt = row.value_type;
      if (typeof key !== "string" || !key) {
        jsonParseError.value = `第 ${i + 1} 条缺少 key`;
        return null;
      }
      if (typeof vt !== "string" || !VALUE_TYPES.includes(vt as ValueType)) {
        jsonParseError.value = `第 ${i + 1} 条 value_type 非法（${vt}）`;
        return null;
      }
      validated.push({
        key,
        value_type: vt as ValueType,
        description: row.description ?? null,
        sort_order: row.sort_order,
        value_text: row.value_text ?? null,
        value_secret: row.value_secret ?? null,
        value_json: row.value_json ?? null,
      });
    }
    jsonParseError.value = "";
    return validated;
  } catch (err) {
    jsonParseError.value =
      err instanceof Error ? `JSON 语法错误：${err.message}` : "JSON 语法错误";
    return null;
  }
});

function formatJson() {
  const t = jsonText.value.trim();
  if (!t) return;
  try {
    jsonText.value = JSON.stringify(JSON.parse(t), null, 2);
  } catch {
    message.warning("JSON 语法错误，无法格式化");
  }
}

// ─── 导入 ─────────────────────────────────────────────────────────────

const canImport = computed(() => {
  if (importing.value) return false;
  if (source.value === "csv") return !!csvFile.value;
  return (jsonItems.value?.length ?? 0) > 0;
});

async function handleImport() {
  if (!canImport.value) return;
  importing.value = true;
  report.value = null;
  try {
    let res;
    if (source.value === "csv") {
      res = await importItemsCsvApi(props.setId, {
        file: csvFile.value!,
        mode: mode.value,
      });
    } else {
      res = await importItemsJsonApi(props.setId, {
        items: jsonItems.value!,
        mode: mode.value,
      });
    }
    if (res.success) {
      report.value = res.data;
      if (res.data.created + res.data.updated > 0) {
        emit("imported", res.data);
        message.success(summarizeReport(res.data));
      } else if (res.data.errors.length > 0) {
        message.error(`导入完成，但全部 ${res.data.errors.length} 条失败`);
      } else {
        message.info("导入完成，但没有实际变更（全部 skip）");
      }
    }
  } catch (err) {
    const msg = extractErrorMessage(err) ?? "导入失败";
    message.error(msg);
  } finally {
    importing.value = false;
  }
}

function summarizeReport(r: ImportReport): string {
  const parts: string[] = [];
  if (r.created > 0) parts.push(`新建 ${r.created}`);
  if (r.updated > 0) parts.push(`更新 ${r.updated}`);
  if (r.skipped > 0) parts.push(`跳过 ${r.skipped}`);
  if (r.errors.length > 0) parts.push(`失败 ${r.errors.length}`);
  return `导入完成：${parts.join(" / ")}`;
}

function extractErrorMessage(err: unknown): string | null {
  if (err && typeof err === "object" && "data" in err) {
    const d = (err as { data?: { message?: string; detail?: unknown } }).data;
    if (d?.message) return d.message;
    if (typeof d?.detail === "string") return d.detail;
  }
  if (err instanceof Error) return err.message;
  return null;
}

const reportType = computed<"success" | "warning" | "error" | "info">(() => {
  if (!report.value) return "info";
  if (report.value.errors.length > 0 && report.value.created + report.value.updated === 0) {
    return "error";
  }
  if (report.value.errors.length > 0) return "warning";
  if (report.value.created + report.value.updated > 0) return "success";
  return "info";
});

// ─── 打开 / 关闭 ──────────────────────────────────────────────────────

function handleClose() {
  visible.value = false;
}

watch(visible, (v) => {
  if (v) {
    // 打开时把状态重置（上次导入的报告不要沿用）
    report.value = null;
    csvFile.value = null;
    jsonText.value = "";
  }
});
</script>

<style scoped>
.imp-section {
  margin-bottom: 16px;
}

.imp-section__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.imp-section__label > span:first-child {
  color: var(--brand-primary);
}

.imp-csv {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.imp-dropzone {
  border: 2px dashed var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-subtle, var(--bg-card));
  padding: 20px 16px;
  text-align: center;
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--easing-standard),
    background-color var(--duration-fast) var(--easing-standard);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.imp-dropzone--active,
.imp-dropzone:hover {
  border-color: var(--brand-primary);
  background: var(--brand-gradient-soft);
}

.imp-dropzone--file {
  flex-direction: row;
  justify-content: space-between;
  text-align: left;
  padding: 12px 14px;
}

.imp-dropzone__icon {
  font-size: 28px;
  color: var(--brand-primary);
}

.imp-dropzone__hint {
  font-size: 13px;
  color: var(--text-primary);
}

.imp-dropzone__sub {
  font-size: 12px;
  color: var(--text-tertiary);
}

.imp-dropzone__file {
  flex: 1;
  min-width: 0;
}

.imp-dropzone__file-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  word-break: break-all;
}

.imp-input {
  display: none;
}

.imp-help {
  margin-top: 4px;
}

.imp-help__body {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.imp-help__body code {
  background: var(--bg-active);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 12px;
  font-family: var(--font-mono, monospace);
}

.imp-help__body ul {
  margin: 4px 0 8px 20px;
}

.imp-help__note {
  color: var(--text-tertiary);
  font-size: 12px;
  margin-top: 6px;
}

.imp-help__csv {
  font-family: var(--font-mono, monospace);
  background: var(--bg-active);
  padding: 10px 12px;
  border-radius: var(--radius-md);
  margin: 0;
  font-size: 12px;
  overflow-x: auto;
}

.imp-json__textarea :deep(textarea) {
  font-family: var(--font-mono, monospace);
  font-size: 12.5px;
  line-height: 1.55;
}

.imp-json__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
}

.imp-warn {
  color: var(--warning-color, #f0a020);
  font-size: 12px;
  margin-left: 6px;
}

.imp-report {
  margin-top: 12px;
}

.imp-report__stats {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 6px 0;
}

.imp-report__errors {
  margin-top: 8px;
}

.imp-report__error-list {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.imp-report__error-list code {
  background: var(--bg-active);
  padding: 0 4px;
  border-radius: 3px;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}

.imp-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
