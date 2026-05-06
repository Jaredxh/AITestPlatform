# 第二期：AI 驱动 UI 自动化测试模块 - 设计文档 v3.0

> **v3.0 关键调整**（基于一期已落地能力的复盘）：
> 1. 浏览器执行后端从虚构的 `@playwright/cli` 改为微软官方 **Playwright MCP** + Playwright Python SDK 双栈方案
> 2. AI 决策完全复用一期已建成的 **OpenAI tool-calling 循环**（`agent_tools.py`），不再自建 prompt-JSON 解析
> 3. 任务调度优先沿用一期的 **ChatStreamHub 模式**（in-process asyncio + SSE 重连），ARQ + Worker 容器降级为 Phase 11 的可选增强
> 4. 安全模型从黑名单改为 **MCP 白名单 + URL 域名校验**
> 5. 新增 **Snapshot 裁剪与 Token 预算治理**，防止 UI 测试单次成本失控
> 6. 明确 `ExecutionEngine / StepRunner / AssertionJudge` 三层职责分离
> 7. 与一期 `intent_handler` 完成统一接入（IntentType.RUN_UI_TEST）
> 8. **新增"测试物料"体系（§2.4）+ 执行触发配置弹窗（§2.5.1）+ TestDataResolver（§3.6）**：解决"用例文本只描述做什么、缺少具体数据"的真实业务痛点；五级层级 + 六种类型 + 三层注入策略
> 9. **v3.0.1 易用性优化**：单弹窗折叠式触发 UI（90% 场景一秒确认，无必填）；缺料**非阻断**改为"AI 自造数据兜底"（`platform_synthesize_data` 启发式 + AI 推断）；执行结果三级数据可信度评级 `reliable / synthesized / data_failure`，**业务通过率自动排除"数据问题导致的失败"**

## 一、模块定位

### 1.1 核心理念

**"选用例 → 配环境 → AI 执行 → 看结果"，四步完成 UI 自动化测试。**

传统 UI 自动化需要写代码、维护脚本、调试定位器。本模块的核心突破在于：用户只需提供**自然语言描述的测试用例**（一期 AI 生成的），AI 通过 **Playwright MCP（Model Context Protocol）** 逐步操控浏览器完成测试，全程无需写一行代码。

**与一期能力的强复用**：

```
一期已建成（不要重新发明）：
  ├─ agent_tools.py  → OpenAI tool-calling 循环 + 工具注册中心
  ├─ chat_service.py → 后台任务 + ChatStreamHub（SSE 重连/续接）
  ├─ intent_handler  → 用户消息意图识别（review / generate）
  └─ PromptTemplate  → 多分类 + 自动注入 system prompt

二期新增（在一期之上叠加）：
  ├─ MCP playwright 工具集 → 注册到 agent_tools 的 TOOL_REGISTRY
  ├─ ExecutionStreamHub    → 复刻 ChatStreamHub 给"执行任务"用
  ├─ IntentType.RUN_UI_TEST → 在 intent_handler 增加新意图
  └─ "UI 自动化执行专家" 提示词 → 写入 PromptTemplate 系统模板
```

### 1.2 与一期的关系

```
一期用例 ──选择──→ 二期 UI 自动化执行 ──产出──→ 测试报告+截图+视频
   │                    ↑
   │              配置执行环境
   │              (URL + 前置步骤)
   └──AI生成──→ 用例库
```

一期产出的测试用例（手工创建或 AI 生成）是二期的**输入源**。二期不重复管理用例，而是在用例列表上叠加"执行 UI 测试"的能力。

### 1.3 对比 WHartTest Actuator 的改进

| 维度 | WHartTest Actuator | 新平台（v3.0 MCP 方案） |
|------|-------------------|---------------------|
| 架构 | 桌面客户端 | 服务端 Playwright MCP + Python SDK 兜底 |
| AI 操控方式 | 无 AI | AI 通过 OpenAI tool-calling 自主决定每步操作 |
| 页面感知 | 手动写选择器 | accessibility 快照（MCP `browser_snapshot`），AI 用 ref 精确定位 |
| Token 效率 | - | 高（snapshot 裁剪 + diff 增量 + token 预算守卫） |
| 错误恢复 | 需手动修复 | AI 在同一 tool-calling 循环内重试单步 |
| 安全性 | 无沙箱 | MCP 工具白名单 + URL 域名白名单 + 凭据加密 |
| 会话管理 | 无 | MCP 持久 browser context + Python SDK `storage_state` 复用 |
| 视频/Trace | 无 | Python SDK `context.tracing` + `context.recordVideo`（MCP 暂不直接覆盖） |
| 可视监控 | 切换到客户端看 | `npx @playwright/mcp --vision`（开发期）+ Web 实时截图流 |

### 1.4 为什么选择 Playwright MCP 而非自建 CLI Wrapper

**Playwright MCP**（`@playwright/mcp`，微软官方维护）是基于 [Model Context Protocol](https://modelcontextprotocol.io) 的浏览器操控服务，已成为 Cursor / Claude Desktop / VS Code Copilot 的事实标准浏览器集成方式。

| 对比项 | 自建 CLI Wrapper（v2 旧方案） | Playwright MCP（v3.0 选型） |
|--------|------------------------|---------------------|
| 协议 | 自定义 stdout/stderr 解析 | **MCP 标准 stdio 协议**（JSON-RPC over stdin/stdout） |
| 与 LLM 集成 | 需自己 prompt 模型按 JSON 输出命令再 parse | **直接作为 OpenAI tool 暴露给一期 agent_tools 循环** |
| 工具集 | 自己定义命令字符串 | 官方维护的 `browser_navigate / browser_click / browser_type / browser_snapshot / browser_screenshot / browser_wait_for` 等 |
| 元素定位 | 自定义 ref | 标准 `aria-ref`（accessibility tree 节点 id） |
| 安全模型 | 命令字符串黑名单 | **天然白名单**（只暴露注册过的 MCP tool） |
| 兼容生态 | 仅本平台 | 任何支持 MCP 的客户端都能复用相同 server |
| 状态感知 | 每步返回快照 | **同样是每步返回 accessibility 快照**（核心优势保留） |
| 维护 | 自己适配 Playwright 版本变化 | 微软官方维护 |

**最大优势**：不用维护"AI ↔ 浏览器"翻译层。一期已经有 `agent_tools.run_tool(name, args_json)` 的 OpenAI tool-calling 循环，把 MCP 暴露的工具批量注册进去即可。MCP server 也提供每步 accessibility 快照（含元素 role、name 和 ref），等同于给 AI 一双"眼睛"。

**MCP 暂未覆盖的能力如何兜底**：
- `storage_state save/load`（登录态复用）→ 用同进程 **Playwright Python SDK** 的 `context.storage_state()` 处理
- 视频录制 / Trace → Python SDK 的 `record_video_dir` + `context.tracing.start/stop()`
- Cookie 注入 → Python SDK 的 `context.add_cookies()`

也就是说：**AI 看得见的浏览器交互走 MCP；AI 看不见的"管家活儿"走 Python SDK**，两者共享同一个 browser context（通过 MCP server 的 `--browser-cdp-endpoint` 模式或自己起 Chromium 把 CDP 暴露给 MCP）。

---

## 二、功能设计

### 2.1 功能全景图

```
┌──────────────────────────────────────────────────────────────────────┐
│                       UI 自动化测试模块（v3.0）                        │
├────────────────┬────────────────┬───────────────┬───────────────────┤
│  环境配置       │  测试物料       │  用例执行       │  结果查看           │
├────────────────┼────────────────┼───────────────┼───────────────────┤
│• 测试环境管理   │• 物料集（5级层）│• 配置弹窗      │• 执行历史列表        │
│  - 目标 URL    │  - 项目级默认  │  - 选环境     │• 执行详情           │
│  - 浏览器配置  │  - 环境级      │  - 前置步骤   │  - 每步截图         │
│• 前置步骤模板   │  - 用例级      │    临时覆盖   │  - accessibility    │
│  - State 复用  │  - 个人级      │  - 物料合并   │    快照              │
│  - AI 智能登录 │  - 执行级（一次）│    + 临时覆盖 │  - 操作日志         │
│  - CLI 步骤     │• 物料类型（6种）│  - 缺料告警   │  - 通过/失败/异常    │
│  - Cookie 注入 │  - string      │  - 推荐物料集 │  - 物料快照面板      │
│  - 验证码 OCR   │  - secret 加密 │  - 高级（token│  - tool_call 时间线 │
│                │  - multiline   │    /LLM/模式）│• 视频回放           │
│                │  - file        │• 单/批量/对话  │• 执行统计报告        │
│                │  - random 模板 │  执行         │  - 通过率           │
│                │  - dataset     │• AI tool 循环 │  - 耗时             │
│                │• CSV/JSON 导入 │• SSE 实时进度 │  - 物料使用统计     │
│                │• 文件上传      │• 调试模式     │• 历史回放（无浏览器）│
│                │                │• 停止 / 视频   │                    │
└────────────────┴────────────────┴───────────────┴───────────────────┘
```

### 2.2 核心流程

```
用户操作流程（v3.0 五步：增加"配物料"）：

Step 1: 配置环境（一次配好，后续复用）
  ┌──────────────────────────────┐
  │ 测试环境: 开发环境            │
  │ 目标 URL: https://dev.xxx.com │
  │ 前置步骤: [登录步骤模板]      │
  │ 浏览器: Chromium / 1920x1080 │
  └──────────────────────────────┘

Step 2: 准备测试物料（一次配好，多次复用，多层覆盖）
  ┌────────────────────────────────────────┐
  │ 物料集：登录账号（项目默认）             │
  │   username = admin                     │
  │   password = ●●●●●●●（加密）            │
  │ 物料集：订单数据（项目级）               │
  │   product_id = SKU-1001                │
  │   address = 北京市...                  │
  └────────────────────────────────────────┘

Step 3: 选择用例并打开执行配置弹窗
  ☑ 用例1: 验证用户注册功能
  ☑ 用例3: 验证密码修改功能
  → 弹窗自动加载默认物料 + 检测缺料 + 推荐
  → 临时调整某些值（不污染默认集）→ 一键开跑

Step 4: AI + Playwright MCP 开始工作
  AI 正在通过 MCP 操控浏览器...（已自动注入物料清单）
  ├── 用例1: 执行中 🔄 [截图预览] [实时快照] [token 50%]
  │     └── 🔒 已获取敏感物料: password
  └── 用例3: 等待中 ⏳

Step 5: 查看结果
  用例1: ✅ 通过 (12.3s) [查看详情] [看视频]
  用例3: ❌ 失败 - 未找到"确认修改"按钮 [查看截图]
```

### 2.3 环境配置详细设计

#### 2.3.1 测试环境

每个项目可配置多个测试环境（开发/测试/预发布）：

```json
{
  "name": "开发环境",
  "base_url": "https://dev.example.com",
  "browser": "chromium",
  "viewport": { "width": 1920, "height": 1080 },
  "timeout_ms": 30000,
  "headless": true,
  "session_name": "dev-env"
}
```

`session_name` 用于浏览器 context 隔离：每个环境对应独立的 Playwright `BrowserContext`（含 storage_state、cookies、cache），不同环境互不污染。MCP server 的实例池按 session_name 复用。

#### 2.3.2 前置步骤（重点设计）

前置步骤解决"被测系统需要先登录"的问题。支持三种模式，灵活度递增：

**模式一：State 注入（最高效，推荐）**
```json
{
  "type": "state_inject",
  "description": "从已保存的登录态恢复",
  "state_file": "login-state.json"
}
```
执行方式：Python SDK `browser.new_context(storage_state="login-state.json")`

适用场景：已有保存好的登录态文件。首次可通过"模式二"登录后自动保存。

**模式二：AI 自然语言登录（最智能）**
```json
{
  "type": "ai_login",
  "description": "打开登录页，输入用户名 admin，密码 123456，点击登录按钮，等待跳转到首页",
  "save_state": true,
  "state_file": "login-state.json"
}
```
执行方式：复用与正式用例相同的 `StepRunner` agent tool-calling 循环，AI 通过 MCP `browser_*` 工具完成登录，结束后用 Python SDK `context.storage_state(path=...)` 持久化登录态。

适用场景：不想写选择器，让 AI 自己看 accessibility 快照找元素。

**模式三：脚本步骤模板（最精确，零 LLM 成本）**
```json
{
  "type": "scripted_steps",
  "steps": [
    {"tool": "browser_navigate", "args": {"url": "{{BASE_URL}}/login"}},
    {"tool": "browser_type",     "args": {"ref": "e12", "text": "{{TEST_USER}}"}},
    {"tool": "browser_type",     "args": {"ref": "e15", "text": "{{TEST_PASS}}"}},
    {"tool": "browser_click",    "args": {"ref": "e20"}},
    {"tool": "browser_wait_for", "args": {"text": "欢迎"}}
  ],
  "save_state": true,
  "state_file": "login-state.json"
}
```
适用场景：已知元素 ref 或选择器、想要 100% 确定性、不希望消耗 LLM token。每条 step 直接调用 MCP tool，绕开 AI 决策层。

**模式四：Cookie/Token 注入（兜底方案）**
```json
{
  "type": "cookie_inject",
  "cookies": [
    { "name": "session_id", "value": "{{SESSION_TOKEN}}", "domain": ".example.com" }
  ]
}
```
执行方式：Python SDK `context.add_cookies([...])`

适用场景：有现成的登录态 Cookie 或 Token。

**变量系统**：前置步骤中的 `{{xxx}}` 变量从环境配置的凭据中替换，敏感信息（密码）加密存储，不明文显示。

**State 复用机制**（核心优化）：
```
首次执行: AI 登录 → context.storage_state(path=...) → 保存到 ./uploads/ui-states/<env_id>/<sha>.json
后续执行: storage_state=<path> 创建 context → 直接进入已登录状态（跳过登录步骤，省时省 Token）
过期处理: state 加载后 AI 调 browser_snapshot 判断是否仍在登录态 → 过期则 fallback 到 AI 重新登录 → 覆盖 state 文件
```

**State 文件治理**：
- 命名按 `<environment_id>/<credentials_sha256>.json`，凭据变更时新生成、旧文件标记 stale
- 落盘路径加挂载卷 `backend_uploads`（与文档上传同卷），保证 docker 重启不丢
- 默认 7 天过期清理；用户也可在环境配置页"清空登录态"手动重置
- 多 worker 场景下文件路径包含 environment_id，互不冲突；并发执行同环境通过 redis lock（Phase 11 引入 ARQ 时一并加）

#### 2.3.3 验证码识别（登录场景）

部分被测系统登录页面包含图形验证码（数字/字母），需要在前置登录步骤中自动识别。采用**分层策略**，优先绕过，必要时 OCR 识别：

**分层处理策略**：
```
Level 0: State 复用（默认首选）
  └─ state-load 恢复登录态 → 成功则跳过整个登录（包括验证码）
  └─ 失败（state 过期）→ 进入 Level 1

Level 1: 万能验证码 / 环境配置绕过
  └─ 环境配置中填入万能验证码值（如 "0000"）→ 直接填入
  └─ 被测系统未提供万能码 → 进入 Level 2

Level 2: ddddocr 离线 OCR 识别
  └─ 截取验证码图片 → ddddocr 识别 → 填入
  └─ 识别失败 → 刷新验证码 → 重试（最多 3 轮）
```

**为什么大多数情况不需要走到 Level 2**：
- State 复用意味着首次登录成功后，后续几天的自动化测试都不需要再过验证码
- 只有 state 过期且没有万能验证码时，才触发 ddddocr

**验证码环境配置**：
```json
{
  "type": "ai_login",
  "description": "打开登录页，输入用户名密码，识别验证码，点击登录",
  "captcha_config": {
    "enabled": true,
    "mode": "bypass",
    "bypass_value": "0000",
    "captcha_element_hint": "验证码图片"
  },
  "save_state": true
}
```

`captcha_config.mode` 支持两种：
- `"bypass"` — 使用万能验证码 `bypass_value` 直接填入（推荐）
- `"ocr"` — 使用 ddddocr 识别验证码图片

**ddddocr 识别流程**：
```
1. AI 通过快照发现验证码输入框和验证码图片元素
2. playwright-cli screenshot <captcha_img_ref> → 截取验证码图片
3. ddddocr.classification(image) → 识别文本
4. playwright-cli fill <input_ref> "识别结果"
5. 如果登录失败（验证码错误）→ 刷新验证码 → 重新识别（最多 3 轮）
```

**ddddocr 适用范围与局限**：
| 验证码类型 | 识别准确率 | 适用性 |
|-----------|-----------|--------|
| 纯数字（4-6位） | 90%+ | 非常适合 |
| 数字+字母混合 | 85-95% | 适合 |
| 轻度扭曲+干扰线 | 70-85% | 基本适用 |
| 重度扭曲+复杂干扰 | 50-70% | 建议用万能码绕过 |
| 滑块/拼图验证码 | 不适用 | 建议测试环境关闭 |
| reCAPTCHA/hCaptcha | 不适用 | 建议测试环境关闭 |

#### 2.3.4 前端交互设计

环境配置页面采用**表单向导**风格：

```
┌─────────────────────────────────────────────┐
│ 新建测试环境                                  │
│                                             │
│ ① 基本信息                    ✅ 已完成     │
│ ② 目标地址与浏览器              ✅ 已完成     │
│ ③ 前置步骤（登录等）            👈 当前步骤   │
│ ④ 验证配置                     ⬜ 待完成     │
│                                             │
│ ┌─────────────────────────────────────────┐ │
│ │ 选择前置步骤类型:                         │ │
│ │                                         │ │
│ │ ● State 注入  — 复用已保存的登录态（推荐） │ │
│ │ ○ AI 智能登录  — 用自然语言描述登录流程    │ │
│ │ ○ CLI 步骤模板 — 精确指定每步操作          │ │
│ │ ○ Cookie 注入  — 直接注入 Cookie          │ │
│ │                                         │ │
│ │ ☐ 登录页有验证码                          │ │
│ │   验证码处理: ○ 万能码 [0000  ] ● OCR识别  │ │
│ │                                         │ │
│ │ 💡 推荐流程：先用"AI 智能登录"完成首次    │ │
│ │    登录，系统会自动保存登录态，后续         │ │
│ │    切换为"State 注入"秒级恢复登录。       │ │
│ │                                         │ │
│ │ [测试前置步骤] ← 点击验证是否成功          │ │
│ └─────────────────────────────────────────┘ │
│                                             │
│                        [上一步] [下一步]      │
└─────────────────────────────────────────────┘
```

### 2.4 测试物料管理（v3.0 新增章节）

UI 自动化测试的核心痛点之一：**用例文本只描述"做什么"，但具体用什么数据？**——登录用什么账号、下单买哪个商品、上传哪个测试文件。本节定义"测试物料"（Test Data）体系，让 AI 在执行用例时能"随手拿到"对应数据。

#### 2.4.1 物料的层级与作用域

```
执行级（一次性）  →  用例级（默认绑定）  →  环境级（dev/test 差异）  →  项目级（团队通用）  →  个人级（仅自己）
   高优先                                                                                          低优先
```

合并规则：高优先级覆盖低优先级，同 key 取高的。**用户在执行触发弹窗里临时改的值最终生效**。

| 层级 | 谁能创建 | 典型用法 |
|---|---|---|
| 项目级（默认） | 项目成员（含 tester） | "测试账号"、"通用收货地址"、"默认商品 SKU" |
| 环境级 | 项目管理员 | dev 环境用 dev 账号、test 环境用 test 账号 |
| 用例级 | 用例编辑者 | 给特定用例绑定"高级会员账号"、"已有未支付订单" |
| 执行级 | 触发执行者 | 本次只想试个新手机号、只这次换收货地址 |
| 个人级 | 用户本人 | 自己的开发调试账号，不污染团队配置 |

#### 2.4.2 物料的形态（决定数据库与 AI 交互方式）

| 类型 | 例子 | 存储 | AI 访问方式 |
|---|---|---|---|
| `string` | username = "admin" | 明文 text | system prompt 注入 + 模板变量替换 |
| `secret` | password = "p@ssw0rd" | **Fernet 加密** | 仅 `platform_get_secret(key)` tool 获取，不进 reasoning/log |
| `multiline` | 留言文本、JSON payload | 明文 text | system prompt 注入 + 模板变量替换 |
| `file` | 测试上传图片/PDF | 路径，文件存 `uploads/test-data/<set>/<file>` | `platform_get_file(key)` tool 返回本地路径供 `set_input_files` 用 |
| `random` | 每次执行新生成（手机号/邮箱/UUID） | 仅存模板 `phone:CN`、`email:gmail.com` 等 | 执行开始时实例化为 string，注入清单 |
| `dataset` | 参数化数据组（5 件商品的购物车） | JSONB 数组 | `platform_iter_dataset(key)` tool 迭代 |

#### 2.4.3 AI 如何使用物料（三层注入策略）

**Layer 1 — 模板变量预替换**

如果用例步骤文本里写了 `{{key}}`，ExecutionEngine 在喂给 StepRunner **之前**先做字符串替换。这是**最确定性**的方式，不依赖 AI 推断。

```
用例步骤："在用户名输入框输入 {{username}}，在密码框输入 {{password_marker}}"
                                                          ↑
                              敏感物料替换为占位符 <secret:password>，AI 看到要调 platform_get_secret 取值

预处理后："在用户名输入框输入 admin，在密码框输入 <secret:password>"
```

**Layer 2 — 物料清单注入 system prompt**

StepRunner 的 system prompt 里追加"本次可用物料清单"，让 AI 在用例步骤没有显式 `{{key}}` 时也能自然地用上数据：

```
## 可用测试物料
本次执行可使用以下物料（在用例步骤中遇到对应场景时引用）：

| key             | 类型      | 描述                       | 当前值（敏感物料显示占位）       |
|-----------------|-----------|----------------------------|---------------------------------|
| username        | string    | 测试登录用户名              | admin                           |
| password        | secret    | 测试登录密码                | ●●● 调 platform_get_secret 取值 |
| product_id      | string    | 默认测试商品 SKU            | SKU-1001                        |
| address         | multiline | 测试收货地址                | 北京市朝阳区...                  |
| avatar          | file      | 测试上传的头像图片          | 调 platform_get_file 取本地路径 |
| phone_random    | random    | 随机生成的中国大陆手机号     | 13800001234（本次实例）          |
| cart_items      | dataset   | 5 件商品的购物车数据         | 5 条记录，调 platform_iter_dataset |

## 物料使用规则
- 普通物料直接按值使用
- secret 物料必须通过 platform_get_secret(key) 获取，**不要在 reasoning 中明文展示**
- file 物料用 platform_get_file(key) 获取本地路径再喂给 browser_set_input_files
```

**Layer 3 — Platform tools 按需获取**

```python
# 注册到 agent_tools.TOOL_REGISTRY 的内置 platform tools
PLATFORM_DATA_TOOLS = {
    "platform_get_test_data":  _get_test_data,    # 通用获取（非敏感）
    "platform_get_secret":     _get_secret,       # 敏感物料专用，结果不入 reasoning log
    "platform_get_file":       _get_file_path,    # 文件物料 → 本地路径
    "platform_iter_dataset":   _iter_dataset,     # 数据集迭代（参数化测试）
}
```

**为什么这样分三层**：
- 严谨用户写 `{{username}}` 模板 → Layer 1 100% 确定性
- 普通用户写自然语言 → Layer 2 让 AI 自由选择
- 敏感/文件/数据集 → Layer 3 通过工具隔离，避免敏感数据进 LLM context 或日志

#### 2.4.4 物料 UI 入口

**入口一：项目设置页** — 全局物料管理
```
项目设置 → 测试物料
┌──────────────────────────────────────────────────────────┐
│ 物料集（按分类）              [+ 新建物料集] [⬆ 导入CSV/JSON]│
├──────────────────────────────────────────────────────────┤
│ 📁 登录账号 (项目级 · 默认)                              │
│    • username (string) = admin                          │
│    • password (secret) = ●●●●●●●         [👁️ 查看]       │
│    • mfa_code (random:digits:6)                         │
│ 📁 订单数据 (用例级)                                     │
│    • product_id (string) = SKU-1001                    │
│    • address (multiline)                                │
│ 📁 上传测试图 (项目级)                                   │
│    • avatar (file) → uploads/test-data/...              │
│ 📁 个人调试账号 (个人级 · 仅你可见)                      │
│    • dev_username = wxh_test                            │
└──────────────────────────────────────────────────────────┘
```

**入口二：用例详情** — 给单条用例绑定默认物料集（可选）
**入口三：环境配置** — 给环境绑定默认物料集（如 dev 环境默认用 "测试账号-dev"）
**入口四：执行触发弹窗** — 见 §2.5.1

### 2.5 用例执行设计

#### 2.5.1 执行入口与配置弹窗（v3.0 升级）

两个入口，覆盖不同使用习惯：

**入口一：用例列表页** — 在一期的用例管理表格上增加操作
```
☑ [高] 验证用户注册 - 正常流程          [详情] [编辑]
☑ [高] 验证用户注册 - 用户名重复        [详情] [编辑]
☐ [中] 验证邮箱格式校验                 [详情] [编辑]

已选 2 条  [批量执行 UI 测试]
```

点击"批量执行 UI 测试"打开**执行配置弹窗**（v3.0.1 单弹窗折叠式设计，90% 场景一秒确认）：

```
┌──────────────────────────────────────────────────┐
│ 执行 UI 自动化测试                                 │
├──────────────────────────────────────────────────┤
│ 3 条用例 [▾]  环境 [开发环境 ▼]  state ✓  ¥0.5K   │
│                                                  │
│ ▸ 测试物料（已自动加载 5 项 · 1 项缺料）  [调整]  │
│ ▸ 高级（前置 / Token / LLM / 模式）       [默认]  │
│                                                  │
│ ⚠️ 用例步骤含未提供物料：captcha (1 处)            │
│    AI 将自动生成测试数据；如数据不准确，          │
│    会在用例报告中标记，**不会阻断后续用例**。     │
│                                                  │
│                       [取消]  [▶ 立即执行]        │
└──────────────────────────────────────────────────┘
```

**默认状态**：弹窗打开后只露顶部一行（用例数 / 环境 / state / 预算）+ 两个折叠区 + 缺料提示 + 执行按钮。**单按钮可一键跑**。

**「测试物料」展开后**（在同一弹窗内 accordion 展开，无新弹窗）：
```
┌──────────────────────────────────────────────────┐
│ ...上面同上...                                    │
│                                                  │
│ ▾ 测试物料（已自动加载 5 项 · 1 项缺料）          │
│   ✓ 登录账号 (项目默认)         [取消加载]        │
│      username = admin        [✏️]                │
│      password = ●●●          [✏️]                │
│   ✓ 订单数据 (用例默认)         [取消加载]        │
│      product_id = SKU-1001   [✏️]                │
│   ⚠️ 缺料：captcha → AI 自造 / [手动填值] / [+集] │
│                                                  │
│   💡 推荐物料集：☐ 邮箱集  ☐ 验证码集            │
│   [+ 加载更多物料集]   [💾 改动另存为新集]        │
│                                                  │
│ ▸ 高级 ...                                       │
│ [取消]  [▶ 立即执行]                             │
└──────────────────────────────────────────────────┘
```

**「高级」展开后**：
```
   ▾ 高级
     前置步骤  [使用环境默认 ▼]   [⚙️ 临时调整]
     Token 预算 [50000]   LLM [DeepSeek ▼]   模式 [normal ▼]
     ☐ 保存本次配置为该用例组合的默认（下次直接跑）
```

**关键交互原则（v3.0.1 优化）**：
1. **单弹窗、零必填**：除了"环境"是默认填好的，其余全部可选；用户不调任何东西也能直接点"立即执行"
2. **物料缺失不阻断**：AI 会用 `platform_synthesize_data` 工具自造数据继续执行；自造的数据会在用例报告中清晰标注（详见 §3.6.8）
3. **执行结果分级**：每条用例返回 `data_confidence ∈ {reliable, synthesized, data_failure}`，**数据问题导致的失败不计入业务缺陷**（详见 §3.6.9）
4. **失败不阻断后续**：单条用例 data_failure → 标记后**继续跑下一条**（不让一个错数据拖垮整批回归）
5. **物料按优先级自动加载**（项目级 → 环境级 → 用例级 → 个人级），用户看到的是合并后的结果
6. **快速沉淀**：临时改动可"另存为新物料集"
7. **历史快捷**：弹窗右上角默认显示最近一次该用例集的执行配置（一键复用）

**入口二：AI 对话** — 自然语言触发
```
用户: "用开发环境执行注册相关的所有用例"
AI:   找到 3 条注册相关用例，使用「开发环境」+ 默认物料（5 项）。
      检测到 1 项缺料 (captcha)，将自动生成数据。
      [▶ 立即执行] [⚙️ 调整配置]
```

对话触发逻辑与弹窗一致：缺料不阻断、AI 兜底自造、报告里标注。

#### 2.5.2 AI + Playwright MCP 执行引擎核心流程（基于一期 agent_tools 复用）

```
用例（自然语言步骤）             一期 agent tool-calling 循环 + MCP 工具
┌──────────────────┐
│ 步骤1: 打开注册页 │ ─→ tool_call: browser_navigate(url="https://xxx/register")
│                  │ ←─ tool_result: { snapshot: <accessibility-tree>, refs: ["e1","e2"...] }
│                  │
│ 步骤2: 在用户名  │ ─→ tool_call: browser_type(ref="e12", text="testuser")
│ 框输入 testuser  │ ←─ tool_result: { snapshot: <updated-tree> }
│                  │
│ 步骤3: 点击注册  │ ─→ tool_call: browser_click(ref="e25")
│                  │ ←─ tool_result: { snapshot: <new-page-tree>, url: "..." }
│                  │
│ 预期: 显示       │ ─→ AssertionJudge: 在最新 snapshot 里搜索 "注册成功" 文本
│ "注册成功"       │     → 找到 → step.passed=true；找不到 → AI 二次查证
└──────────────────┘

每步自动: browser_screenshot(filename=<step_id>.png) → 截图存证（异步落盘，不入 LLM context）
```

**与一期 agent_tools 完全同构**：

```python
# agent_tools.py 已有
TOOLS = [{"type": "function", "function": {"name": "web_search", ...}}]
TOOL_REGISTRY = {"web_search": _execute_web_search}

# 二期增量（同一注册中心，新增 MCP 桥接）
PLAYWRIGHT_MCP_TOOLS = [
    {"type": "function", "function": {"name": "browser_navigate",   ...}},
    {"type": "function", "function": {"name": "browser_click",      ...}},
    {"type": "function", "function": {"name": "browser_type",       ...}},
    {"type": "function", "function": {"name": "browser_select",     ...}},
    {"type": "function", "function": {"name": "browser_snapshot",   ...}},
    {"type": "function", "function": {"name": "browser_screenshot", ...}},
    {"type": "function", "function": {"name": "browser_wait_for",   ...}},
    {"type": "function", "function": {"name": "browser_press_key",  ...}},
    {"type": "function", "function": {"name": "browser_hover",      ...}},
    {"type": "function", "function": {"name": "browser_evaluate",   ...}},  # 受白名单约束
]
TOOL_REGISTRY.update({name: _bridge_to_mcp(name) for name in [...]})
```

**逐步交互式执行**：

```
方案：MCP + agent tool-calling
  用例 ──拆分为步骤──→ StepRunner 跑一轮 tool-calling 循环:
    1. 把"用例步骤描述 + 当前 snapshot 摘要"塞 system prompt
    2. 模型自主选择 browser_* 工具调用（一次或多次）
    3. 每个 tool_call 走 agent_tools.run_tool → MCP server → 返回新 snapshot
    4. 模型判断本步骤完成 → 输出"完成确认" → 退出循环
    5. AssertionJudge 独立判定该步骤是否符合预期
  优势：
   - 复用一期成熟的 tool-calling 循环（含 MAX_TOOL_ITERATIONS、reasoning_content 透传）
   - AI 每步都能"看到"真实页面 accessibility tree
   - 多 tool_call 在同一步骤内自然支持（如先 wait 再 click 再截图）
```

#### 2.5.3 执行进度实时推送（SSE，复用一期 chat 同款事件协议）

**事件包格式**与一期 chat 完全一致：`data: {"type": "<kind>", ...}`，前端复用 `useSSE.ts` composable，无需新写解析逻辑。

```
data: {"type": "execution_start", "execution_id": "uuid", "total_cases": 3}

data: {"type": "case_start", "case_id": "uuid", "title": "验证用户注册", "index": 1}

data: {"type": "step_progress", "case_id": "uuid", "step": 1, "action": "打开注册页面", "status": "running"}

data: {"type": "tool_call", "case_id": "uuid", "step": 1, "name": "browser_navigate", "args": {"url": "https://xxx/register"}}

data: {"type": "tool_result", "case_id": "uuid", "step": 1, "name": "browser_navigate", "ok": true, "snapshot_summary": "用户注册表单，含用户名/邮箱/密码字段"}

data: {"type": "reasoning", "content": "快照显示 e12 是用户名输入框（role=textbox, name=用户名），下一步应该往里输入"}

data: {"type": "step_screenshot", "case_id": "uuid", "step": 1, "screenshot_url": "/media/screenshots/xxx.png"}

data: {"type": "step_complete", "case_id": "uuid", "step": 1, "status": "passed", "duration_ms": 2300, "tokens_used": 1830}

data: {"type": "case_complete", "case_id": "uuid", "status": "passed", "duration_ms": 12300}

data: {"type": "execution_complete", "execution_id": "uuid", "passed": 2, "failed": 1, "total": 3, "duration_ms": 45000, "video_url": "/media/videos/xxx.webm", "tokens_total": 28100}

data: {"type": "done"}
```

**与 chat 复用的事件**：
- `reasoning` — 推理过程（一期已有透传通道）
- `info` — 状态提示（如"State 已加载"、"OCR 识别中"）
- `error` — 错误信息
- `done` — 流结束

**断线续接**：完全复用一期 `ChatStreamHub` 实现（详见 §3.5）——刷新页面、切走再切回都能从最新事件断点继续。

### 2.6 结果展示设计

#### 2.6.1 执行历史列表

```
┌─────────────────────────────────────────────────────────────────┐
│ UI 自动化执行历史                                    [筛选▼]     │
├──────┬───────────┬──────┬──────┬──────┬────────┬──────────────┤
│ 批次  │ 环境      │ 通过  │ 失败  │ 总数  │ 耗时    │ 时间         │
├──────┼───────────┼──────┼──────┼──────┼────────┼──────────────┤
│ #12  │ 开发环境   │ 8    │ 2    │ 10   │ 2m30s  │ 5分钟前       │
│ #11  │ 测试环境   │ 5    │ 0    │ 5    │ 1m12s  │ 昨天 16:30   │
│ #10  │ 开发环境   │ 3    │ 1    │ 4    │ 58s    │ 昨天 10:15   │
└──────┴───────────┴──────┴──────┴──────┴────────┴──────────────┘
```

#### 2.6.2 执行详情页

```
┌─────────────────────────────────────────────────────────────────┐
│ 执行批次 #12                                    开发环境         │
│ 2026-04-30 14:30:00  耗时 2m30s     [🎬 观看完整视频回放]       │
│                                                                │
│ ┌──────────────────────────────────────────────┐               │
│ │ 通过率  ████████████████░░░░  80%  (8/10)    │               │
│ └──────────────────────────────────────────────┘               │
│                                                                │
│ ┌─ ✅ 验证用户注册 - 正常流程  (12.3s) ────────────────────────┐ │
│ │                                                             │ │
│ │  步骤 1: 打开注册页面                                        │ │
│ │    AI: "快照中发现表单，goto 注册页"                          │ │
│ │    CLI: playwright-cli goto https://dev.xxx/register         │ │
│ │    ✅ 2.1s  [截图] [快照]                                    │ │
│ │                                                             │ │
│ │  步骤 2: 输入用户名 testuser                                 │ │
│ │    AI: "e12 是 username 输入框 (role=textbox, name=用户名)"  │ │
│ │    CLI: playwright-cli fill e12 "testuser"                  │ │
│ │    ✅ 0.8s  [截图] [快照]                                    │ │
│ │                                                             │ │
│ │  步骤 3: 点击注册按钮                                        │ │
│ │    AI: "e25 是注册按钮 (role=button, name=注册)"             │ │
│ │    CLI: playwright-cli click e25                            │ │
│ │    ✅ 3.2s  [截图] [快照]                                    │ │
│ │                                                             │ │
│ │  步骤 4: 验证显示"注册成功"                                  │ │
│ │    AI: "快照中找到 '注册成功' 文本，断言通过"                  │ │
│ │    ✅ 0.5s  [截图]                                          │ │
│ │                                                             │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                │
│ ┌─ ❌ 验证密码修改 - 正常流程  (18.7s) ────────────────────────┐ │
│ │                                                             │ │
│ │  步骤 1: 进入个人设置页面          ✅ 3.1s  [截图] [快照]    │ │
│ │  步骤 2: 点击修改密码              ✅ 1.2s  [截图] [快照]    │ │
│ │  步骤 3: 输入新密码                ❌ 失败                   │ │
│ │                                                             │ │
│ │  AI 分析: "快照中无 '新密码' 相关输入框，页面可能未完全加载    │ │
│ │          或功能入口已变更"                                    │ │
│ │  重试 1: playwright-cli snapshot → 仍无目标元素              │ │
│ │  重试 2: playwright-cli reload → snapshot → 仍无目标元素     │ │
│ │  结论: 页面结构变更，标记为环境问题                            │ │
│ │                                                             │ │
│ │  [查看失败截图]  [查看快照]  [重新执行此用例]                  │ │
│ └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

#### 2.6.3 视频回放

每次执行自动通过 `playwright-cli video-start` / `video-stop` 录制操作过程视频。

- 视频嵌入在执行详情页顶部
- 支持按用例跳转（通过 `video-chapter` 在用例切换时打标记）
- 比逐张截图更直观地展示执行全过程

#### 2.6.4 截图与快照查看器

点击截图后弹出**轮播查看器**；点击快照后展示 accessibility tree（YAML 格式），帮助理解 AI 为什么选择了某个元素。

---

## 三、技术架构

### 3.1 技术依赖

| 组件 | 选型 | 理由 |
|------|------|------|
| 浏览器操控（AI 端） | **Playwright MCP** (`@playwright/mcp`) | 微软官方维护，标准 MCP 协议，天然适配一期 agent tool-calling |
| 浏览器操控（管家活儿） | **Playwright Python SDK** (`playwright`) | 处理 storage_state、视频、trace、cookie 等 MCP 暂不直接覆盖的能力 |
| MCP 桥接 | **mcp-python**（官方 SDK）| 把 MCP server 的 tools 暴露给一期 `agent_tools.TOOL_REGISTRY` |
| 任务调度（默认） | **ChatStreamHub 同款 in-process 模式** | 复用一期成熟的"后台 asyncio task + SSE 重连"，零新依赖 |
| 任务队列（可选） | **Redis + ARQ**（Phase 11 增强） | 当需要跨进程恢复 / 多 worker 弹性扩容时启用，feature flag 控制 |
| 实时推送 | **SSE** | 复用一期 chat 的事件协议（`data: {"type": ...}`） |
| 验证码识别 | **ddddocr** | 离线 OCR，免费，支持数字/字母图形验证码 |
| 文件存储 | 本地磁盘 + `/uploads/ui-runs/` | 截图/视频/快照/state 存储，挂载持久卷 |

**新增依赖**（对比一期）：
- `playwright`（Python SDK）+ `mcp`（MCP Python client）+ `ddddocr`
- worker 容器额外安装 Node.js + `npx @playwright/mcp` + Chromium 浏览器

**移除的依赖**（对比 v2 旧方案）：
- ~~`@playwright/cli`~~ — 不存在，已替换为 `@playwright/mcp`
- ~~自建 CLI Wrapper + JSON 命令解析~~ — MCP 协议天然处理
- ~~`RestrictedPython`~~ — MCP 工具白名单天然安全
- ~~`Pillow`~~ — Playwright 原生处理截图

### 3.2 架构图

```
┌──────────────────────────────────────────────────────────────┐
│                         前端                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ 环境配置  │  │ 用例选择  │  │ 执行监控  │  │ 结果/视频    │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘ │
└───────┼──────────────┼─────────────┼───────────────┼─────────┘
        │              │           SSE              REST
┌───────┼──────────────┼─────────────┼───────────────┼─────────┐
│       ▼              ▼             │               ▼  后端    │
│  ┌─────────────────────────────┐   │   ┌──────────────────┐  │
│  │      API 路由层              │   │   │ 静态文件服务      │  │
│  └──────────────┬──────────────┘   │   │ (截图/视频)      │  │
│                 ▼                   │   └──────────────────┘  │
│  ┌──────────────────────────────┐  │                          │
│  │   ExecutionEngine（编排）     │──┤     ┌──────────────────┐│
│  │  - TestDataResolver 构建      │  │ ←──→│ ExecutionStreamHub││
│  │  - 前置步骤执行 + 缺料检测   │  │     │ (复刻 ChatHub)   ││
│  │  - 用例调度                  │  │     └──────────────────┘│
│  │  - 中断/重跑                 │  │                          │
│  └──────────────┬───────────────┘  │     ┌──────────────────┐│
│                 │                   │     │ TestDataResolver ││
│                 ▼                   │     │ - 五级合并        ││
│  ┌──────────────────────────────┐  │     │ - 模板替换        ││
│  │   StepRunner（每步一个循环）  │←─┤     │ - 清单生成        ││
│  │  - 复用一期 agent tool-calling│  │     │ - secret/file 注册││
│  │  - 把 MCP tools 当 OpenAI tool│  │     │   platform_*  tool││
│  │  - 接收 data_manifest 注入   │  │     └──────────────────┘│
│  │  - 多 tool_call 同步在一步内 │  │                          │
│  └──────────────┬───────────────┘  │     ┌──────────────────┐│
│                 │                   │     │ ARQ + Redis      ││
│                 │                   │     │ (Phase 11 可选)  ││
│                 │                   │     └──────────────────┘│
│                 ▼                                              │
│  ┌──────────────────────────────┐                             │
│  │   AssertionJudge（断言判定）  │                             │
│  │  - step 退出循环后独立判定    │                             │
│  │  - snapshot 文本搜索 + AI 兜底│                             │
│  └──────────────┬───────────────┘                             │
│                 ▼                                              │
│  ┌──────────────────────────────┐                             │
│  │  agent_tools.TOOL_REGISTRY    │                             │
│  │  - web_search (一期已有)       │                             │
│  │  - browser_navigate / click /  │                             │
│  │    type / snapshot / screenshot│                             │
│  │  - 受 SecurityGuard 校验      │                             │
│  └──────────────┬───────────────┘                             │
│                 ▼                                              │
│  ┌──────────────────────────────┐  ┌──────────────────────┐  │
│  │  Playwright MCP server       │←→│ Playwright SDK Bridge │  │
│  │  (@playwright/mcp，stdio)    │  │ - storage_state      │  │
│  │  → AI 看得见的浏览器交互       │  │ - tracing / video    │  │
│  └──────────────┬───────────────┘  │ - cookies            │  │
│                 │ CDP                │  └──────────┬──────────┘  │
│                 └────共享同一 ──────────────────────┘             │
│                       Browser Context                          │
│                                                                │
│  ┌──────────────────────────────┐                             │
│  │  ddddocr（验证码 OCR）        │                             │
│  └──────────────────────────────┘                             │
└───────────────────────────────────────────────────────────────┘
```

### 3.3 AI + MCP 执行引擎设计（强调与一期 agent_tools 复用）

这是二期最核心的技术点。**核心原则：不重新实现 tool-calling 循环，直接复用一期 `agent_tools.py`**。

#### 3.3.1 三层职责分离

| 层 | 文件 | 职责 | 不做什么 |
|---|---|---|---|
| **ExecutionEngine** | `execution_engine.py` | 任务调度（前置步骤 → 用例循环 → 收尾），管理 StreamHub 事件、中断信号 | 不直接调 LLM、不操作浏览器 |
| **StepRunner** | `step_runner.py` | 单步骤执行单元，跑一轮 agent tool-calling 循环（复用一期 `_handle_chat_stream` 同款骨架），允许同一步骤多次 tool_call | 不判定整体通过/失败 |
| **AssertionJudge** | `assertion_judge.py` | StepRunner 退出后独立判定该步骤的断言（snapshot 文本搜索为主，AI 模糊判断兜底） | 不发起 tool_call、不重试 |

#### 3.3.2 执行策略

```
ExecutionEngine.run(execution_id):
  1. 构造 BrowserBundle = (MCP server 进程, Playwright SDK BrowserContext)
  2. SDK 起 video + tracing
  3. 执行前置步骤（state_inject / ai_login / scripted_steps / cookie_inject）
  4. for testcase in execution.testcases:
       hub.publish(case_start)
       for step in testcase.steps:
           hub.publish(step_progress)
           # 内部跑 agent_tools tool-calling 循环（最多 N 次 tool_call/步）
           result = await StepRunner.run_one(
               step_description=step.action,
               browser=BrowserBundle,
               token_budget=remaining_budget,
           )
           verdict = await AssertionJudge.judge(
               expected=step.expected_result,
               final_snapshot=result.last_snapshot,
           )
           hub.publish(step_complete, verdict)
           if verdict == FAILED and not testcase.continue_on_failure:
               break
       hub.publish(case_complete)
  5. SDK 停 video + tracing → 落盘
  6. 关闭 BrowserBundle
  7. hub.publish(execution_complete) → mark_done
```

#### 3.3.3 StepRunner 与一期 agent tool-calling 的复用关系

```python
# 一期 chat_service._handle_chat_stream 已实现的循环结构：
async def _handle_chat_stream(...):
    for iteration in range(MAX_TOOL_ITERATIONS):
        async for chunk in stream_chat(messages, tools=TOOLS, ...):
            # 收集 delta / reasoning / tool_calls
            ...
        if finish_reason != "tool_calls":
            break
        # append assistant tool_calls + execute tools + append tool results
        for tc in pending_tool_calls:
            result = await run_tool(tc.name, tc.arguments)
            messages.append({"role": "tool", "content": result, ...})

# 二期 step_runner.run_one 直接复用同款骨架：
async def run_one(step_description, browser, token_budget) -> StepRunResult:
    messages = [
        {"role": "system", "content": _build_step_system_prompt(browser)},
        {"role": "user",   "content": step_description},
    ]
    snapshots = []
    tool_calls_made = []
    for iteration in range(MAX_STEP_TOOL_ITERATIONS):  # default 5
        async for chunk in stream_chat(messages, tools=PLAYWRIGHT_MCP_TOOLS, ...):
            # 同样的 tool_call 收集逻辑
            ...
        if finish_reason != "tool_calls":
            break
        for tc in pending_tool_calls:
            # SecurityGuard 校验 url 域名 / ref 合法性 / token 预算
            SecurityGuard.check(tc, browser, token_budget)
            result = await run_tool(tc.name, tc.arguments)  # → MCP server
            messages.append({"role": "tool", ...})
            snapshots.append(result.snapshot)
            tool_calls_made.append(tc)
    return StepRunResult(
        last_snapshot=snapshots[-1] if snapshots else None,
        tool_calls=tool_calls_made,
        reasoning=full_reasoning,
        tokens_used=usage_total,
    )
```

**核心 Prompt（极简）**：模型不需要被告知"输出 JSON"，因为它直接通过 OpenAI tool-calling 协议调 tool。Prompt 只需要给出"上下文 + 元素定位策略"。

```python
STEP_SYSTEM_PROMPT = """你是 UI 自动化测试执行专家，通过 Playwright MCP 工具操控浏览器。

## 浏览器当前状态
- URL: {current_url}
- 页面标题: {page_title}
- Accessibility 快照（裁剪后）:
{snapshot_summary}

## 元素定位优先级
1. 用快照中的 ref（如 e15）— 最准确
2. 用 role + accessible name 组合（如 role=button name="登录"）
3. 用 placeholder / 可见文本辅助
4. 最后才考虑 CSS 选择器

## 行为约束
- 一步操作完成即停止 tool 调用
- 每轮 tool_call 不超过 3 次
- 不要 navigate 到 base_url 之外的域名（被 SecurityGuard 拦截）
- 完成后用一句简短自然语言确认操作结果
"""

ASSERTION_JUDGE_PROMPT = """判定 UI 测试步骤是否符合预期。

步骤描述：{step_description}
预期结果：{expected_result}

执行后页面 accessibility 快照:
{snapshot}

请输出严格 JSON：{"passed": bool, "reason": str, "evidence": str}
"""
```

#### 3.3.4 安全模型（白名单 + 域名校验 + 预算守卫）

MCP 协议天然就是白名单——只暴露注册过的 tool。在此之上叠加三层校验：

```python
class SecurityGuard:
    """MCP tool 调用前的统一安全闸门"""

    ALLOWED_TOOLS = {
        "browser_navigate", "browser_click", "browser_type", "browser_select",
        "browser_check", "browser_press_key", "browser_hover",
        "browser_snapshot", "browser_screenshot", "browser_wait_for",
        # 注意：browser_evaluate 默认禁用，需在环境配置中显式开启
    }

    @classmethod
    def check(cls, tool_call, browser, budget):
        # 1) 白名单
        if tool_call.name not in cls.ALLOWED_TOOLS:
            raise SecurityError(f"工具 {tool_call.name} 不在白名单")

        # 2) URL 域名校验（防 AI 跳到攻击者站点）
        if tool_call.name == "browser_navigate":
            url = tool_call.args.get("url", "")
            allowed_hosts = browser.environment.allowed_hosts  # 含 base_url 域名
            if not _host_in_allowlist(url, allowed_hosts):
                raise SecurityError(f"URL 域名 {url} 不在允许列表")

        # 3) Token 预算
        if budget.consumed >= budget.limit:
            raise SecurityError(f"已超过 token 预算 {budget.limit}")
```

**对比 v2 旧方案的安全模型升级**：
- ❌ 黑名单（`BLOCKED_COMMANDS = {"eval", "run-code"}`）→ ✅ 白名单（`ALLOWED_TOOLS`）
- ❌ 仅靠"AI 不会输出 eval"→ ✅ 协议层强制（MCP 不暴露未注册的 tool）
- ✅ 新增：URL 域名白名单（默认 environment.base_url 同域 + 同源子路径）
- ✅ 新增：Token 预算守卫，超额 raise 中断

### 3.4 Snapshot 裁剪与 Token 预算治理（新增章节）

UI 测试的核心成本风险：每步往 LLM 灌完整 accessibility snapshot，长页面可达 5–10K tokens，5 用例 × 8 步 × 5K = 200K tokens/次执行。必须治理。

**裁剪策略（自动应用）**：

| 策略 | 实现 | 收益 |
|---|---|---|
| 主区裁剪 | 默认只取 `<main>` / `[role=main]` 子树，缺失则取 body 但去掉 header/footer/nav | snapshot 体积减半 |
| 字符上限 | 单次 snapshot 注入上限 `MAX_SNAPSHOT_CHARS = 3000`，超额按"广度优先 + 焦点优先"截断 | 防长页面爆炸 |
| Diff 增量 | 第二步及以后只喂"上次快照 → 本次快照"的差异块（含焦点元素 + 周围 3 层节点） | 减少 60%+ 重复 token |
| Ref 缓存 | StepRunner 内部维护 `ref → role/name` 映射，模型可用 ref 索引而无需重复看完整 tree | 后续步骤 prompt 更短 |

**预算守卫**：

```python
@dataclass
class TokenBudget:
    limit: int = 50_000              # 默认单次执行 5 万 tokens
    consumed: int = 0                # 累加每个 tool 循环的 usage_total
    warned_at: int | None = None     # 触发 80% 预警

# environment 表新增 token_budget 字段，可按环境配置覆盖
```

超 80% 触发 SSE `info` 事件提醒；超 100% 中止执行并标记 `status="aborted_budget"`。

**模型选型策略**（推荐而非强制）：
- **决策步骤**：使用便宜的长 context 模型（GLM-4-flash / Gemini Flash / DeepSeek-Chat），$/M tokens 低
- **断言判定**：可选用强模型（GPT-4o-mini / Claude Haiku），仅每个步骤末尾调用 1 次

执行触发时允许用户选 LLM 配置：默认 = 项目默认配置；高级用户可独立设"决策模型"和"断言模型"。

### 3.5 ExecutionStreamHub 设计（直接复刻 ChatStreamHub）

一期 `chat_service.CHAT_STREAM_HUB` 已经验证了"in-process 后台任务 + SSE 重连"模式可用。二期直接复刻：

```python
# 一期已有
class _ChatStream: ...        # chunks 缓冲 + condvar 通知
class _ChatStreamHub: ...     # msg_id → _ChatStream

# 二期同构
class _ExecutionStream: ...   # 与 _ChatStream 完全一致
class _ExecutionStreamHub:    # execution_id → _ExecutionStream
    def register(self, execution_id): ...
    def get(self, execution_id): ...
    # 30 分钟过期 evict 同款逻辑

EXECUTION_STREAM_HUB = _ExecutionStreamHub()

# 触发执行
async def start_execution(execution_id, ...):
    await EXECUTION_STREAM_HUB.register(execution_id)
    asyncio.create_task(ExecutionEngine.run(execution_id))
    return {"execution_id": str(execution_id)}

# SSE 订阅（同款实现）
async def subscribe_execution_stream(execution_id, user):
    stream = EXECUTION_STREAM_HUB.get(execution_id)
    if stream is None:  # 任务结束 → DB 回放
        execution = await db.get(UIExecution, execution_id)
        for case in execution.case_results:
            yield _sse({"type": "case_complete", ...})
        yield _sse({"type": "execution_complete", ...})
        yield _sse_done()
        return
    async for event_name, event_data in stream.subscribe():
        yield _sse(event_data)
```

**Phase 11 引入 ARQ 后的演进**：
- ARQ worker 同样产生事件，写到 Redis Pub/Sub
- backend 进程的 `_ExecutionStreamHub` 升级为 Redis Pub/Sub 订阅者
- 现有 SSE 端点零改动
- feature flag `USE_TASK_QUEUE=false` 时退化到 in-process 模式

### 3.6 TestDataResolver 设计（v3.0 新增章节，对应 §2.4）

物料体系的运行时核心。负责"加载 → 合并 → 注入 → 实例化 → 快照"五件事。

#### 3.6.1 工作流程

```
ExecutionEngine.run(execution_id):
  ...
  # 在前置步骤之前调用
  resolver = await TestDataResolver.build(
      execution=execution,
      manual_overrides=execution.config.test_data_overrides,  # 弹窗里临时填的
      loaded_set_ids=execution.config.test_data_set_ids,      # 弹窗里勾选的物料集
  )
  # resolver 持有合并后的 final_data + secrets + files + datasets
  await execution.snapshot_test_data(resolver.serialize_for_audit())  # 持久化快照

  # 跑前置步骤时已经能用 resolver
  await run_precondition(bundle, template, resolver=resolver)

  for testcase in execution.testcases:
      # 用例可能有自己的覆盖（用例级物料）
      case_resolver = resolver.with_case_overrides(testcase.id)
      ...
      for step in testcase.steps:
          # Layer 1：模板变量预替换
          rendered_action = case_resolver.render_template(step.action)
          rendered_expected = case_resolver.render_template(step.expected_result)
          # Layer 2：把"可用物料清单 markdown"传给 StepRunner，拼到 system prompt
          data_manifest = case_resolver.render_manifest_markdown()

          result = await StepRunner.run_one(
              step_description=rendered_action,
              expected=rendered_expected,
              data_manifest=data_manifest,    # ← 新增
              data_resolver=case_resolver,    # ← 新增（提供 platform_get_* tools 的取值能力）
              browser=BrowserBundle,
              budget=token_budget,
          )
```

#### 3.6.2 合并优先级（代码语义）

```python
class TestDataResolver:
    """五级合并 + 三层注入"""

    @classmethod
    async def build(cls, execution, manual_overrides, loaded_set_ids):
        merged: dict[str, TestDataItem] = {}

        # 由低到高合并（高优先级覆盖低）
        for source in [
            await load_personal_sets(execution.triggered_by),   # 1) 个人级
            await load_project_sets(execution.project_id),      # 2) 项目级 + 默认集
            await load_environment_sets(execution.environment_id),  # 3) 环境级
            await load_loaded_sets(loaded_set_ids),             # 4) 弹窗加载的（含用例级推荐）
        ]:
            for item in source:
                merged[item.key] = item     # 覆盖

        # 5) 执行级（弹窗里临时编辑的值，最高优先级）
        for key, value in manual_overrides.items():
            if key in merged:
                merged[key] = merged[key].overridden_with(value)
            else:
                merged[key] = TestDataItem.adhoc(key, value)

        # 实例化 random 类型（每次执行新值）
        for k, v in merged.items():
            if v.value_type == "random":
                v.realize()  # phone:CN → 13800001234 这种

        return cls(merged, execution=execution)
```

#### 3.6.3 模板变量替换

```python
import re

_VAR_PATTERN = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")

def render_template(self, text: str) -> str:
    """Layer 1: 字符串模板替换。secret 类型替换为占位符（避免明文进 system prompt）"""
    if not text:
        return text
    def replace(m):
        key = m.group(1)
        item = self.data.get(key)
        if item is None:
            return m.group(0)  # 保留原占位 → StepRunner 会看到 + 上报"缺料告警"
        if item.value_type == "secret":
            return f"<secret:{key}>"  # AI 看到后会调 platform_get_secret
        if item.value_type == "file":
            return f"<file:{key}>"    # AI 看到后会调 platform_get_file
        return str(item.value)
    return _VAR_PATTERN.sub(replace, text)
```

#### 3.6.4 物料清单 markdown 注入

```python
def render_manifest_markdown(self) -> str:
    """Layer 2: 给 StepRunner system prompt 用的物料清单"""
    if not self.data:
        return ""
    rows = []
    for key, item in sorted(self.data.items()):
        display = item.display_safe_value()  # secret 显示 "●●●"，file 显示路径名
        rows.append(f"| {key} | {item.value_type} | {item.description or '-'} | {display} |")
    return (
        "## 可用测试物料\n"
        "本次执行可使用以下物料（在用例步骤中遇到对应场景时引用）：\n\n"
        "| key | 类型 | 描述 | 当前值 |\n"
        "|-----|------|------|--------|\n"
        + "\n".join(rows)
        + "\n\n## 物料使用规则\n"
          "- 普通物料按值使用\n"
          "- secret 物料必须通过 `platform_get_secret(key)` tool 获取，**不要在 reasoning 中明文展示**\n"
          "- file 物料用 `platform_get_file(key)` 获取本地路径再喂给 `browser_set_input_files`\n"
          "- dataset 物料用 `platform_iter_dataset(key)` 迭代访问每条记录\n"
    )
```

#### 3.6.5 Platform Tools 注册

```python
# 注册到 agent_tools.TOOL_REGISTRY，命名空间 <execution_id>:platform_*
def register_data_tools(execution_id, resolver):
    namespace = f"{execution_id}:"
    TOOL_REGISTRY[namespace + "platform_get_secret"] = _make_get_secret(resolver)
    TOOL_REGISTRY[namespace + "platform_get_file"]   = _make_get_file(resolver)
    TOOL_REGISTRY[namespace + "platform_iter_dataset"] = _make_iter_dataset(resolver)
    TOOL_REGISTRY[namespace + "platform_get_test_data"] = _make_get_test_data(resolver)
    TOOL_REGISTRY[namespace + "platform_synthesize_data"] = _make_synthesize_data(resolver)  # v3.0.1 新增

def _make_get_secret(resolver):
    async def _impl(args):
        key = args.get("key", "")
        item = resolver.data.get(key)
        if item is None or item.value_type != "secret":
            return {"error": f"secret '{key}' not found"}
        # 关键：返回值不进入 reasoning_content，只直接进 tool 流
        # SSE 推送时这条 tool_result 也对前端做脱敏（前端只展示 "<secret resolved>"）
        return {"value": item.resolve_secret()}  # 内部解密
    return _impl

def _make_get_file(resolver):
    async def _impl(args):
        key = args.get("key", "")
        item = resolver.data.get(key)
        if item is None or item.value_type != "file":
            return {"error": f"file '{key}' not found"}
        return {"path": item.file_path, "filename": item.filename, "size": item.file_size}
    return _impl
```

#### 3.6.6 缺料检测（Pre-flight check，v3.0.1 调整为非阻断告警）

ExecutionEngine 启动后第一件事，扫描所有用例的 step.action / step.expected_result，提取 `{{key}}`，与 resolver 已合并的 keys 做差集：

```python
async def preflight_data_check(execution, resolver) -> list[MissingDataAlert]:
    missing = set()
    for tc in execution.testcases:
        for step in tc.steps:
            for m in _VAR_PATTERN.finditer(step.action + " " + (step.expected_result or "")):
                key = m.group(1)
                if key not in resolver.data:
                    missing.add(key)
    return [MissingDataAlert(key=k, detected_in_steps=[...]) for k in missing]
```

**v3.0.1 关键变化**：缺料**只产生告警**，不阻断执行。资讯通过 SSE `missing_data_warning` 事件推送，前端用黄色提示条展示"AI 将自动生成"；只有用户主动在配置弹窗里勾选 ☐ 缺料严格模式 时才会阻断。

#### 3.6.7 安全与脱敏

- **存储**：`secret` 类型的 value 字段 Fernet 加密（复用一期 `app/core/crypto.py`）
- **API**：`GET /api/test-data-sets/{id}` 列表 secret 字段返回 `null`，需调 `GET /api/test-data-items/{id}/reveal`（仅 owner / admin）
- **Reasoning**：`platform_get_secret` 的 tool result 不写入 `step_results.ai_reasoning`，只写 `["<secret used: key>"]`
- **日志**：`flush_step` 把 tool_calls 落盘前对 secret 类工具结果做脱敏
- **前端 SSE**：tool_result 事件如果 name = `platform_get_secret`，前端展示"已获取敏感物料: <key>"
- **快照**：`ui_executions.test_data_snapshot` 记录本次用了哪些集 + 哪些 key（不存 secret 明文，存"已使用"标记）

#### 3.6.8 AI 自造数据（platform_synthesize_data 工具，v3.0.1 新增）

**核心设计哲学**：物料配置应该是"提质"而非"准入"。完全没配也能跑，配了就跑得更稳。

**工作机制**：StepRunner 的 system prompt 在物料清单 markdown 之后增加一段"自造规则"指引：

```
## 物料缺失时的兜底
如果用例步骤里出现 {{key}}，但你在「可用测试物料」清单里找不到对应条目，
请调用 platform_synthesize_data(key, hint, value_type) 让平台帮你生成
一个合理的临时测试值。系统会优先用启发式（如 phone/email/username 等
常见 key 直接套模板），失败时由你基于上下文推断。

调用后请把返回的 value 用于步骤，**并在 reasoning 中显式说明
"此数据为 AI 自造，准确性未保证"**，方便测试报告标记。

如果业务系统对自造数据返回不接受（如格式错、账号不存在），
请调 platform_mark_data_failure(key, reason) 上报数据问题，
后续步骤如果完全依赖此 key 你可以直接放弃当前用例，平台会跳到下一条。
```

**实现**：

```python
def _make_synthesize_data(resolver):
    """v3.0.1: 缺料兜底。两层策略：启发式 → AI 推断"""

    # 启发式 key 库（覆盖 80% 常见场景）
    HEURISTIC_RULES = {
        # 账号类
        "username":  lambda h: f"test_user_{_rand_hex(4)}",
        "user_name": lambda h: f"test_user_{_rand_hex(4)}",
        "account":   lambda h: f"test_acc_{_rand_hex(4)}",
        "nickname":  lambda h: f"测试用户{_rand_hex(2)}",
        # 联系方式
        "phone":     lambda h: f"138{_rand_digits(8)}",
        "mobile":    lambda h: f"138{_rand_digits(8)}",
        "email":     lambda h: f"test_{_rand_hex(4)}@example.com",
        # 验证码
        "captcha":   lambda h: "0000",   # 测试环境万能码；如失败 AI 会进入 OCR 流程
        "sms_code":  lambda h: "123456",
        "otp":       lambda h: "123456",
        # 业务标识
        "order_id":  lambda h: f"TEST{_now_ts()}",
        "product_id":lambda h: f"SKU-TEST-{_rand_digits(4)}",
        "address":   lambda h: "北京市朝阳区测试地址 100 号",
        # 内容类
        "comment":   lambda h: f"[自动化测试] {_rand_hex(8)}",
        "search":    lambda h: "test",
        "keyword":   lambda h: "test",
    }

    async def _impl(args):
        key   = args.get("key", "").lower().strip()
        hint  = args.get("hint", "")
        vtype = args.get("value_type", "string")

        # Layer A: 启发式精确匹配
        if key in HEURISTIC_RULES:
            value = HEURISTIC_RULES[key](hint)
            source = "heuristic_exact"
        else:
            # Layer B: 启发式模糊匹配（包含子串）
            for pattern, gen in HEURISTIC_RULES.items():
                if pattern in key:
                    value = gen(hint)
                    source = f"heuristic_fuzzy:{pattern}"
                    break
            else:
                # Layer C: 调一次小模型让 AI 推断
                value = await _llm_infer_test_value(key, hint, vtype)
                source = "ai_inferred"

        # 落到 resolver 的运行时缓存（同一执行内同一 key 第二次直接复用）
        resolver.cache_synthesized(key, value, source)
        # 记录到当前 case 的 synthesized_data 数组（写库时用）
        resolver.current_case_log_synth(key, value, source, hint=hint)

        return {
            "key": key,
            "value": value,
            "source": source,
            "warning": "AI synthesized — accuracy not guaranteed",
        }
    return _impl


def _make_mark_data_failure(resolver):
    """AI 主动上报数据问题"""
    async def _impl(args):
        key    = args.get("key", "")
        reason = args.get("reason", "")
        resolver.current_case_mark_data_failure(key, reason)
        return {"acknowledged": True, "case_will_be_marked": "data_failure"}
    return _impl
```

**两个新增 platform tool 一并注册**：

```python
TOOL_REGISTRY[namespace + "platform_synthesize_data"]   = _make_synthesize_data(resolver)
TOOL_REGISTRY[namespace + "platform_mark_data_failure"] = _make_mark_data_failure(resolver)
```

**为什么不直接在 resolver 里把缺料的 key 用启发式预填？**因为 AI 才知道当前步骤的语义上下文（比如 `email` 是注册邮箱还是收件人邮箱），交给 AI 决定是否调用、传什么 hint 更合理；同时显式调用让"自造"这个动作可观测（SSE/日志/报告里都能看到），便于审计。

#### 3.6.9 数据可信度评级（v3.0.1 新增）

执行结果按"数据来源"为每条用例打三级标签，**与"功能通过/失败"正交**：

| 评级 | 触发规则 | 报告徽章 | 业务含义 |
|---|---|---|---|
| `reliable` | 该用例所有步骤的物料 100% 来自显式配置（项目/环境/用例/个人/执行级），无 synthesize 调用 | 🟢 数据可信 | 可作为正式回归依据 |
| `synthesized` | 至少 1 次 `platform_synthesize_data` 被调用，且最终步骤通过 | 🟡 含自造数据 | 跑通了但建议下次补物料；列表里点击徽章可看哪些 key 是自造的 |
| `data_failure` | 出现至少 1 次 `platform_mark_data_failure`，或自造的数据导致后续步骤失败 | 🟠 数据导致失败 | **不计入业务缺陷**；统计报表里"被测系统通过率"会排除这类用例 |

**判定算法（在 ExecutionEngine 完成单条用例后）**：

```python
def evaluate_case_confidence(case_result) -> str:
    if case_result.synthesized_data is None or len(case_result.synthesized_data) == 0:
        return "reliable"
    if case_result.data_failures:        # 显式 mark_data_failure 或自造数据触发系统拒绝
        return "data_failure"
    return "synthesized"
```

**单条用例 data_failure 不阻断后续用例**：ExecutionEngine 的循环只 break 对应单条用例，整批继续。

**统计报表区分**：仪表盘的"通过率 / 失败率"卡片提供两个视图切换：
- **业务视图**（默认）：把 `data_failure` 用例排除分母，反映被测系统真实质量
- **执行视图**：包含全部用例，反映自动化执行覆盖率

#### 3.6.10 SSE 事件（v3.0.1 增量）

| 事件 | data 字段 | 说明 |
|---|---|---|
| `missing_data_warning` | `{keys: [...], will_synthesize: true}` | 缺料告警（黄色，**不阻断**） |
| `data_synthesized` | `{key, value_preview, source, case_id, step_id}` | AI 自造一条数据；前端在步骤详情里显示 🟡 标签 |
| `data_failure_marked` | `{key, reason, case_id, step_id}` | AI 主动上报数据问题；该用例最终评级会是 data_failure |
| `case_confidence` | `{case_id, confidence: reliable\|synthesized\|data_failure}` | 单条用例完成时的最终评级 |

---

## 四、数据库设计

### 4.1 新增表

```sql
-- ===== 测试环境配置 =====

CREATE TABLE test_environments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    base_url VARCHAR(500) NOT NULL,
    allowed_hosts JSONB DEFAULT '[]',          -- URL 域名白名单（默认含 base_url 域名）
    browser VARCHAR(20) DEFAULT 'chromium',    -- chromium, firefox, webkit
    viewport_width INTEGER DEFAULT 1920,
    viewport_height INTEGER DEFAULT 1080,
    timeout_ms INTEGER DEFAULT 30000,
    headless BOOLEAN DEFAULT TRUE,
    session_name VARCHAR(100),                 -- 浏览器 context 隔离名
    token_budget INTEGER DEFAULT 50000,        -- 单次执行 LLM token 预算
    enable_browser_evaluate BOOLEAN DEFAULT FALSE,  -- 是否允许 browser_evaluate（默认禁，需 admin 显式开）
    is_default BOOLEAN DEFAULT FALSE,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ===== 前置步骤配置 =====

CREATE TABLE precondition_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    environment_id UUID REFERENCES test_environments(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    type VARCHAR(20) NOT NULL,                 -- state_inject, ai_login, scripted_steps, cookie_inject
    config JSONB NOT NULL,
    credentials_encrypted JSONB,
    state_file_path VARCHAR(500),              -- storage_state 文件路径
    state_saved_at TIMESTAMPTZ,                -- state 最近一次保存时间（用于过期判定）
    captcha_config JSONB,                      -- 验证码配置 {enabled, mode, bypass_value, ...}
    sort_order INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ===== 执行批次 =====

CREATE TABLE ui_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    environment_id UUID REFERENCES test_environments(id),
    status VARCHAR(20) DEFAULT 'pending',      -- pending, running, completed, stopped, failed, aborted_budget
    mode VARCHAR(20) DEFAULT 'normal',         -- normal, debug（调试模式逐步暂停）
    total_cases INTEGER DEFAULT 0,
    passed_cases INTEGER DEFAULT 0,
    failed_cases INTEGER DEFAULT 0,
    skipped_cases INTEGER DEFAULT 0,
    duration_ms INTEGER,
    tokens_total INTEGER DEFAULT 0,            -- 整次执行消耗的 LLM token 总量
    video_path VARCHAR(500),                   -- 录制视频路径
    trace_path VARCHAR(500),                   -- Playwright trace 路径（可选）
    chat_message_id UUID REFERENCES chat_messages(id) ON DELETE SET NULL,  -- 若由对话触发
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    triggered_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ===== 单条用例执行结果 =====

CREATE TABLE ui_case_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id UUID REFERENCES ui_executions(id) ON DELETE CASCADE,
    testcase_id UUID REFERENCES testcases(id),
    status VARCHAR(20) DEFAULT 'pending',      -- pending, running, passed, failed, error, skipped
    error_message TEXT,
    ai_summary TEXT,                           -- AI 对执行过程的总结
    duration_ms INTEGER,
    tokens_used INTEGER DEFAULT 0,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    sort_order INTEGER DEFAULT 0
);

-- ===== 每步执行记录 =====

CREATE TABLE ui_step_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_result_id UUID REFERENCES ui_case_results(id) ON DELETE CASCADE,
    step_number INTEGER NOT NULL,
    description TEXT NOT NULL,
    expected_result TEXT,                      -- 预期结果
    -- StepRunner 的所有 tool_call 序列（含 args + result）
    tool_calls JSONB DEFAULT '[]',             -- [{name, args, ok, snapshot_summary, duration_ms}, ...]
    ai_reasoning TEXT,                         -- 模型 reasoning_content（一期已有透传通道）
    snapshot_before TEXT,                      -- 进入 StepRunner 时的 snapshot（裁剪后）
    snapshot_after TEXT,                       -- 步骤结束时的 snapshot（裁剪后）
    -- AssertionJudge 的判定结果
    assertion_passed BOOLEAN,
    assertion_reason TEXT,
    assertion_evidence TEXT,
    status VARCHAR(20) DEFAULT 'pending',      -- pending, running, passed, failed, skipped
    screenshot_path VARCHAR(500),
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    tokens_used INTEGER DEFAULT 0,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ui_step_results_case ON ui_step_results(case_result_id, step_number);
CREATE INDEX idx_ui_executions_project_status ON ui_executions(project_id, status, created_at DESC);

-- ===== 测试物料（v3.0 新增）=====

CREATE TABLE test_data_sets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    category VARCHAR(50),                      -- account / order / product / search / upload / 等业务分类
    scope VARCHAR(20) NOT NULL DEFAULT 'project',  -- project | environment | personal
    environment_id UUID REFERENCES test_environments(id) ON DELETE CASCADE,  -- scope=environment 时
    owner_id UUID REFERENCES users(id),        -- scope=personal 时
    is_default BOOLEAN DEFAULT FALSE,          -- 项目级默认（自动加载）
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_test_data_sets_project ON test_data_sets(project_id, scope);

CREATE TABLE test_data_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    set_id UUID REFERENCES test_data_sets(id) ON DELETE CASCADE,
    key VARCHAR(100) NOT NULL,                 -- 物料 key（用例步骤里 {{key}}）
    value_type VARCHAR(20) NOT NULL,           -- string | secret | multiline | file | random | dataset
    value_text TEXT,                           -- string / multiline / random 模板（如 "phone:CN"）
    value_encrypted TEXT,                      -- secret 类型，Fernet 加密的明文值
    value_json JSONB,                          -- dataset 类型的数组，或复杂结构
    file_path VARCHAR(500),                    -- file 类型，相对 uploads/ 的路径
    file_size BIGINT,
    file_mime VARCHAR(100),
    description TEXT,                          -- 物料含义说明（注入 system prompt 给 AI 看）
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(set_id, key)
);

CREATE INDEX idx_test_data_items_set ON test_data_items(set_id);

-- 用例可绑定默认物料集（用例级层）
ALTER TABLE testcases
    ADD COLUMN default_data_set_ids JSONB DEFAULT '[]';  -- [uuid, uuid, ...]，多集合并

-- 环境可绑定默认物料集（环境级层）
ALTER TABLE test_environments
    ADD COLUMN default_data_set_ids JSONB DEFAULT '[]';

-- 执行批次记录使用的物料快照（审计追溯用）
ALTER TABLE ui_executions
    ADD COLUMN test_data_snapshot JSONB;
    -- 结构：{
    --   "loaded_set_ids": [uuid, ...],
    --   "manual_overrides": {key: {"value_type": "string", "value": "..."}, ...},  -- secret 不存值
    --   "realized_random": {phone_random: "13800001234", ...},
    --   "missing_keys_skipped": ["captcha"]
    -- }

-- 单条用例执行也记录实际用了哪些 key（细化审计）+ 数据可信度（v3.0.1 新增字段）
ALTER TABLE ui_case_results
    ADD COLUMN test_data_used JSONB,
    -- 结构：[{key: "username", source: "project_default", used_in_steps: [1, 3]}, ...]
    ADD COLUMN synthesized_data JSONB DEFAULT '[]',
    -- AI 自造的数据列表（v3.0.1）
    -- 结构：[{key, value, source: "heuristic_exact"|"heuristic_fuzzy:phone"|"ai_inferred",
    --        hint, used_in_steps: [int, ...], synthesized_at: timestamp}, ...]
    ADD COLUMN data_failures JSONB DEFAULT '[]',
    -- 数据导致的失败（v3.0.1）
    -- 结构：[{key, reason, step_id, marked_at: timestamp}, ...]
    ADD COLUMN data_confidence VARCHAR(20) DEFAULT 'reliable';
    -- v3.0.1: reliable | synthesized | data_failure
    -- 与 status (passed/failed/error) 正交，用于业务质量统计

CREATE INDEX idx_ui_case_results_confidence ON ui_case_results(data_confidence);
```

### 4.2 新增权限

```python
PHASE2_PERMISSIONS = {
    "ui_automation:config",      # 配置测试环境
    "ui_automation:execute",     # 执行 UI 测试
    "ui_automation:view",        # 查看执行结果
    "ui_automation:stop",        # 停止执行中的测试
}
```

---

## 五、API 设计

```
# 测试环境
GET    /api/projects/{id}/environments              # 环境列表
POST   /api/projects/{id}/environments              # 创建环境
PATCH  /api/environments/{id}                       # 编辑环境
DELETE /api/environments/{id}                       # 删除环境
POST   /api/environments/{id}/test-precondition     # 测试前置步骤是否成功
POST   /api/environments/{id}/clear-state           # 清空登录态文件（state 过期/凭据变更）

# 前置步骤模板
GET    /api/environments/{id}/preconditions         # 前置步骤列表
POST   /api/environments/{id}/preconditions         # 创建前置步骤
PATCH  /api/preconditions/{id}                      # 编辑
DELETE /api/preconditions/{id}                      # 删除

# 执行
POST   /api/projects/{id}/ui-executions             # 创建执行（含 mode=normal|debug、token_budget 覆盖）
GET    /api/projects/{id}/ui-executions             # 执行历史列表
GET    /api/ui-executions/{id}                      # 执行详情
GET    /api/ui-executions/{id}/stream               # SSE 实时进度（同 chat 协议）
POST   /api/ui-executions/{id}/stop                 # 停止执行
POST   /api/ui-executions/{id}/continue             # 调试模式下继续下一步
POST   /api/ui-executions/{id}/retry-failed         # 重跑失败用例
POST   /api/ui-executions/{id}/replay               # 基于已存 snapshot/截图回放（无浏览器）

# 结果
GET    /api/ui-case-results/{id}                    # 单用例执行详情
GET    /api/ui-case-results/{id}/screenshots        # 用例截图列表
GET    /api/ui-case-results/{id}/snapshots          # 用例快照列表
GET    /api/ui-step-results/{id}/screenshot         # 单步截图
GET    /api/ui-step-results/{id}/snapshot           # 单步 accessibility 快照
GET    /api/ui-step-results/{id}/tool-calls         # 单步 tool_call 序列详情

# 视频 / Trace
GET    /api/ui-executions/{id}/video                # 获取执行视频
GET    /api/ui-executions/{id}/trace                # 下载 Playwright trace.zip

# 统计
GET    /api/projects/{id}/ui-stats                  # 项目维度统计（通过率/耗时/token 趋势）

# 媒体清理（管理员）
POST   /api/admin/ui-media/cleanup                  # 触发清理（可指定 days）

# ===== 测试物料（v3.0 新增）=====

# 物料集
GET    /api/projects/{id}/test-data-sets            # 列表（按 scope 过滤：project/environment/personal）
POST   /api/projects/{id}/test-data-sets            # 创建
PATCH  /api/test-data-sets/{id}                     # 编辑
DELETE /api/test-data-sets/{id}                     # 删除（含其下所有 items）
POST   /api/test-data-sets/{id}/clone               # 克隆为新集
POST   /api/test-data-sets/{id}/import              # 导入 CSV/JSON 批量创建 items

# 物料条目
GET    /api/test-data-sets/{id}/items               # 列表（secret 字段值返回 null）
POST   /api/test-data-sets/{id}/items               # 创建（含文件上传 multipart）
PATCH  /api/test-data-items/{id}                    # 编辑
DELETE /api/test-data-items/{id}                    # 删除
GET    /api/test-data-items/{id}/reveal             # 查看 secret 明文（仅 owner / admin，记录审计日志）
GET    /api/test-data-items/{id}/file               # 下载 file 类型的物料文件

# 解析与预览
POST   /api/projects/{id}/test-data/preview-merge   # 预览合并结果（参数：set_ids[]、environment_id、testcase_ids[]）
POST   /api/projects/{id}/test-data/missing-check   # 扫描用例步骤，返回缺失 key 列表
POST   /api/projects/{id}/test-data/save-as-set     # 把"配置弹窗的临时改动"另存为新物料集

# 推荐
GET    /api/projects/{id}/test-data/recommend       # 根据 testcase_ids/tags 推荐应加载的物料集
```

---

## 六、关键技术挑战与解决方案

| 挑战 | v3.0 MCP 方案 |
|------|---------------|
| AI 选择器不准 | accessibility ref 精确定位 + 每步 snapshot 实时反馈 |
| 页面加载时间不确定 | MCP `browser_wait_for(text/selector/state)` + AI 自主判断何时 snapshot |
| 动态内容/SPA 路由 | snapshot diff 对比 + `browser_wait_for(state="networkidle")` |
| 执行安全性 | MCP 工具白名单 + URL 域名校验 + Token 预算守卫 + 凭据加密 |
| 错误恢复 | StepRunner 内 tool-calling 循环天然支持单步多次重试，且模型能看新 snapshot |
| 执行过程留痕 | 截图 + 快照 diff + reasoning_content + 完整 tool_call JSON + 视频 + trace |
| 前置登录复用 | Python SDK `storage_state save/load`，state 文件按环境 + 凭据 hash 命名 |
| 登录验证码 | 分层策略：State 复用 > 万能码 > ddddocr OCR；OCR 失败自动刷新重试 |
| Token 成本失控 | snapshot 主区裁剪 + 字符上限 + diff 增量 + ref 缓存；超 80% 预警，超 100% 中止 |
| 长任务跨进程恢复 | Phase 11 引入 ARQ 后启用；当前 in-process + ChatStreamHub 模式已能扛住 SSE 重连 |
| 调试与排查 | 调试模式逐步暂停 + 历史 snapshot/截图回放（无需重新跑浏览器） |
| 与一期对话对接 | `intent_handler.IntentType.RUN_UI_TEST` + `_handle_ui_test_intent` 走 chat SSE 通道，结果写入 `ChatMessage.meta_data` 渲染卡片 |
| **用例文本只描述"做什么"，AI 不知道用什么数据** | **§2.4 + §3.6 测试物料体系**：五级层级（执行级 > 用例级 > 环境级 > 项目级 > 个人级）+ 六种类型 + 三层注入（模板预替换 / 清单注入 / platform tool）；敏感字段加密、不进 LLM context、不入 reasoning log |
| **配置项太多导致触发执行繁琐** | **§2.5.1 单弹窗折叠式**：默认只露顶部一行 + 折叠区，零必填一键执行；物料/前置/高级全部 accordion 展开，不开新弹窗 |
| **物料没填齐就跑不动** | **§3.6.8 platform_synthesize_data 兜底**：缺料**非阻断**，AI 调工具让平台启发式生成（覆盖 username/phone/email/captcha 等 17+ 常见 key）+ AI 推断兜底；自造的数据完整记录 |
| **真假失败混在一起影响质量度量** | **§3.6.9 数据可信度三级评级**：reliable / synthesized / data_failure；统计报表"业务通过率"自动排除 data_failure（数据问题不算被测系统 bug）；仪表盘提供"业务视图 / 执行视图"切换 |

---

## 七、二期新增部署组件

### 7.1 默认模式（in-process，零新容器）

二期默认不引入新容器：MCP server 与浏览器都跑在 backend 容器内（subprocess + 持久 BrowserContext）。

**Backend Dockerfile 增量**：

```dockerfile
# 在现有 backend/Dockerfile 末尾追加
RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs npm \
        # Chromium 运行时依赖（与 playwright install --with-deps 等价）
        libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libxcomposite1 \
        libxdamage1 libxrandr2 libgbm1 libxss1 libasound2 \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @playwright/mcp@latest

# 安装 Playwright Python SDK 自带的 Chromium 二进制
RUN uv run playwright install chromium
```

`pyproject.toml` 新增依赖：`playwright`、`mcp`、`ddddocr`。

### 7.2 Phase 11 增强模式（按需引入 ARQ + Worker）

```yaml
# docker-compose.yml 新增（feature flag 控制）
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  worker:
    build:
      context: ./backend
      dockerfile: Dockerfile
    command: uv run arq app.worker.WorkerSettings
    environment:
      <<: *backend-env
      USE_TASK_QUEUE: "true"
      REDIS_URL: redis://redis:6379/0
    depends_on:
      - db
      - redis
    volumes:
      - backend_uploads:/app/uploads
```

Backend 进程的 `ExecutionStreamHub` 升级为 Redis Pub/Sub 订阅者；`USE_TASK_QUEUE=false` 时退回 in-process 模式。**没有破坏性变更**，单容器/多容器部署都能跑。

---

*文档版本：v3.0.1 — 测试物料体系 + 单弹窗 + AI 自造数据兜底 + 数据可信度评级*
*最后更新：2026-05-02*
*主要变更：见文档顶部 v3.0 关键调整列表（含第 8 条"测试物料体系"、第 9 条"v3.0.1 易用性优化"）*
