<template>
  <div class="login-page">
    <div class="login-page__bg">
      <div class="login-page__blob login-page__blob--1" />
      <div class="login-page__blob login-page__blob--2" />
      <div class="login-page__blob login-page__blob--3" />
      <div class="login-page__grid" />
    </div>

    <div class="login-page__content">
      <!-- 左侧品牌区域 -->
      <aside class="login-page__brand">
        <div class="login-brand">
          <div class="login-brand__logo">
            <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
              <path
                d="M16 3 L27 9 V23 L16 29 L5 23 V9 Z"
                fill="rgba(255, 255, 255, 0.95)"
                stroke="rgba(255,255,255,0.6)"
                stroke-width="0.6"
              />
              <path
                d="M11 21 L16 11 L21 21 M13 17 H19"
                fill="none"
                stroke="#4338CA"
                stroke-width="2.4"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            <div class="login-brand__name">
              <span class="login-brand__title">AITest</span>
              <span class="login-brand__sub">Platform</span>
            </div>
          </div>

          <h2 class="login-brand__heading">
            <span>更聪明的</span>
            <span class="login-brand__heading-accent">AI 测试平台</span>
          </h2>
          <p class="login-brand__lead">
            从需求评审到用例生成，整合 LLM 能力，为质量保障团队打造现代、协作、高效的工作台。
          </p>

          <ul class="login-brand__features">
            <li>
              <span class="login-brand__feature-icon i-carbon-analytics" />
              <div>
                <strong>需求评审</strong>
                <p>一键扫描需求文档，定位风险与歧义。</p>
              </div>
            </li>
            <li>
              <span class="login-brand__feature-icon i-carbon-magic-wand" />
              <div>
                <strong>用例生成</strong>
                <p>结合提示词模板批量生产高质量用例。</p>
              </div>
            </li>
            <li>
              <span class="login-brand__feature-icon i-carbon-chat-bot" />
              <div>
                <strong>AI 对话</strong>
                <p>多模型流式对话，支持文件与上下文检索。</p>
              </div>
            </li>
            <li>
              <span class="login-brand__feature-icon i-carbon-task" />
              <div>
                <strong>用例管理</strong>
                <p>模块化组织、批量编辑、执行结果追踪。</p>
              </div>
            </li>
          </ul>
        </div>
      </aside>

      <!-- 右侧表单 -->
      <main class="login-page__form-wrap">
        <n-card class="login-card" :bordered="false">
          <div class="login-card__header">
            <h1>欢迎回来</h1>
            <p>登录以继续访问 AITestPlatform</p>
          </div>

          <n-tabs
            v-model:value="activeTab"
            type="segment"
            justify-content="space-evenly"
            animated
            class="login-card__tabs"
          >
            <n-tab-pane name="login" tab="登录">
              <n-form ref="loginFormRef" :model="loginForm" :rules="loginRules" class="login-form">
                <n-form-item path="username" label="用户名 / 邮箱">
                  <n-input
                    v-model:value="loginForm.username"
                    placeholder="请输入用户名或邮箱"
                    size="large"
                    :input-props="{ autocomplete: 'username' }"
                  >
                    <template #prefix>
                      <span class="i-carbon-user text-base text-gray-400" />
                    </template>
                  </n-input>
                </n-form-item>
                <n-form-item path="password" label="密码">
                  <n-input
                    v-model:value="loginForm.password"
                    type="password"
                    show-password-on="click"
                    placeholder="请输入密码"
                    size="large"
                    :input-props="{ autocomplete: 'current-password' }"
                    @keyup.enter="handleLogin"
                  >
                    <template #prefix>
                      <span class="i-carbon-locked text-base text-gray-400" />
                    </template>
                  </n-input>
                </n-form-item>
                <n-button
                  type="primary"
                  block
                  size="large"
                  :loading="loginLoading"
                  class="login-form__submit"
                  @click="handleLogin"
                >
                  登 录
                </n-button>
              </n-form>
            </n-tab-pane>

            <n-tab-pane name="register" tab="注册">
              <n-form
                ref="registerFormRef"
                :model="registerForm"
                :rules="registerRules"
                class="login-form"
              >
                <n-form-item path="username" label="用户名">
                  <n-input
                    v-model:value="registerForm.username"
                    placeholder="3-50 位，字母 / 数字 / 下划线"
                    size="large"
                    :input-props="{ autocomplete: 'username' }"
                  >
                    <template #prefix>
                      <span class="i-carbon-user text-base text-gray-400" />
                    </template>
                  </n-input>
                </n-form-item>
                <n-form-item path="email" label="邮箱">
                  <n-input
                    v-model:value="registerForm.email"
                    placeholder="name@company.com"
                    size="large"
                    :input-props="{ autocomplete: 'email' }"
                  >
                    <template #prefix>
                      <span class="i-carbon-email text-base text-gray-400" />
                    </template>
                  </n-input>
                </n-form-item>
                <n-form-item path="password" label="密码">
                  <n-input
                    v-model:value="registerForm.password"
                    type="password"
                    show-password-on="click"
                    placeholder="至少 6 位"
                    size="large"
                    :input-props="{ autocomplete: 'new-password' }"
                  >
                    <template #prefix>
                      <span class="i-carbon-locked text-base text-gray-400" />
                    </template>
                  </n-input>
                </n-form-item>
                <n-form-item path="display_name" label="昵称（选填）">
                  <n-input
                    v-model:value="registerForm.display_name"
                    placeholder="显示名称"
                    size="large"
                  >
                    <template #prefix>
                      <span class="i-carbon-user-avatar text-base text-gray-400" />
                    </template>
                  </n-input>
                </n-form-item>
                <n-button
                  type="primary"
                  block
                  size="large"
                  :loading="registerLoading"
                  class="login-form__submit"
                  @click="handleRegister"
                >
                  创 建 账 号
                </n-button>
              </n-form>
            </n-tab-pane>
          </n-tabs>

          <div class="login-card__footer">
            登录即表示同意《服务条款》与《隐私政策》
          </div>
        </n-card>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from "vue";
import { useRouter, useRoute } from "vue-router";
import {
  NCard,
  NForm,
  NFormItem,
  NInput,
  NButton,
  NTabs,
  NTabPane,
  useMessage,
  type FormInst,
  type FormRules,
} from "naive-ui";
import { useAuthStore } from "@/stores/auth";

const router = useRouter();
const route = useRoute();
const message = useMessage();
const authStore = useAuthStore();

const activeTab = ref("login");
const loginLoading = ref(false);
const registerLoading = ref(false);
const loginFormRef = ref<FormInst | null>(null);
const registerFormRef = ref<FormInst | null>(null);

const loginForm = reactive({ username: "", password: "" });
const loginRules: FormRules = {
  username: { required: true, message: "请输入用户名或邮箱", trigger: "blur" },
  password: { required: true, message: "请输入密码", trigger: "blur" },
};

const registerForm = reactive({
  username: "",
  email: "",
  password: "",
  display_name: "",
});
const registerRules: FormRules = {
  username: [
    { required: true, message: "请输入用户名", trigger: "blur" },
    { min: 3, max: 50, message: "3-50个字符", trigger: "blur" },
    { pattern: /^[a-zA-Z0-9_]+$/, message: "仅支持字母、数字和下划线", trigger: "blur" },
  ],
  email: [
    { required: true, message: "请输入邮箱", trigger: "blur" },
    { type: "email", message: "邮箱格式不正确", trigger: "blur" },
  ],
  password: [
    { required: true, message: "请输入密码", trigger: "blur" },
    { min: 6, message: "至少6位", trigger: "blur" },
  ],
};

async function handleLogin() {
  try {
    await loginFormRef.value?.validate();
  } catch {
    return;
  }
  loginLoading.value = true;
  try {
    await authStore.login(loginForm);
    message.success("登录成功");
    const redirect = (route.query.redirect as string) || "/";
    router.push(redirect);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "登录失败";
    message.error(msg);
  } finally {
    loginLoading.value = false;
  }
}

async function handleRegister() {
  try {
    await registerFormRef.value?.validate();
  } catch {
    return;
  }
  registerLoading.value = true;
  try {
    await authStore.register(registerForm);
    message.success("注册成功，请登录");
    activeTab.value = "login";
    loginForm.username = registerForm.username;
    loginForm.password = "";
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "注册失败";
    message.error(msg);
  } finally {
    registerLoading.value = false;
  }
}
</script>

<style scoped>
.login-page {
  position: relative;
  min-height: 100vh;
  width: 100%;
  background: linear-gradient(120deg, #eef2ff 0%, #f5f7fb 50%, #faf5ff 100%);
  overflow: hidden;
}

html.dark .login-page {
  background: linear-gradient(120deg, #0b0d12 0%, #12141b 60%, #1a1326 100%);
}

.login-page__bg {
  position: absolute;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
}

.login-page__blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.55;
}

.login-page__blob--1 {
  width: 480px;
  height: 480px;
  top: -120px;
  left: -120px;
  background: radial-gradient(circle at 30% 30%, #6366f1, transparent 70%);
}

.login-page__blob--2 {
  width: 520px;
  height: 520px;
  bottom: -160px;
  right: -120px;
  background: radial-gradient(circle at 70% 70%, #a855f7, transparent 70%);
}

.login-page__blob--3 {
  width: 360px;
  height: 360px;
  top: 40%;
  left: 50%;
  transform: translate(-50%, -50%);
  background: radial-gradient(circle, #38bdf8, transparent 70%);
  opacity: 0.25;
}

.login-page__grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(15, 23, 42, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(15, 23, 42, 0.04) 1px, transparent 1px);
  background-size: 32px 32px;
  mask-image: radial-gradient(ellipse at center, #000 50%, transparent 80%);
}

html.dark .login-page__grid {
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.04) 1px, transparent 1px);
}

.login-page__content {
  position: relative;
  z-index: 1;
  min-height: 100vh;
  display: grid;
  grid-template-columns: 1fr 1fr;
  align-items: center;
  gap: 48px;
  padding: 48px;
  max-width: 1280px;
  margin: 0 auto;
}

@media (max-width: 960px) {
  .login-page__content {
    grid-template-columns: 1fr;
    padding: 32px 20px;
  }
  .login-page__brand {
    display: none;
  }
}

.login-brand {
  max-width: 520px;
  color: var(--text-primary);
}

.login-brand__logo {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 32px;
}

.login-brand__logo svg {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: var(--brand-gradient);
  padding: 8px;
  box-shadow: 0 10px 24px -8px rgba(79, 70, 229, 0.6);
}

.login-brand__name {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}

.login-brand__title {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.3px;
}

.login-brand__sub {
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.6px;
  text-transform: uppercase;
  color: var(--text-tertiary);
}

.login-brand__heading {
  font-size: 40px;
  line-height: 1.18;
  font-weight: 700;
  letter-spacing: -0.5px;
  margin: 0 0 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.login-brand__heading-accent {
  background: var(--brand-gradient);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
}

.login-brand__lead {
  font-size: 15px;
  color: var(--text-secondary);
  line-height: 1.65;
  margin: 0 0 32px;
}

.login-brand__features {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.login-brand__features li {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 14px 16px;
  background: rgba(255, 255, 255, 0.6);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(15, 23, 42, 0.05);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xs);
}

html.dark .login-brand__features li {
  background: rgba(255, 255, 255, 0.04);
  border-color: rgba(255, 255, 255, 0.06);
}

.login-brand__feature-icon {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: 10px;
  background: var(--brand-primary-soft);
  color: var(--brand-primary);
  display: inline-flex !important;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  /* iconify 通过 ::before 渲染图标，确保 box-sizing 正确 */
  box-sizing: border-box;
}

.login-brand__features li strong {
  display: block;
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 2px;
}

.login-brand__features li p {
  margin: 0;
  font-size: 13px;
  color: var(--text-tertiary);
}

.login-page__form-wrap {
  display: flex;
  justify-content: center;
}

.login-card {
  width: 100%;
  max-width: 440px;
  background: var(--bg-card);
  border-radius: var(--radius-xl) !important;
  box-shadow:
    0 20px 60px -20px rgba(15, 23, 42, 0.18),
    0 1px 0 rgba(255, 255, 255, 0.6) inset;
}

html.dark .login-card {
  box-shadow: 0 20px 60px -20px rgba(0, 0, 0, 0.6);
}

.login-card :deep(.n-card__content) {
  padding: 28px 32px 24px;
}

.login-card__header {
  text-align: center;
  margin-bottom: 20px;
}

.login-card__header h1 {
  font-size: 22px;
  font-weight: 700;
  margin: 0 0 4px;
  letter-spacing: -0.2px;
}

.login-card__header p {
  font-size: 13px;
  color: var(--text-tertiary);
  margin: 0;
}

.login-card__tabs :deep(.n-tabs-pane-wrapper) {
  padding-top: 6px;
}

.login-form {
  margin-top: 12px;
}

.login-form__submit {
  margin-top: 8px;
  font-size: 15px !important;
  height: 44px !important;
  background: var(--brand-gradient) !important;
  border: 0 !important;
  letter-spacing: 4px;
  box-shadow: 0 8px 18px -6px rgba(79, 70, 229, 0.55);
}

.login-card__footer {
  margin-top: 18px;
  text-align: center;
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
