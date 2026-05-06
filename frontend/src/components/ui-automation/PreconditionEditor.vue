<template>
  <div class="precondition-editor">
    <div class="precondition-editor__header">
      <div class="precondition-editor__hint">
        <span class="i-carbon-information mr-1" />
        前置步骤按"启用 &amp; 顺序"依次执行；同一环境建议最多一条"能产生登录态"的步骤（state_inject / ai_login / scripted_steps / cookie_inject / http_login）。
      </div>
      <n-button size="small" type="primary" @click="openCreate">
        <template #icon><span class="i-carbon-add" /></template>
        新增前置步骤
      </n-button>
    </div>

    <n-empty
      v-if="sortedList.length === 0"
      size="small"
      description="还没有前置步骤"
      class="my-6"
    >
      <template #extra>
        <n-button size="small" @click="openCreate">添加第一个</n-button>
      </template>
    </n-empty>

    <n-data-table
      v-else
      :columns="columns"
      :data="sortedList"
      :row-key="(row: PreconditionTemplate) => row.id"
      :bordered="false"
      size="small"
      striped
    />

    <!-- ── 新建 / 编辑子弹窗 ──────────────────────────────────────── -->
    <n-modal
      v-model:show="editorVisible"
      preset="card"
      :mask-closable="false"
      :title="editorTitle"
      :style="{ width: '640px', maxWidth: 'calc(100vw - 32px)' }"
      @close="resetDraft"
    >
      <n-form ref="formRef" :model="draft" :rules="rules" label-placement="top">
        <n-grid :cols="2" :x-gap="16">
          <n-gi :span="2">
            <n-form-item label="步骤名称" path="name" required>
              <n-input
                v-model:value="draft.name"
                placeholder="例：注入测试账号 session / 管理员账号密码登录"
                maxlength="100"
                show-count
              />
            </n-form-item>
          </n-gi>

          <n-gi>
            <n-form-item label="类型" path="type" required>
              <n-select
                v-model:value="draft.type"
                :options="typeOptions"
                :disabled="isEditing"
              />
              <template #feedback>
                <span v-if="isEditing" class="text-xs"
                  >编辑模式下不允许改类型，避免 config 字段语义错乱</span
                >
                <span v-else class="text-xs">{{ currentTypeMeta.description }}</span>
              </template>
            </n-form-item>
          </n-gi>

          <n-gi>
            <n-form-item label="顺序">
              <n-input-number
                v-model:value="draft.order_index"
                :min="0"
                :max="10000"
                class="w-full"
              />
            </n-form-item>
          </n-gi>

          <n-gi :span="2">
            <n-form-item label="描述">
              <n-input
                v-model:value="draft.description"
                type="textarea"
                :rows="2"
                placeholder="给这个步骤的备注（可选）"
              />
            </n-form-item>
          </n-gi>
        </n-grid>

        <n-divider class="!my-4" />

        <!-- ─── 类型专属配置 ─────────────────────────────────────── -->

        <!-- state_inject -->
        <template v-if="draft.type === 'state_inject'">
          <p class="precondition-editor__section-hint">
            从磁盘读已保存的 <code>storage_state</code>（cookie + localStorage），navigate
            到 <code>verify_url</code> 后用关键字检测过期。本类型只读不写；state 文件由其他步骤（ai_login / scripted_steps / cookie_inject）产出。
          </p>
          <n-form-item label="验证 URL（留空使用环境的 base_url）">
            <n-input
              v-model:value="stateConfig.verify_url"
              placeholder="/home 或 https://staging.foo.com/me"
            />
          </n-form-item>
          <n-checkbox v-model:checked="stateConfig.required" class="mb-2">
            必需：加载不到 state 文件就视为失败
          </n-checkbox>
          <n-checkbox v-model:checked="stateConfig.fallback_to_ai_login">
            检测到过期时自动触发后续 ai_login 步骤
          </n-checkbox>
        </template>

        <!-- ai_login -->
        <template v-else-if="draft.type === 'ai_login'">
          <p class="precondition-editor__section-hint">
            让 LLM 看着页面自主操作登录表单，自动处理字段填充 / 验证码 / 点击登录。
            适合"登录页经常改 UI 但核心流程不变"的场景。会自动在成功后保存登录态。
          </p>
          <n-grid :cols="2" :x-gap="16">
            <n-gi>
              <n-form-item label="登录页 URL">
                <n-input
                  v-model:value="aiConfig.login_url"
                  placeholder="/login（相对环境 base_url）或完整 URL"
                />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="最大步数（防死循环）">
                <n-input-number
                  v-model:value="aiConfig.max_steps"
                  :min="1"
                  :max="50"
                  class="w-full"
                />
              </n-form-item>
            </n-gi>
          </n-grid>
          <n-form-item>
            <template #label>
              <span>
                登录成功标志
                <n-tooltip>
                  <template #trigger>
                    <span class="i-carbon-information cursor-help" />
                  </template>
                  <div class="max-w-md text-sm leading-6">
                    <div>子串匹配，按以下三处依次校验，任一命中即判通过：</div>
                    <div class="mt-1">
                      ① <b>当前页面 URL</b>（最稳）—— 填登录后跳转地址即可，
                      完整 URL / path 片段 / host 都行。<br />
                      ② <b>页面文本快照</b>（aria 树文本，菜单项、用户名等都在）。<br />
                      ③ <b>AI 收尾文字总结</b>（兜底）。
                    </div>
                    <div class="mt-1 text-xs text-tertiary">
                      推荐填登录后必出现的稳定文字（"退出登录"、用户昵称）或
                      跳转后 URL 的特征片段（如 ``/home/`` 或 host 名）。
                    </div>
                  </div>
                </n-tooltip>
              </span>
            </template>
            <n-input
              v-model:value="aiConfig.success_indicator"
              placeholder="例：/home/  或  cq-auth-dashboard.keyuanjiankang.com  或  退出登录"
            />
          </n-form-item>
          <credentials-editor
            v-model:entries="draft.credentialEntries"
            v-model:clear-existing="draft.clear_credentials"
            :has-existing="hasExistingCredentials"
            preset-hint="常用字段：username / password / mfa_code"
          />
        </template>

        <!-- scripted_steps -->
        <template v-else-if="draft.type === 'scripted_steps'">
          <p class="precondition-editor__section-hint">
            按顺序执行一组 Playwright 动作（goto / click / fill / press / wait_for_selector 等）。
            最快最稳但要写死选择器。成功后自动保存登录态。
          </p>
          <n-form-item label="步骤（JSON 数组）" path="scriptedStepsJson">
            <n-input
              v-model:value="draft.scriptedStepsJson"
              type="textarea"
              :rows="10"
              placeholder="[
  { &quot;action&quot;: &quot;goto&quot;, &quot;url&quot;: &quot;/login&quot; },
  { &quot;action&quot;: &quot;fill&quot;, &quot;selector&quot;: &quot;#username&quot;, &quot;value_ref&quot;: &quot;username&quot; },
  { &quot;action&quot;: &quot;fill&quot;, &quot;selector&quot;: &quot;#password&quot;, &quot;value_ref&quot;: &quot;password&quot; },
  { &quot;action&quot;: &quot;click&quot;, &quot;selector&quot;: &quot;button[type=submit]&quot; },
  { &quot;action&quot;: &quot;wait_for_selector&quot;, &quot;selector&quot;: &quot;#user-avatar&quot;, &quot;timeout&quot;: 10000 }
]"
              class="!font-mono text-xs"
            />
            <template #feedback>
              允许的 action: goto / click / fill / press / wait_for_selector /
              wait_for_load_state / select_option / check / uncheck / sleep。
              <code>value_ref</code> 会从 credentials 里读明文。
            </template>
          </n-form-item>
          <credentials-editor
            v-model:entries="draft.credentialEntries"
            v-model:clear-existing="draft.clear_credentials"
            :has-existing="hasExistingCredentials"
            preset-hint="写 value_ref 引用的字段（如 username / password）"
          />
        </template>

        <!-- cookie_inject -->
        <template v-else-if="draft.type === 'cookie_inject'">
          <p class="precondition-editor__section-hint">
            直接把 cookie 写入 BrowserContext。适合"已有 session token /
            第三方登录成功后拿到的 cookie"的场景，无需跑完登录流程。
          </p>
          <n-form-item label="Cookies（JSON 数组）" path="cookiesJson">
            <n-input
              v-model:value="draft.cookiesJson"
              type="textarea"
              :rows="8"
              placeholder="[
  { &quot;name&quot;: &quot;SESSIONID&quot;, &quot;value_ref&quot;: &quot;session_token&quot;, &quot;domain&quot;: &quot;staging.foo.com&quot;, &quot;path&quot;: &quot;/&quot; }
]"
              class="!font-mono text-xs"
            />
            <template #feedback>
              必填：name + (value 或 value_ref) + domain。path / expires / httpOnly / secure / sameSite 可选。
            </template>
          </n-form-item>
          <credentials-editor
            v-model:entries="draft.credentialEntries"
            v-model:clear-existing="draft.clear_credentials"
            :has-existing="hasExistingCredentials"
            preset-hint="放 cookie 的明文值，供 value_ref 引用"
          />
        </template>

        <!-- http_login -->
        <template v-else-if="draft.type === 'http_login'">
          <p class="precondition-editor__section-hint">
            纯 HTTP 走「<code>GET /auth/getCode</code> 拿挑战 cookie →
            <code>POST /auth/login</code> 拿 token cookie → 注入浏览器」。
            <b>免浏览器、免 LLM、&lt;2 秒完成</b>，是这类后台最稳的登录方式。
            适用于「图形验证码值实际就在 Set-Cookie 里」的设计
            （如 keyuanjiankang / weimiaocaishang 鉴权服务）。
          </p>
          <n-grid :cols="2" :x-gap="16">
            <n-gi>
              <n-form-item label="鉴权服务 base URL" required>
                <n-input
                  v-model:value="httpConfig.auth_base_url"
                  placeholder="https://auth-dashboard.keyuanjiankang.com"
                />
                <template #feedback>
                  挑战 + 登录两个接口的根 URL（不包含 /api 前缀）。
                </template>
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="Cookie 注入域">
                <n-input
                  v-model:value="httpConfig.cookie_domain"
                  placeholder="keyuanjiankang.com（留空自动从 base URL 推断）"
                />
              </n-form-item>
            </n-gi>
          </n-grid>
          <n-grid :cols="2" :x-gap="16">
            <n-gi>
              <n-form-item label="密码哈希算法">
                <n-select
                  v-model:value="httpConfig.password_hash"
                  :options="[
                    { label: 'md5（最常见）', value: 'md5' },
                    { label: 'sha256', value: 'sha256' },
                    { label: '不哈希（明文传）', value: 'none' },
                  ]"
                />
                <template #feedback>
                  凭据里的 password 在发往 login 接口前会按此算法处理。
                </template>
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="验证 URL（可选）">
                <n-input
                  v-model:value="httpConfig.verify_url"
                  placeholder="https://test-cq-auth-dashboard.keyuanjiankang.com/home/"
                />
                <template #feedback>
                  注入 cookie 后跳到这个 URL，看是否到了登录后的页面。
                </template>
              </n-form-item>
            </n-gi>
          </n-grid>
          <n-form-item label="登录成功标志（可选）">
            <n-input
              v-model:value="httpConfig.success_indicator"
              placeholder="例：/home/  或  退出登录"
            />
            <template #feedback>
              子串匹配 verify URL 跳转后的当前页面 URL 或 HTML 内容。
            </template>
          </n-form-item>
          <n-form-item label="高级配置（可选 JSON）" path="httpAdvancedJson">
            <n-input
              v-model:value="httpConfig.advancedJson"
              type="textarea"
              :rows="6"
              placeholder='// 大多数场景留空。需要重写 endpoint / 拼装额外 cookie（如 wm_user）时填：
{
  "precode_path": "/api/auth/verification/getCode",
  "login_path": "/api/auth/account/login",
  "extra_login_body": { "h_app_id": "127" },
  "extra_cookies": [
    {
      "name": "wm_user",
      "value_template": "${url_encode_json:{\"cn\":\"${credentials.username}\",\"token\":\"${captured.c_token}\"}}",
      "domain": "keyuanjiankang.com",
      "path": "/"
    }
  ]
}'
              class="!font-mono text-xs"
            />
            <template #feedback>
              支持模板：<code>${'{credentials.X}'}</code> /
              <code>${'{captured.X}'}</code> /
              <code>${'{md5:...}'}</code> /
              <code>${'{url_encode:...}'}</code> /
              <code>${'{url_encode_json:...}'}</code>。
            </template>
          </n-form-item>
          <credentials-editor
            v-model:entries="draft.credentialEntries"
            v-model:clear-existing="draft.clear_credentials"
            :has-existing="hasExistingCredentials"
            preset-hint="必填：username + password（明文，后端按上面选的算法哈希后再发）"
          />
        </template>
      </n-form>

      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="editorVisible = false">取消</n-button>
          <n-button type="primary" :loading="submitting" @click="submitDraft">
            {{ isEditing ? "保存" : "创建" }}
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- ── 试跑结果弹窗 ──────────────────────────────────────────── -->
    <n-modal
      v-model:show="testResultVisible"
      preset="card"
      :title="`试跑结果 · ${testResultTargetName}`"
      :style="{ width: '720px', maxWidth: 'calc(100vw - 32px)' }"
    >
      <n-spin :show="testRunning">
        <template v-if="testResult">
          <div class="test-result__summary">
            <n-tag :type="testResult.success ? 'success' : 'error'" size="large" round>
              <template #icon>
                <span
                  :class="
                    testResult.success
                      ? 'i-carbon-checkmark-filled'
                      : 'i-carbon-close-filled'
                  "
                />
              </template>
              {{ testResult.success ? "成功" : "失败" }}
            </n-tag>
            <n-tag :bordered="false" size="medium" class="ml-2">
              <template #icon><span class="i-carbon-time" /></template>
              耗时 {{ testResult.elapsed_ms }} ms
            </n-tag>
            <n-tag
              v-if="testResult.fell_back_to"
              type="warning"
              :bordered="false"
              size="medium"
              class="ml-2"
            >
              自动降级到 {{ testResult.fell_back_to }}
            </n-tag>
          </div>

          <n-alert
            v-if="testResult.error"
            type="error"
            :show-icon="false"
            class="mt-3"
          >
            <span class="font-mono text-xs">{{ testResult.error }}</span>
            <span v-if="testResult.error_kind" class="ml-2 text-xs opacity-70">
              （{{ testResult.error_kind }}）
            </span>
            <div v-if="canExtendTimeout" class="mt-2">
              <n-button
                size="small"
                type="primary"
                ghost
                :loading="testRunning"
                @click="handleRetryWithLongerTimeout"
              >
                <template #icon><span class="i-carbon-renew" /></template>
                延长到 600s 重试
              </n-button>
              <span class="ml-2 text-xs opacity-70">
                慢速 LLM（如火山方舟 GLM 5.1）每轮可达 30-60s，10 步登录可能需要 5-10 分钟
              </span>
            </div>
          </n-alert>

          <n-descriptions
            v-if="showStateMeta"
            :column="2"
            size="small"
            label-placement="left"
            bordered
            class="mt-3"
          >
            <n-descriptions-item label="state 文件加载">
              {{ testResult.state_was_loaded ? "是" : "否" }}
            </n-descriptions-item>
            <n-descriptions-item label="state 已过期">
              {{ testResult.state_was_stale ? "是" : "否" }}
            </n-descriptions-item>
            <n-descriptions-item label="执行后保存 state">
              {{ testResult.state_was_saved ? "是" : "否" }}
            </n-descriptions-item>
            <n-descriptions-item label="保存路径">
              <code class="text-xs">{{
                testResult.state_saved_path ?? "—"
              }}</code>
            </n-descriptions-item>
          </n-descriptions>

          <div v-if="testResult.logs.length > 0" class="test-result__logs">
            <div class="test-result__label">执行日志</div>
            <pre>{{ testResult.logs.join("\n") }}</pre>
          </div>

          <div v-if="testResult.screenshot_base64" class="test-result__screenshot">
            <div class="test-result__label">结束时截图</div>
            <img
              :src="`data:image/png;base64,${testResult.screenshot_base64}`"
              alt="precondition screenshot"
            />
          </div>
        </template>
        <div v-else class="test-result__loading">
          <div>
            正在执行前置步骤…<strong>{{ testTimeoutSeconds }}s</strong> 超时上限。
          </div>
          <div class="text-xs opacity-70 mt-2">
            AI 登录的瓶颈在 LLM 推理速度（每轮 5-60s）；标准登录页通常 1-3 轮搞定。
            慢速模型（火山方舟 / GLM 5.1）下需要更长时间，超时后可选"延长到 600s 重试"。
          </div>
        </div>
      </n-spin>
      <template #footer>
        <div class="flex justify-end">
          <n-button @click="testResultVisible = false">关闭</n-button>
        </div>
      </template>
    </n-modal>

    <!-- ── 嵌入式 credentials 编辑器（kv 列表） ─────────────────── -->
  </div>
</template>

<script setup lang="ts">
import { computed, h, reactive, ref, watch } from "vue";
import {
  NAlert,
  NButton,
  NCheckbox,
  NDataTable,
  NDescriptions,
  NDescriptionsItem,
  NDivider,
  NEmpty,
  NForm,
  NFormItem,
  NGi,
  NGrid,
  NInput,
  NInputNumber,
  NModal,
  NPopconfirm,
  NSelect,
  NSpin,
  NTag,
  NTooltip,
  useMessage,
} from "naive-ui";
import type {
  DataTableColumns,
  FormInst,
  FormRules,
  FormItemRule,
} from "naive-ui";

import {
  createPreconditionApi,
  deletePreconditionApi,
  PRECONDITION_TYPE_META,
  testPreconditionApi,
  updatePreconditionApi,
} from "@/services/uiAutomation";
import type {
  PreconditionCreateParams,
  PreconditionTemplate,
  PreconditionType,
  PreconditionUpdateParams,
  TestPreconditionResult,
} from "@/services/uiAutomation";
import CredentialsEditor from "./CredentialsEditor.vue";

const props = defineProps<{
  environmentId: string;
  preconditions: PreconditionTemplate[];
}>();

const emit = defineEmits<{
  changed: [];
}>();

const message = useMessage();

// ─── 类型选项 ────────────────────────────────────────────────────────

const typeOptions = (Object.keys(PRECONDITION_TYPE_META) as PreconditionType[]).map(
  (k) => ({
    label: PRECONDITION_TYPE_META[k].label,
    value: k,
  }),
);

// ─── 排序后的展示列表 ───────────────────────────────────────────────

const sortedList = computed(() =>
  [...props.preconditions].sort((a, b) => {
    if (a.order_index !== b.order_index) return a.order_index - b.order_index;
    return a.created_at.localeCompare(b.created_at);
  }),
);

// ─── 编辑态 draft ────────────────────────────────────────────────────

interface CredentialEntry {
  key: string;
  value: string;
}

interface Draft {
  name: string;
  type: PreconditionType;
  description: string;
  order_index: number;
  enabled: boolean;

  // state_inject / ai_login 用扁平的子对象，提交前重新组装成 config
  stateConfig: {
    required: boolean;
    fallback_to_ai_login: boolean;
    verify_url: string;
  };
  aiConfig: {
    login_url: string;
    success_indicator: string;
    max_steps: number;
  };
  /**
   * http_login：纯 HTTP API 登录配置。
   * 5 个核心字段单独显式输入；高级字段（endpoint path 重写、extra_login_body、
   * extra_cookies）走 advancedJson（可选 JSON）。
   */
  httpLoginConfig: {
    auth_base_url: string;
    cookie_domain: string;
    verify_url: string;
    success_indicator: string;
    password_hash: "md5" | "sha256" | "none";
    advancedJson: string; // 可空；非空时是合法 JSON object
  };
  scriptedStepsJson: string;
  cookiesJson: string;

  // 凭据：明文 kv 列表
  credentialEntries: CredentialEntry[];
  clear_credentials: boolean;
}

function emptyDraft(): Draft {
  return {
    name: "",
    type: "state_inject",
    description: "",
    order_index: 0,
    enabled: true,

    stateConfig: {
      required: false,
      fallback_to_ai_login: true,
      verify_url: "",
    },
    aiConfig: {
      login_url: "/login",
      success_indicator: "",
      max_steps: 10,
    },
    httpLoginConfig: {
      auth_base_url: "",
      cookie_domain: "",
      verify_url: "",
      success_indicator: "",
      password_hash: "md5",
      advancedJson: "",
    },
    scriptedStepsJson: "[\n  { \"action\": \"goto\", \"url\": \"/\" }\n]",
    cookiesJson: "[\n  { \"name\": \"SESSIONID\", \"value_ref\": \"session_token\", \"domain\": \"\", \"path\": \"/\" }\n]",

    credentialEntries: [],
    clear_credentials: false,
  };
}

const draft = reactive<Draft>(emptyDraft());
const editingId = ref<string | null>(null);
const hasExistingCredentials = ref(false);
const editorVisible = ref(false);
const submitting = ref(false);
const formRef = ref<FormInst | null>(null);

const stateConfig = computed(() => draft.stateConfig);
const aiConfig = computed(() => draft.aiConfig);
const httpConfig = computed(() => draft.httpLoginConfig);

const isEditing = computed(() => editingId.value !== null);
const editorTitle = computed(() => (isEditing.value ? "编辑前置步骤" : "新增前置步骤"));
const currentTypeMeta = computed(() => PRECONDITION_TYPE_META[draft.type]);

const rules: FormRules = {
  name: [{ required: true, message: "请输入步骤名称", trigger: "blur" }],
  type: [{ required: true, message: "请选择类型", trigger: "change" }],
  scriptedStepsJson: [
    {
      validator: (_rule: FormItemRule, value: string) => {
        if (draft.type !== "scripted_steps") return true;
        try {
          const arr = JSON.parse(value);
          if (!Array.isArray(arr)) return new Error("需为数组");
          for (const s of arr) {
            if (!s || typeof s !== "object" || typeof s.action !== "string") {
              return new Error("每个步骤需包含 action 字段");
            }
          }
          return true;
        } catch {
          return new Error("JSON 解析失败");
        }
      },
      trigger: ["blur"],
    },
  ],
  cookiesJson: [
    {
      validator: (_rule: FormItemRule, value: string) => {
        if (draft.type !== "cookie_inject") return true;
        try {
          const arr = JSON.parse(value);
          if (!Array.isArray(arr) || arr.length === 0) {
            return new Error("需为非空数组");
          }
          for (const c of arr) {
            if (!c || typeof c !== "object" || typeof c.name !== "string") {
              return new Error("每个 cookie 需包含 name");
            }
          }
          return true;
        } catch {
          return new Error("JSON 解析失败");
        }
      },
      trigger: ["blur"],
    },
  ],
  httpAdvancedJson: [
    {
      validator: (_rule: FormItemRule, value: string) => {
        if (draft.type !== "http_login") return true;
        const trimmed = (value ?? "").trim();
        if (!trimmed) return true;
        try {
          const obj = JSON.parse(trimmed);
          if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
            return new Error("需为 JSON object");
          }
          return true;
        } catch {
          return new Error("JSON 解析失败");
        }
      },
      trigger: ["blur"],
    },
  ],
};

function resetDraft() {
  Object.assign(draft, emptyDraft());
  editingId.value = null;
  hasExistingCredentials.value = false;
}

function openCreate() {
  resetDraft();
  // 计算下一个 order_index = 现有最大 + 10（留出后续插队空间）
  const maxOrder = sortedList.value.reduce(
    (acc, p) => Math.max(acc, p.order_index),
    -10,
  );
  draft.order_index = maxOrder + 10;
  editorVisible.value = true;
}

function openEdit(row: PreconditionTemplate) {
  resetDraft();
  editingId.value = row.id;
  hasExistingCredentials.value = row.has_credentials;

  draft.name = row.name;
  draft.type = row.type;
  draft.description = row.description ?? "";
  draft.order_index = row.order_index;
  draft.enabled = row.enabled;

  // 按类型回填 config
  const cfg = row.config ?? {};
  if (row.type === "state_inject") {
    draft.stateConfig.required = Boolean(cfg.required ?? false);
    draft.stateConfig.fallback_to_ai_login = Boolean(cfg.fallback_to_ai_login ?? true);
    draft.stateConfig.verify_url = String(cfg.verify_url ?? "");
  } else if (row.type === "ai_login") {
    draft.aiConfig.login_url = String(cfg.login_url ?? "/login");
    draft.aiConfig.success_indicator = String(cfg.success_indicator ?? "");
    draft.aiConfig.max_steps = Number(cfg.max_steps ?? 10);
  } else if (row.type === "scripted_steps") {
    draft.scriptedStepsJson = JSON.stringify(cfg.steps ?? [], null, 2);
  } else if (row.type === "cookie_inject") {
    draft.cookiesJson = JSON.stringify(cfg.cookies ?? [], null, 2);
  } else if (row.type === "http_login") {
    draft.httpLoginConfig.auth_base_url = String(cfg.auth_base_url ?? "");
    draft.httpLoginConfig.cookie_domain = String(cfg.cookie_domain ?? "");
    draft.httpLoginConfig.verify_url = String(cfg.verify_url ?? "");
    draft.httpLoginConfig.success_indicator = String(cfg.success_indicator ?? "");
    const ph = String(cfg.password_hash ?? "md5");
    draft.httpLoginConfig.password_hash =
      ph === "sha256" ? "sha256" : ph === "none" ? "none" : "md5";
    // advanced：把"已知字段"以外的所有 config 序列化成 JSON 显示
    const known = new Set([
      "auth_base_url", "cookie_domain", "verify_url",
      "success_indicator", "password_hash",
    ]);
    const advanced: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(cfg)) {
      if (!known.has(k)) advanced[k] = v;
    }
    draft.httpLoginConfig.advancedJson =
      Object.keys(advanced).length > 0 ? JSON.stringify(advanced, null, 2) : "";
  }

  editorVisible.value = true;
}

function buildConfigFromDraft(): Record<string, unknown> {
  switch (draft.type) {
    case "state_inject":
      return {
        required: draft.stateConfig.required,
        fallback_to_ai_login: draft.stateConfig.fallback_to_ai_login,
        ...(draft.stateConfig.verify_url
          ? { verify_url: draft.stateConfig.verify_url }
          : {}),
      };
    case "ai_login":
      return {
        login_url: draft.aiConfig.login_url || "/login",
        success_indicator: draft.aiConfig.success_indicator || "",
        max_steps: draft.aiConfig.max_steps || 10,
      };
    case "scripted_steps":
      return { steps: JSON.parse(draft.scriptedStepsJson) };
    case "cookie_inject":
      return { cookies: JSON.parse(draft.cookiesJson) };
    case "http_login": {
      const base: Record<string, unknown> = {
        auth_base_url: draft.httpLoginConfig.auth_base_url.trim(),
        password_hash: draft.httpLoginConfig.password_hash,
      };
      if (draft.httpLoginConfig.cookie_domain.trim()) {
        base.cookie_domain = draft.httpLoginConfig.cookie_domain.trim();
      }
      if (draft.httpLoginConfig.verify_url.trim()) {
        base.verify_url = draft.httpLoginConfig.verify_url.trim();
      }
      if (draft.httpLoginConfig.success_indicator.trim()) {
        base.success_indicator = draft.httpLoginConfig.success_indicator.trim();
      }
      const adv = (draft.httpLoginConfig.advancedJson ?? "").trim();
      if (adv) {
        const advObj = JSON.parse(adv) as Record<string, unknown>;
        Object.assign(base, advObj);
      }
      return base;
    }
  }
}

function buildCredentialsFromDraft(): Record<string, string> | null {
  const entries = draft.credentialEntries.filter(
    (e) => e.key.trim() !== "" && e.value !== "",
  );
  if (entries.length === 0) return null;
  const out: Record<string, string> = {};
  for (const e of entries) {
    out[e.key.trim()] = e.value;
  }
  return out;
}

async function submitDraft() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }

  submitting.value = true;
  try {
    if (isEditing.value && editingId.value) {
      const creds = buildCredentialsFromDraft();
      const payload: PreconditionUpdateParams = {
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        config: buildConfigFromDraft(),
        order_index: draft.order_index,
        enabled: draft.enabled,
      };
      if (draft.clear_credentials) {
        payload.clear_credentials = true;
      } else if (creds) {
        payload.credentials = creds;
      }
      const res = await updatePreconditionApi(editingId.value, payload);
      if (res.success) {
        message.success("已保存");
        editorVisible.value = false;
        emit("changed");
      }
    } else {
      const payload: PreconditionCreateParams = {
        name: draft.name.trim(),
        type: draft.type,
        description: draft.description.trim() || null,
        config: buildConfigFromDraft(),
        order_index: draft.order_index,
        enabled: draft.enabled,
      };
      const creds = buildCredentialsFromDraft();
      if (creds) payload.credentials = creds;
      const res = await createPreconditionApi(props.environmentId, payload);
      if (res.success) {
        message.success("已创建");
        editorVisible.value = false;
        emit("changed");
      }
    }
  } catch (err: unknown) {
    const msg = extractErrorMessage(err) ?? "保存失败";
    message.error(msg);
  } finally {
    submitting.value = false;
  }
}

async function handleToggleEnabled(row: PreconditionTemplate) {
  try {
    const res = await updatePreconditionApi(row.id, { enabled: !row.enabled });
    if (res.success) {
      message.success(row.enabled ? "已停用" : "已启用");
      emit("changed");
    }
  } catch {
    message.error("更新失败");
  }
}

async function handleDelete(row: PreconditionTemplate) {
  try {
    const res = await deletePreconditionApi(row.id);
    if (res.success) {
      message.success("已删除");
      emit("changed");
    }
  } catch {
    message.error("删除失败");
  }
}

async function handleMove(row: PreconditionTemplate, delta: -1 | 1) {
  const idx = sortedList.value.findIndex((p) => p.id === row.id);
  const target = sortedList.value[idx + delta];
  if (!target) return;
  try {
    await Promise.all([
      updatePreconditionApi(row.id, { order_index: target.order_index }),
      updatePreconditionApi(target.id, { order_index: row.order_index }),
    ]);
    emit("changed");
  } catch {
    message.error("调整顺序失败");
  }
}

// ─── 试跑 ────────────────────────────────────────────────────────────

const testResultVisible = ref(false);
const testRunning = ref(false);
const testResult = ref<TestPreconditionResult | null>(null);
const testResultTargetName = ref("");

const showStateMeta = computed(() => {
  if (!testResult.value) return false;
  return (
    testResult.value.state_was_loaded ||
    testResult.value.state_was_saved ||
    testResult.value.state_saved_path !== null
  );
});

/** 试跑当前用的超时（秒）；初始 300s（与后端默认一致）。
 *  超时一次后用户可点"延长到 600s 重试"按钮，把这个值替换成 600 再调一次。 */
const testTimeoutSeconds = ref<number>(300);
const testTargetRow = ref<PreconditionTemplate | null>(null);

async function handleTest(row: PreconditionTemplate) {
  testTargetRow.value = row;
  testTimeoutSeconds.value = 300;
  await runTestOnce(row, 300);
}

async function handleRetryWithLongerTimeout() {
  if (!testTargetRow.value) return;
  testTimeoutSeconds.value = 600;
  await runTestOnce(testTargetRow.value, 600);
}

async function runTestOnce(
  row: PreconditionTemplate,
  timeoutSeconds: number,
) {
  testResultTargetName.value = row.name;
  testResult.value = null;
  testResultVisible.value = true;
  testRunning.value = true;
  try {
    const res = await testPreconditionApi(props.environmentId, row.id, {
      persist_state: false,
      timeout_seconds: timeoutSeconds,
    });
    if (res.success) {
      testResult.value = res.data;
    } else {
      message.error("试跑返回非 success");
      testResultVisible.value = false;
    }
  } catch (err: unknown) {
    const msg = extractErrorMessage(err) ?? "试跑失败";
    message.error(msg);
    testResultVisible.value = false;
  } finally {
    testRunning.value = false;
  }
}

/** 超时类失败 → 提供"延长到 600s 重试"的 actionable 出口；当前已经
 *  600s 了就别再无限提示。 */
const isTestTimeoutError = computed(() => {
  if (!testResult.value) return false;
  if (testResult.value.success) return false;
  return testResult.value.error_kind === "timeout";
});

const canExtendTimeout = computed(
  () => isTestTimeoutError.value && testTimeoutSeconds.value < 600,
);

// ─── 表格列 ─────────────────────────────────────────────────────────

const columns = computed<DataTableColumns<PreconditionTemplate>>(() => [
  {
    title: "顺序",
    key: "order_index",
    width: 90,
    render: (row, idx) =>
      h("div", { class: "order-cell" }, [
        h("span", { class: "order-cell__num" }, String(row.order_index)),
        h(
          NButton,
          {
            size: "tiny",
            quaternary: true,
            disabled: idx === 0,
            onClick: () => handleMove(row, -1),
          },
          { default: () => h("span", { class: "i-carbon-chevron-up" }) },
        ),
        h(
          NButton,
          {
            size: "tiny",
            quaternary: true,
            disabled: idx === sortedList.value.length - 1,
            onClick: () => handleMove(row, 1),
          },
          { default: () => h("span", { class: "i-carbon-chevron-down" }) },
        ),
      ]),
  },
  {
    title: "名称",
    key: "name",
    // **关键**：必须给名称列足够 minWidth，否则其他列加起来挤完，名称列只剩
    // 几十 px，3+ 字就会换行，看着每条记录"很高很丑"。modal 宽度也调整到了
    // 880px 配合（详见 ``EnvironmentWizard`` 的 ``n-modal style.width``）。
    minWidth: 220,
    resizable: true,
    render: (row) =>
      h("div", { class: "precondition-name-cell" }, [
        h(
          "div",
          {
            class: "precondition-name-cell__title",
            title: row.name,
          },
          row.name,
        ),
        row.description
          ? h(
              "div",
              {
                class: "precondition-name-cell__desc",
                title: row.description,
              },
              row.description,
            )
          : null,
      ]),
  },
  {
    title: "类型",
    key: "type",
    width: 160,
    render: (row) => {
      const meta = PRECONDITION_TYPE_META[row.type];
      return h(
        NTag,
        { type: meta.color, size: "small", bordered: false },
        {
          icon: () => h("span", { class: meta.icon }),
          default: () => meta.label,
        },
      );
    },
  },
  {
    title: "凭据",
    key: "has_credentials",
    width: 80,
    render: (row) =>
      row.has_credentials
        ? h("span", { class: "i-carbon-locked text-gray-500", title: "已加密保存" })
        : h("span", { class: "text-gray-300" }, "—"),
  },
  {
    title: "启用",
    key: "enabled",
    width: 80,
    render: (row) =>
      h(NCheckbox, {
        checked: row.enabled,
        onUpdateChecked: () => handleToggleEnabled(row),
      }),
  },
  {
    title: "操作",
    key: "actions",
    // 操作列从 230 → 132：用 icon-only 按钮 + tooltip 提示，把腾出来的空间
    // 留给名称列。三个按钮 (28px each) + 2*gap(6px) ≈ 96px + 表格 padding。
    width: 132,
    align: "center",
    titleAlign: "center",
    render: (row) =>
      h("div", { class: "precondition-actions" }, [
        h(
          NTooltip,
          { trigger: "hover", placement: "top" },
          {
            trigger: () =>
              h(
                NButton,
                {
                  size: "tiny",
                  quaternary: true,
                  type: "primary",
                  onClick: () => handleTest(row),
                },
                { icon: () => h("span", { class: "i-carbon-play" }) },
              ),
            default: () => "试跑",
          },
        ),
        h(
          NTooltip,
          { trigger: "hover", placement: "top" },
          {
            trigger: () =>
              h(
                NButton,
                {
                  size: "tiny",
                  quaternary: true,
                  onClick: () => openEdit(row),
                },
                { icon: () => h("span", { class: "i-carbon-edit" }) },
              ),
            default: () => "编辑",
          },
        ),
        h(
          NPopconfirm,
          { onPositiveClick: () => handleDelete(row) },
          {
            trigger: () =>
              h(
                NTooltip,
                { trigger: "hover", placement: "top" },
                {
                  trigger: () =>
                    h(
                      NButton,
                      { size: "tiny", quaternary: true, type: "error" },
                      { icon: () => h("span", { class: "i-carbon-trash-can" }) },
                    ),
                  default: () => "删除",
                },
              ),
            default: () => `确认删除「${row.name}」？`,
          },
        ),
      ]),
  },
]);

function extractErrorMessage(err: unknown): string | null {
  if (err && typeof err === "object" && "data" in err) {
    const data = (err as { data?: { message?: string } }).data;
    if (data?.message) return data.message;
  }
  if (err instanceof Error) return err.message;
  return null;
}

// 类型切换时显式重置 clear_credentials（避免切过去切回来时残留）
watch(
  () => draft.type,
  () => {
    if (!isEditing.value) {
      draft.clear_credentials = false;
    }
  },
);
</script>

<style scoped>
.precondition-editor__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.precondition-editor__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
  flex: 1;
}

.precondition-editor__section-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
  margin: 0 0 12px;
}

:deep(.order-cell) {
  display: flex;
  align-items: center;
  gap: 2px;
}

/* 名称单元格：标题加粗 + 单行 ellipsis；说明小字 + 单行 ellipsis；
   不再像之前那样让说明文本无限堆叠把整行撑高（用户反馈"3 字换行"
   就是描述折行 + 名称列宽不足）。 */
:deep(.precondition-name-cell) {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

:deep(.precondition-name-cell__title) {
  font-weight: 500;
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.5;
}

:deep(.precondition-name-cell__desc) {
  font-size: 12px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.4;
}

:deep(.precondition-actions) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

:deep(.order-cell__num) {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12px;
  color: var(--text-tertiary);
  width: 24px;
  text-align: right;
  margin-right: 4px;
}

.test-result__summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}

.test-result__label {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 6px;
  font-weight: 500;
}

.test-result__logs {
  margin-top: 16px;
}

.test-result__logs pre {
  background: var(--bg-subtle, #f5f5f5);
  padding: 10px 12px;
  border-radius: var(--radius-md);
  font-size: 12px;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  line-height: 1.55;
}

.test-result__screenshot {
  margin-top: 16px;
}

.test-result__screenshot img {
  width: 100%;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  display: block;
}

.test-result__loading {
  text-align: center;
  color: var(--text-tertiary);
  padding: 40px 0;
  font-size: 13px;
}
</style>
