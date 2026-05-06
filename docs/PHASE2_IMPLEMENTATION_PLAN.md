# 第二期实现计划 - UI 自动化测试模块（v3.1 任务粒度优化版）

> **v3.1 关键调整**（基于 v3.0.1 的可执行性优化）：
> - 把 v3.0.1 的 17 个 task 重新切分为 **27 个细粒度 task（含 1 个可选）**
> - 每个 task 严格控制在 **≤ 6 个新文件 + ≤ 500 行预估代码 + ≤ 4 个验证场景**，单次会话可安全完成
> - 跨前后端的工作一律拆开
> - 同一目录的强相关文件可以合并；可选/增强类功能放到尾部
> - 每个 task 都自带"会话启动模板"，方便后续一个一个执行

> **保留 v3.0.1 全部架构决策**（详见 `PHASE2_DESIGN.md`）：
> - 浏览器后端：Playwright MCP + Playwright Python SDK 双栈
> - AI 决策：完全复用一期 `agent_tools.TOOL_REGISTRY` 的 OpenAI tool-calling 循环
> - 任务调度：默认 ExecutionStreamHub（in-process）；ARQ 推迟为 Phase 11 可选
> - 测试物料体系：五级层级 + 六种类型 + 三层注入；secret 加密、不进 LLM context
> - 缺料兜底：黄条警告非阻断 + AI `platform_synthesize_data` 启发式/AI 兜底
> - 数据可信度三级评级 reliable / synthesized / data_failure；业务通过率自动排除数据问题

## 前置条件

- 一期全部功能已完成并可用（特别是 `agent_tools.py` / `chat_service.py` 已经稳定）
- 用例管理模块已有数据（手动或 AI 生成的用例）
- Node.js ≥ 18 已安装（Playwright MCP 依赖）

## 依赖变更

```bash
# 后端 Python 新增
./run.sh add-backend playwright       # Playwright Python SDK（"管家活儿"）
./run.sh add-backend mcp              # MCP Python client（连接 @playwright/mcp） ✅ Task 7.2 已加入
./run.sh add-backend ddddocr          # 验证码 OCR 离线识别

# Node.js 端（在 backend 容器/worker 容器中全局安装）
npm install -g @playwright/mcp@latest

# 浏览器二进制
uv run playwright install chromium
```

Docker 变更：
- `docker-compose.dev.yml` **不变**（默认 in-process 模式不需要 Redis）
- `docker-compose.yml` **不变**（Phase 11 可选追加 Redis + Worker）
- `backend/Dockerfile` 新增 Node.js + `@playwright/mcp` + Chromium 依赖（在 Task 11.3 完成）

> 📋 **部署集成清单**：所有部署相关变更（Dockerfile / docker-compose / .env / nginx 增量、镜像体积估算、常见坑）全部归档在 [`PHASE2_DEPLOYMENT_NOTES.md`](./PHASE2_DEPLOYMENT_NOTES.md)。**每个引入新依赖 / 新挂载点 / 新外部进程的 task 完成后，必须同步更新该文档的 §2 表格**；Task 11.3 直接照该文档 §3 施工。

## Task 规模约定

每个 task 标注规模等级：
- 🟢 **S**（小）：≤ 3 文件 / ≤ 250 行 / ≤ 2 验证场景，会话宽裕
- 🟡 **M**（中）：≤ 6 文件 / ≤ 500 行 / ≤ 4 验证场景，会话舒适
- 🟠 **L**（大）：接近上限，但仍能在单会话完成；后续如发现超时再拆

---

## Phase 7：基础设施准备（3 个 task）

### Task 7.1 — ExecutionStreamHub（复刻 ChatStreamHub） 🟢 S

**目标**：让 UI 测试任务能像一期 chat 一样跑后台 + 支持 SSE 重连，不引入新容器。

**前置依赖**：无（直接基于一期 `chat_service.py`）

**产出文件**：
- `app/modules/ui_automation/__init__.py`
- `app/modules/ui_automation/stream_hub.py`
  - `_ExecutionStream` / `_ExecutionStreamHub`（结构与 `chat_service._ChatStream / _ChatStreamHub` 完全一致，可直接拷贝改名）
  - `EXECUTION_STREAM_HUB` 单例
  - 30 分钟过期 evict
- `app/modules/ui_automation/sse.py`
  - `_sse / _sse_event(type, payload)` 与 chat 同款的 SSE 编码工具
- `app/modules/ui_automation/persistence.py`
  - `flush_step / flush_case / flush_execution` 占位（实际写库逻辑在 Task 9.5 实现，先建函数签名）

**验证方式**：
- 写一个 fake task 周期性 publish `step_progress` 事件
- curl 订阅 `/api/ui-executions/{id}/stream`（暂用 mock router）→ 立刻看到事件流
- 中途 Ctrl+C 再 curl 一次 → 仍能从头看完整事件

**会话启动模板**：
```
请执行 Task 7.1 - ExecutionStreamHub。
项目：/Users/wxh/Downloads/长轻Job/AITestPlatform
目标：复刻一期 chat_service._ChatStreamHub 模式，建 app/modules/ui_automation/{stream_hub,sse,persistence}.py
关键复用：app/modules/llm/chat_service.py 中的 _ChatStream / _ChatStreamHub
```

---

### Task 7.2 — Playwright MCP Bridge（MCP client + tool 注册） 🟡 M

**目标**：起 MCP server 子进程，把 MCP tools 桥接为一期 OpenAI tool-calling 可用的注册项。

**前置依赖**：Task 7.1 完成

**产出文件**：
- `app/modules/ui_automation/mcp_bridge.py`
  - `class MCPBridge`：管理 MCP stdio session（启动/握手/关闭/重启）
  - `discover_mcp_tools(bridge) -> list[OpenAIToolSpec]`：把 MCP server 自描述的 tool schema 转为 OpenAI function 格式
  - `make_tool_executor(bridge, tool_name) -> Callable`：返回 async 函数，调 MCP `call_tool` 并返回 JSON
  - `register_into_agent_tools(bridge, execution_id)`：注册到一期 `TOOL_REGISTRY`，命名空间 `<execution_id>:browser_*`
  - `unregister(execution_id)`：清理
- `tests/ui_automation/test_mcp_bridge.py`（mock MCP server）
  - 验证 tool discovery
  - 验证命名空间隔离

**关键技术点**：
- MCP 启动命令：`npx @playwright/mcp --browser-cdp-endpoint=...`（CDP endpoint 在 Task 7.3 由 SDK 提供）
- 子进程异常 → 自动重试 3 次 + 健康探测

**验证方式**：
- 单元测试：mock 一个 MCP server，verify discovery 返回正确 tool 列表
- 注册到 TOOL_REGISTRY 后能通过 `<execution_id>:browser_navigate` 调用
- unregister 后 TOOL_REGISTRY 清空对应命名空间

**会话启动模板**：
```
请执行 Task 7.2 - Playwright MCP Bridge。
依赖 Task 7.1 已完成。目标：起 MCP server 子进程，把 MCP tools 注册为一期 agent_tools.TOOL_REGISTRY 可用项
关键复用：app/modules/llm/agent_tools.py 中的 TOOL_REGISTRY
新文件：app/modules/ui_automation/mcp_bridge.py + tests/ui_automation/test_mcp_bridge.py
注意：浏览器实例由 Task 7.3 BrowserBundle 管理，本 task 只用 mock CDP endpoint 验证
```

---

### Task 7.3 — BrowserBundle + SecurityGuard 🟡 M

**目标**：用 Playwright Python SDK 启动 Chromium 并暴露 CDP endpoint 给 MCP；实现安全白名单。

**前置依赖**：Task 7.2 完成

**产出文件**：
- `app/modules/ui_automation/browser_bundle.py`
  - `class BrowserBundle`：含 `pw_browser` + `pw_context` + `mcp_bridge`
  - `async def open(environment) -> BrowserBundle`
    - SDK 启动 Chromium → 获取 CDP endpoint
    - 启动 `MCPBridge`，传入 CDP endpoint
    - 完成 stdio handshake
  - `async def close(self)`：双向关闭
  - 失败回退：MCP 启动失败时退化为"纯 SDK 模式"标记
- `app/modules/ui_automation/security.py`
  - `class SecurityGuard`
    - `ALLOWED_TOOLS` 白名单
    - `check(tool_name, args, environment, budget)`
    - URL 域名校验：`browser_navigate` 的 url 必须命中 `environment.allowed_hosts`
    - Token 预算：超 80% 预警事件、超 100% 抛 `BudgetExceededError`
- `app/modules/ui_automation/snapshot_clipper.py`
  - 主区裁剪 + 字符上限 + diff 增量 + ref 缓存

**验证方式**：
- 起 BrowserBundle → `bridge.discover_tools()` 返回真实 MCP tool 列表
- 调 `<execution_id>:browser_navigate` 跳到 `environment.base_url` → 截图非空
- mock 一个跨域 navigate → SecurityGuard 拦截抛 SecurityError
- token 预算耗尽 → 抛 BudgetExceededError

**会话启动模板**：
```
请执行 Task 7.3 - BrowserBundle + SecurityGuard。
依赖 Task 7.2 已完成。目标：用 Playwright Python SDK 启浏览器，配合 MCPBridge 共享 BrowserContext；加白名单守卫
新文件：app/modules/ui_automation/{browser_bundle,security,snapshot_clipper}.py
依赖确认：playwright + @playwright/mcp 已装好（依赖变更章节）
```

---

## Phase 8：环境配置 + 物料管理（8 个 task）

### Task 8.1 — 环境配置后端（模型 + CRUD + State 治理） 🟡 M ✅ 已完成

**目标**：测试环境 CRUD + 前置步骤模板 CRUD + State 文件治理。

**前置依赖**：无（独立后端模块）

**产出文件**：
- `app/modules/ui_automation/models.py`
  - `TestEnvironment`（含 `allowed_hosts`、`token_budget`、`enable_browser_evaluate`、`session_name`、`default_data_set_ids` JSONB）
  - `PreconditionTemplate`（type 改为 `state_inject / ai_login / scripted_steps / cookie_inject`，含 `state_saved_at`）
- `app/modules/ui_automation/schemas.py`
- `app/modules/ui_automation/service.py`
  - 环境与前置步骤 CRUD
  - 创建环境时自动从 base_url 提取域名写入 allowed_hosts
- `app/modules/ui_automation/router.py`
  - 环境 + 前置步骤的 CRUD 端点
  - `POST /environments/{id}/clear-state`
- `app/modules/ui_automation/state_manager.py`
  - `state_path_for / mark_state_stale / load_state_or_none`
- `alembic/versions/xxx_ui_automation_env.py` 迁移
- 凭据加密复用一期 `app/core/crypto.py`

**验证方式**：
- API 创建环境（含 allowed_hosts、session_name、token_budget 字段）
- API 添加前置步骤（4 种类型各测一个）
- 凭据在数据库中为加密状态
- `POST /environments/{id}/clear-state` 删除 state 文件并清空 `state_saved_at`

**会话启动模板**：
```
请执行 Task 8.1 - 环境配置后端。
目标：测试环境 + 前置步骤模板的 CRUD + State 文件治理
新文件：app/modules/ui_automation/{models,schemas,service,router,state_manager}.py + alembic 迁移
关键复用：app/core/crypto.py（凭据加密）
注意：本 task 只做"配置面"，前置步骤的"执行面"在 Task 8.2 实现
```

---

### Task 8.2 — 前置步骤执行器 + State 自动复用 🟡 M ✅ 已完成

**目标**：实现 4 种前置步骤类型的实际执行，并接入 BrowserBundle；State 自动复用。

**前置依赖**：Task 7.3（BrowserBundle）+ Task 8.1（环境模型）

**产出文件**：
- `app/modules/ui_automation/precondition_executor.py`
  - `async def run_precondition(bundle, template) -> PreconditionResult`
  - 4 种类型分支：
    - `state_inject` → SDK `new_context(storage_state=...)`
    - `ai_login` → 跑一遍 StepRunner（**Task 8.2 先用 stub，等 9.4 才接真 StepRunner**）
    - `scripted_steps` → 按序直接调 `tool_executor(name, args)`
    - `cookie_inject` → SDK `context.add_cookies([...])`
  - **State 过期自动检测**：state_inject 后调 `browser_snapshot`，简单文本检查（如包含 "登录" 关键字判定过期）；过期则降级到 ai_login
- `app/modules/ui_automation/router.py` 增量
  - `POST /environments/{id}/test-precondition`
- 单元测试：`tests/ui_automation/test_precondition.py`（mock BrowserBundle）

**验证方式**：
- test-precondition API → 返回成功 + 截图
- 4 种前置步骤类型均能正确执行（ai_login 用 stub）
- state 文件不存在 → 自动跳过 state_inject
- 模拟 state 过期 → 自动重新走 ai_login 并覆盖 state

**会话启动模板**：
```
请执行 Task 8.2 - 前置步骤执行器 + State 自动复用。
依赖 Task 7.3 + 8.1 已完成。目标：4 种前置步骤类型的执行 + State 自动复用 + 过期检测
新文件：app/modules/ui_automation/precondition_executor.py + 单测
注意：ai_login 这一分支需要 StepRunner（Task 9.4），本 task 用 stub 占位；Task 9.4 完成后回填
```

---

### Task 8.3 — 验证码识别（CaptchaSolver + ai_login 集成） 🟢 S ✅ 已完成

**目标**：验证码 OCR 识别，接入 ai_login 流程。

**前置依赖**：Task 7.3（BrowserBundle）+ Task 8.2（前置步骤执行器）

**产出文件**：
- `app/modules/ui_automation/captcha_solver.py`
  - `class CaptchaSolver`
  - `async def solve(bundle, captcha_config) -> str | None`
  - 模式分支：bypass / ocr
  - OCR 模式：调 MCP `browser_screenshot(ref=验证码图)` → 字节流 → ddddocr
  - 失败重试：刷新验证码图 → 重试，最多 3 轮
- 注册 `platform_solve_captcha` tool 到 `agent_tools.TOOL_REGISTRY`（命名空间 `<execution_id>:platform_*`）
- 单元测试：`tests/ui_automation/test_captcha.py`（用真实验证码图样本）

**验证方式**：
- captcha_config mode=bypass → 直接返回万能码
- captcha_config mode=ocr + 简单数字验证码图 → 正确识别
- OCR 失败 3 次 → 返回 None + 上报日志

**会话启动模板**：
```
请执行 Task 8.3 - 验证码识别 CaptchaSolver。
依赖 Task 7.3 + 8.2 已完成。目标：bypass / ocr 两种模式 + 失败重试
新文件：app/modules/ui_automation/captcha_solver.py + 单测
依赖确认：ddddocr 已装好
```

---

### Task 8.4 — 环境配置前端 🟡 M ✅ 已完成

**目标**：环境列表页 + 创建向导 + 前置步骤编辑器 + 验证测试。

**前置依赖**：Task 8.1（后端 API）

**产出文件**：
- `src/views/ui-automation/EnvironmentList.vue`
- `src/components/ui-automation/EnvironmentWizard.vue`（4 步向导）
- `src/components/ui-automation/PreconditionEditor.vue`（4 种类型）
- `src/services/uiAutomation.ts`（环境 + 前置步骤相关 API）
- `src/router/index.ts` 增量：`/projects/:id/ui-environments`

**验证方式**：
- 向导走通：创建环境 → 配置前置步骤 → 一键 test-precondition 成功
- 编辑已有环境 → 信息正确回显
- State 文件状态展示准确（✓ / ✗ / 已过期 + 上次保存时间）
- "清空登录态"按钮工作正常

**会话启动模板**：
```
请执行 Task 8.4 - 环境配置前端。
依赖 Task 8.1 后端 API 已完成。目标：环境列表 + 创建向导 + 前置步骤编辑
新文件：src/views/ui-automation/EnvironmentList.vue + src/components/ui-automation/{EnvironmentWizard,PreconditionEditor}.vue + src/services/uiAutomation.ts
关键复用：一期 services/request.ts、components/common/PageContainer.vue
```

---

### Task 8.5 — 测试物料模型 + 基础 CRUD（含加密 + 文件） 🟡 M ✅ 已完成

**目标**：物料集 + 物料条目模型 + 基础 CRUD + secret 加密 + 文件上传。

**前置依赖**：无（独立后端模块）

**产出文件**：
- `app/modules/test_data/__init__.py`
- `app/modules/test_data/models.py`
  - `TestDataSet`（scope: project|environment|personal、is_default、environment_id、owner_id）
  - `TestDataItem`（value_type、value_text、value_encrypted、value_json、file_path、description）
- `app/modules/test_data/schemas.py`
  - 关键：list 接口 secret value 始终返回 null
- `app/modules/test_data/service.py`
  - 物料集 + 条目 CRUD
  - secret 字段 Fernet 加密（复用 `app/core/crypto.py`）
  - 文件上传到 `uploads/test-data/<project_id>/<set_id>/`
- `app/modules/test_data/router.py`
  - `GET/POST/PATCH/DELETE /api/projects/{id}/test-data-sets`
  - `GET/POST/PATCH/DELETE /api/test-data-sets/{id}/items`
  - `GET /api/test-data-items/{id}/reveal`（owner / admin only + 审计日志）
  - `GET /api/test-data-items/{id}/file`（下载 file 物料）
- `app/modules/test_data/random_generator.py`
  - `generate(template) -> str`（phone:CN / email / uuid / digits:N / hex:N）
- `alembic/versions/xxx_test_data.py` 迁移
  - 建 `test_data_sets` / `test_data_items` 表
  - 给 `testcases` 加 `default_data_set_ids`、`test_environments` 加 `default_data_set_ids`
  - 给 `ui_executions`（如有）/`ui_case_results`（如有）加快照字段（v3.0.1 字段，详见设计 §4.1）

**权限增量**（写到 `app/modules/auth/permissions.py`）：
```python
"test_data:view", "test_data:edit", "test_data:reveal", "test_data:import"
```

**验证方式**：
- 创建物料集 + 加 6 种类型各一条 item → 列表只看到非 secret 明文
- secret 调 reveal API → 仅 owner / admin 200，其他 403
- 文件物料上传 50MB → 成功；上传 .exe → 拒绝
- random 类型在 generator 单测里能生成

**会话启动模板**：
```
请执行 Task 8.5 - 测试物料模型 + 基础 CRUD。
目标：物料集 + 物料条目模型 + CRUD + secret 加密 + 文件上传
新文件：app/modules/test_data/{models,schemas,service,router,random_generator}.py + alembic 迁移
关键复用：app/core/crypto.py（Fernet）
注意：导入 / 推荐 / 缺料检测 等增强 API 在 Task 8.6；前端在 Task 8.7
```

---

### Task 8.6 — 物料增强 API（CSV/JSON 导入 + 推荐 + save-as-set） 🟢 S ✅ 已完成

**目标**：物料的批量导入 + 推荐 + 临时改动沉淀 API。

**前置依赖**：Task 8.5（基础 CRUD）

**产出文件**：
- `app/modules/test_data/service.py` 增量方法：
  - `import_csv(set_id, csv_file)`
  - `import_json(set_id, payload)`
  - `clone_set(set_id, new_name)`
  - `recommend_sets(project_id, testcase_ids) -> list[TestDataSet]`（基于历史使用日志，初版可按"项目级 default + 用例 default + 最常用 top3"）
  - `save_overrides_as_set(project_id, name, overrides)`
- `app/modules/test_data/router.py` 增量端点：
  - `POST /api/test-data-sets/{id}/import`
  - `POST /api/test-data-sets/{id}/clone`
  - `GET /api/projects/{id}/test-data/recommend?testcase_ids=...`
  - `POST /api/projects/{id}/test-data/save-as-set`

**验证方式**：
- 上传 CSV（10 条）→ 批量创建 items
- 上传非法 CSV（缺 key 列）→ 返回详细错误
- recommend API → 返回非空列表
- save-as-set → 落地为新物料集，能在 list 里看到

**会话启动模板**：
```
请执行 Task 8.6 - 物料增强 API。
依赖 Task 8.5 已完成。目标：CSV/JSON 导入 + 克隆 + 推荐 + save-as-set
增量文件：app/modules/test_data/{service,router}.py
新单测：tests/test_data/test_import.py
```

---

<!-- assistant: {"status":"COMPLETED","final_message":"物料管理前端完整落地：主页 (scope tab + 卡片网格)、物料集编辑页 (元数据 + 条目表格 + 弹窗)、6 种 value_type 字段组件 (string / multiline / secret / file / random / dataset)，以及 services/testData.ts API 客户端。菜单、面包屑、路由、权限常量都已同步。端到端在真实后端上验证了创建/读/reveal/下载/删除 全流程；typecheck + build 全绿。"} -->
### Task 8.7 — 物料管理前端：列表 + 编辑器 + 6 种字段 🟡 M ✅ 已完成

**目标**：物料管理主页 + 物料集编辑页 + 6 种类型字段组件。

**前置依赖**：Task 8.5（基础 CRUD API）

**产出文件**：
- `src/views/test-data/TestDataView.vue` — 主页（左侧 scope tab + 右侧物料集卡片）
- `src/views/test-data/DataSetEditor.vue` — 编辑页（元数据 + 条目表格）
- `src/components/test-data/SecretField.vue` — 敏感字段遮蔽 + reveal
- `src/components/test-data/FileField.vue` — 文件上传 / 下载 / 替换
- `src/components/test-data/RandomField.vue` — 随机模板编辑 + 试运行预览
- `src/components/test-data/DatasetField.vue` — JSON 编辑（先用 `<el-input type="textarea">` + JSON.parse 校验，避免引入 Monaco）
- `src/services/testData.ts` — API 调用
- `src/router/index.ts` 增量：`/projects/:id/test-data` + `/projects/:id/test-data/sets/:setId`

**验证方式**：
- 创建物料集 + 加 6 种类型物料 → 列表正常显示
- secret 字段默认 ●●●，点 👁️ → 弹出明文（权限校验）
- file 字段上传 → 文件可下载
- random 字段试运行 → 预览生成结果

**会话启动模板**：
```
请执行 Task 8.7 - 物料管理前端：列表 + 编辑器 + 6 种字段组件。
依赖 Task 8.5 后端 API 已完成。目标：物料管理主页 + 编辑页 + 6 种类型字段
新文件：src/views/test-data/{TestDataView,DataSetEditor}.vue + src/components/test-data/{SecretField,FileField,RandomField,DatasetField}.vue + src/services/testData.ts
注意：CSV 导入 + 用例/环境绑定增量在 Task 8.8
```

---

<!-- assistant: {"status":"COMPLETED","final_message":"物料前端增强完整落地：批量导入弹窗（CSV multipart + JSON 粘贴，支持 skip_existing / upsert，部分失败按行展示错误）；克隆对话框（继承 scope 或转为个人私有）；SetSelector 按 scope 分组的多选（推荐置顶、拖拽排序、带 reason tag）；用例详情页 + 环境向导 均接入 SetSelector。后端小量增量：testcases schemas / service 开放 default_data_set_ids 读写。典型端到端：CSV 3 条 ✅ / JSON upsert 1创建+1更新 ✅ / clone 4 条含 secret 文件 ✅ / recommend 正确区分项目默认/用例默认/个人/常用 ✅ / case 绑定与回显 ✅ / 环境绑定与回显 ✅；typecheck + build 全绿。"} -->
### Task 8.8 — 物料前端增强：CSV 导入 + 用例/环境绑定 🟢 S ✅ 已完成

**目标**：批量导入弹窗 + 用例详情页/环境编辑页绑定默认物料集。

**前置依赖**：Task 8.6（增强 API）+ Task 8.7（前端基础）

**产出文件**：
- `src/components/test-data/ImportDialog.vue` — CSV/JSON 批量导入
- `src/components/test-data/SetSelector.vue` — 物料集多选下拉（项目+环境+个人 grouped）
- `src/views/testcases/TestcaseDetail.vue` 增量：增加"默认物料集"多选
- `src/components/ui-automation/EnvironmentWizard.vue` 增量：第 2 步加"默认物料集"多选
- 一些 `services/testData.ts` 增量调用

**验证方式**：
- 上传 CSV → 物料批量入库
- 用例详情绑定默认物料集 → 保存后回显正确
- 环境配置绑定默认物料集 → 保存后回显正确

**会话启动模板**：
```
请执行 Task 8.8 - 物料前端增强：CSV 导入 + 绑定。
依赖 Task 8.6 + 8.7 已完成。目标：批量导入弹窗 + 用例/环境页绑定增量
新文件：src/components/test-data/{ImportDialog,SetSelector}.vue
增量文件：src/views/testcases/TestcaseDetail.vue + src/components/ui-automation/EnvironmentWizard.vue
```

---

## Phase 9：AI + MCP 执行引擎（7 个 task）

### Task 9.1 — TestDataResolver 核心（合并 + 模板 + 清单 + 缓存） 🟡 M

**目标**：物料运行时核心：五级合并 + 模板替换 + 清单 markdown + 自造缓存。

**前置依赖**：Task 8.5（物料模型）

**产出文件**：
- `app/modules/ui_automation/test_data_resolver.py`
  - `class TestDataItem`（运行时表示）
    - `display_safe_value()` / `resolve_secret()` / `realize()`
  - `class TestDataResolver`（详见设计文档 §3.6）
    - `await build(execution, manual_overrides, loaded_set_ids)` — 五级合并
    - `with_case_overrides(testcase_id)` — 派生子 resolver
    - `render_template(text)` — Layer 1 模板替换（{{key}} → value；secret → `<secret:key>`；file → `<file:key>`）
    - `render_manifest_markdown()` — Layer 2 清单 + "缺料兜底规则"指引段
    - `serialize_for_audit()` — 持久化快照（不含 secret 明文）
    - `cache_synthesized(key, value, source)` — 缓存自造结果
    - `current_case_log_synth(key, value, source, hint)` / `current_case_mark_data_failure(key, reason)`
    - `finalize_case() -> {synthesized_data, data_failures, data_confidence}`
- `app/modules/ui_automation/confidence_evaluator.py`
  - `evaluate_case_confidence(synthesized_data, data_failures) -> str`
- 单元测试：`tests/ui_automation/test_resolver.py`
  - 五级合并优先级
  - 模板替换 / secret 占位
  - 清单生成
  - finalize_case 三种评级

**验证方式**：
- 五级合并：高优先 key 覆盖低优先（单测）
- secret 渲染为 `<secret:key>` 占位
- finalize_case 三种评级正确（reliable / synthesized / data_failure）
- 缓存命中：同 key 二次写入应保留首次值

**会话启动模板**：
```
请执行 Task 9.1 - TestDataResolver 核心。
依赖 Task 8.5 已完成。目标：物料运行时五级合并 + 模板替换 + 清单 markdown + 自造缓存 + 三级评级
新文件：app/modules/ui_automation/{test_data_resolver,confidence_evaluator}.py + tests/ui_automation/test_resolver.py
设计文档：docs/PHASE2_DESIGN.md §3.6.1-3.6.4 + §3.6.9
注意：platform tools 注册和 DataSynthesizer 在 Task 9.2；preflight 和配置 API 在 Task 9.3
```

---

### Task 9.2 — Platform Tools + DataSynthesizer（兜底 + 评级） 🟡 M

**目标**：6 个 platform tool 注册 + AI 自造数据兜底引擎。

**前置依赖**：Task 7.2（agent tool 注册基础）+ Task 9.1（resolver）

**产出文件**：
- `app/modules/ui_automation/data_synthesizer.py`
  - `class DataSynthesizer`
    - `HEURISTIC_RULES`（17+ 常见 key 模板，详见设计 §3.6.8）
    - `async def synthesize(key, hint, value_type) -> SynthesizedValue`
      - Layer A 精确匹配 / Layer B 模糊匹配 / Layer C LLM 推断
- `app/modules/ui_automation/data_platform_tools.py`
  - `register_data_tools(execution_id, resolver)` 注册 6 个 tool 到 `TOOL_REGISTRY`：
    - `platform_get_test_data` / `platform_get_secret` / `platform_get_file` / `platform_iter_dataset`
    - `platform_synthesize_data` / `platform_mark_data_failure`
  - secret 工具的 result 不进 ai_reasoning，标记 `_test_data_secret_used`
  - synthesize 工具的 result 完整入 reasoning
  - `unregister_data_tools(execution_id)`
- 单元测试：`tests/ui_automation/test_synthesizer.py` + `test_platform_tools.py`

**验证方式**：
- 启发式精确匹配：`synthesize("phone", ...)` → 返回 138 开头号码
- 启发式模糊匹配：`synthesize("user_phone", ...)` → 命中 phone 规则
- LLM 推断 fallback：`synthesize("weird_xxx", "下单时的优惠券码", "string")` → 调小模型生成
- secret tool result 不在 reasoning 里
- platform_mark_data_failure 调用后 resolver 该 case 标记为 data_failure

**会话启动模板**：
```
请执行 Task 9.2 - Platform Tools + DataSynthesizer。
依赖 Task 7.2 + 9.1 已完成。目标：6 个 platform_* tool 注册 + AI 自造数据兜底
新文件：app/modules/ui_automation/{data_synthesizer,data_platform_tools}.py + 单测
设计文档：docs/PHASE2_DESIGN.md §3.6.5 + §3.6.8
关键复用：app/modules/llm/agent_tools.py TOOL_REGISTRY、app/modules/llm/providers.py（LLM 推断走 project 默认 LLM）
```

---

### Task 9.3 — Preflight + 执行配置 API 🟢 S

**目标**：缺料检测（非阻断） + 弹窗用的 3 个配置 API。

**前置依赖**：Task 9.1（resolver）+ Task 8.6（推荐 API）

**产出文件**：
- `app/modules/ui_automation/preflight.py`
  - `async def preflight_data_check(testcases, resolver) -> list[MissingDataAlert]`
  - 扫描所有 step.action / expected_result 中的 `{{key}}` → 与 resolver merged keys 做差集
  - 返回告警列表（不抛异常）
- `app/modules/test_data/router.py` 增量：
  - `POST /api/projects/{id}/test-data/preview-merge` — 复用 resolver 合并逻辑（不依赖真实 execution，传 set_ids + overrides 即可）
  - `POST /api/projects/{id}/test-data/missing-check` — 复用 preflight，返回 `{missing_keys: [...], will_synthesize: true}`
- `app/modules/ui_automation/router.py` 增量：
  - `GET /api/projects/{id}/recent-executions/last-config?testcase_ids=...` — 用例组合的最近一次执行配置（弹窗"复用上次"用）

**验证方式**：
- preview-merge：传 2 个 set_ids + 1 个 manual override → 返回正确合并结果（secret 遮蔽）
- missing-check：传含 `{{captcha}}` 的用例 + 不含 captcha 的物料集 → 返回 `["captcha"]`
- recent-config：之前跑过的用例组合 → 返回上次配置；新组合 → 返回 null

**会话启动模板**：
```
请执行 Task 9.3 - Preflight + 执行配置 API。
依赖 Task 8.6 + 9.1 已完成。目标：缺料检测（非阻断） + 弹窗 3 个配置 API
新文件：app/modules/ui_automation/preflight.py
增量文件：app/modules/test_data/router.py + app/modules/ui_automation/router.py
```

---

### Task 9.4 — StepRunner（含 data_manifest 注入） ✅ 已完成

**实现**：
- `backend/app/modules/ui_automation/step_runner.py` — `StepRunner.run_one()` + `default_chat_round()` + `StepRunResult` / `ToolCallRecord`，复刻一期 chat_service 的 tool-calling 循环骨架
- `backend/app/modules/ui_automation/prompts/step_runner_system.py` — system / user prompt 模板，含裁剪后 snapshot + 元素定位策略 + 行为约束 + 物料清单注入
- `backend/app/modules/ui_automation/ai_login_runner.py` — `StepRunnerAILoginRunner`，回填 Task 8.2 的 `_StubAILoginRunner`，Engine 注入即可激活
- `backend/app/modules/ui_automation/security.py` — 给 SecurityGuard 添加 `platform_*` 工具白名单豁免（不影响浏览器侧白名单）
- `backend/tests/ui_automation/test_step_runner.py`（10 用例）+ `test_ai_login_runner.py`（4 用例），全部 mock LLM + mock TOOL_REGISTRY，零外部依赖

**验证覆盖**：
- 单步骤完成 1 次 tool_call → 正常收尾、tokens 累加、final_message 正确
- 跨域 navigate → SecurityGuard 拦截，记录 `blocked=True` 不抛错
- Token 预算耗尽（轮内 / 轮前）→ `error_kind="budget_exceeded"`
- `data_manifest` 注入 system prompt → 模型可见 `{{username}} → admin` 等映射
- `data_resolver` 提供时 `platform_*` 工具自动合并到 `tools=`
- secret 工具的 plaintext 不写入下一轮 messages（`redact_tool_result_for_reasoning` 兜底）
- tool_runner 抛错 → 写入 ToolCallRecord 不影响 step 收尾
- 末轮强制 `tool_choice="none"` 让模型给最终自然语言回复

**原任务定义**（设计稿，保留供 Engine 章节引用）：

**目标**：单步骤执行单元，复刻一期 tool-calling 循环；接收物料清单和 platform tools。

**前置依赖**：Task 7.3（BrowserBundle）+ Task 9.2（data tools）

**产出文件**：
- `app/modules/ui_automation/step_runner.py`
  - `class StepRunner`
  - `async def run_one(step_description, expected, bundle, budget, data_manifest="", data_resolver=None) -> StepRunResult`
    - 组装 system prompt（含裁剪后 snapshot + 元素定位策略 + data_manifest）
    - tools = MCP tools + (data_resolver 时) platform_* tools
    - `MAX_STEP_TOOL_ITERATIONS = 5` 次 tool-calling 循环
    - 每次 tool_call 前过 `SecurityGuard.check`
    - 每次 tool_call 后调 `snapshot_clipper`
    - 收集 `tool_calls / reasoning / snapshots / tokens`
    - 返回 `StepRunResult(last_snapshot, tool_calls, reasoning, tokens_used, success)`
- `app/modules/ui_automation/prompts/step_runner_system.py` — system prompt 模板
- 单元测试：`tests/ui_automation/test_step_runner.py`（mock LLM + mock MCP）
- 回填 Task 8.2 的 `precondition_executor.ai_login` 分支：替换 stub 为真实 StepRunner 调用

**验证方式**：
- mock 用例步骤 + mock 浏览器 → 完成 1-3 次 tool_call
- 步骤描述清楚 → 模型输出合理的 browser_* 调用序列
- 安全拦截：mock 跨域 navigate → 失败
- token 预算耗尽 → 抛 BudgetExceededError
- data_manifest 注入有效：用例含 `{{username}}`（已替换为 admin）→ AI 直接调 type "admin"

**会话启动模板**：
```
请执行 Task 9.4 - StepRunner。
依赖 Task 7.3 + 9.2 已完成。目标：单步骤执行单元 + tool-calling 循环 + data_manifest 注入
新文件：app/modules/ui_automation/step_runner.py + prompts/step_runner_system.py + 单测
关键复用：app/modules/llm/chat_service.py 中 _handle_chat_stream 的 tool-calling 循环骨架
回填：Task 8.2 precondition_executor.ai_login 的 stub → 真实 StepRunner.run_one 调用
```

---

### Task 9.5 — AssertionJudge + ExecutionEngine 编排 ✅ 已完成

**实现**：
- `backend/app/modules/ui_automation/models.py` — 新增 ORM：`UIExecution` / `UICaseResult` / `UIStepResult`，含 status / data_confidence / config_snapshot / test_data_snapshot 等设计字段
- `backend/alembic/versions/d3c8a2b6f410_add_ui_execution_tables.py` — 三张表迁移（含 CHECK 约束保证 status / data_confidence 枚举一致）
- `backend/app/modules/ui_automation/persistence.py` — 替换 stub 为真实实现：`init_execution_record` / `mark_execution_running` / `is_execution_stopped` / `create_case_result` / `flush_step` / `flush_case` / `flush_execution`，含 `sanitize_tool_call_for_storage` 把 secret plaintext 替换为 `"<secret used>"`
- `backend/app/modules/ui_automation/assertion_judge.py` — `AssertionJudge.judge`：no_expected → 整段命中 → 多关键词全命中 → LLM 兜底（严格 JSON）→ llm_unavailable 兜底
- `backend/app/modules/ui_automation/execution_engine.py` — 主编排：5 级物料合并 → preflight 缺料告警 → 起 BrowserBundle → 注册 MCP/platform 工具 → 跑前置 → 用例循环（每 case 重注册 platform 工具到 case_resolver）→ AssertionJudge 判定 → flush，全程通过 `EngineDeps` 暴露注入点（DB / Bundle / StepRunner / Judge / Persistence / StreamHub / preconditions runner）
- `backend/tests/conftest.py` — eager import 全部 ORM 模块，避免跨模块 relationship 字符串引用在测试期解析失败
- `backend/tests/ui_automation/test_assertion_judge.py`（10 用例）+ `test_persistence.py`（6 用例）+ `test_engine.py`（7 用例）

**验证覆盖**：
- 全用例通过 → `execution.status=completed`，`data_confidence=reliable`
- 单 case data_failure → 该 case `error` + `data_confidence=data_failure`，**整批继续**，下一条 case 正常完成
- preflight 发现缺料 + `strict_data_mode=True` → 直接 `failed`，bundle 不启动
- token 预算耗尽（StepRunner 返回 `error_kind="budget_exceeded"`）→ `aborted_budget`，剩余用例 `skipped`
- `assertion_passed=False` → 单 case `failed`，整批继续
- 用户通过 DB 标记 stopped → engine 在 case 边界 `stopped`，剩余 case `skipped`
- engine 入口异常（如 environment 加载失败）→ `failed` + `flush_execution` + 事件流 `mark_done`
- 所有 SSE 事件链：`execution_started → bundle_ready → case_started → step_complete → case_complete → execution_complete`
- secret 工具结果在 `flush_step` 写库前 plaintext 替换为 `"<secret used>"`

### Task 9.5 — AssertionJudge + ExecutionEngine 编排（设计稿，保留供后续 task 引用） 🟡 M

**目标**：完整执行流程编排 + 信心度评级 + 单 case data_failure 不阻断。

**前置依赖**：Task 8.2（前置步骤）+ Task 9.1（resolver）+ Task 9.2（data tools）+ Task 9.3（preflight）+ Task 9.4（StepRunner）

**产出文件**：
- `app/modules/ui_automation/assertion_judge.py`
  - `class AssertionJudge`
  - `async def judge(expected, snapshot, llm_config) -> AssertionVerdict`
    - 优先纯文本搜索；失败 fallback LLM 模糊判断
- `app/modules/ui_automation/execution_engine.py`
  - `class ExecutionEngine`
  - `async def run(execution_id)`
    - 加载 environment / testcases / llm_config
    - BrowserBundle.open
    - **build TestDataResolver + register data tools + preflight check**
    - SDK 起 video + tracing
    - 执行前置步骤
    - 循环 testcases：
      - case_resolver = resolver.with_case_overrides(testcase_id)
      - publish case_start
      - 循环 steps：
        - rendered_action = case_resolver.render_template(step.action)
        - StepRunner.run_one
        - AssertionJudge.judge
        - flush_step → DB
        - 若 case_resolver 标记 data_failure 且后续无法继续 → break 当前 case，**不 break 整批**
      - case_finalized = case_resolver.finalize_case() → 写 ui_case_results
      - publish case_confidence
      - publish case_complete
    - SDK 停 video + tracing → 落盘
    - publish execution_complete
    - 中断检查：定期查 execution.status == 'stopped'
- `app/modules/ui_automation/persistence.py` 实现 flush 函数（Task 7.1 留的占位）
- 数据库迁移：新增 `ui_executions` / `ui_case_results` / `ui_step_results` 表（已在设计 §4.1 定义）
- 单元测试：`tests/ui_automation/test_engine.py`（mock 全链路）

**验证方式**：
- 创建 execution → 后台任务跑 → DB 中有完整 execution → case_results → step_results 链
- 每条 case_result 含 `data_confidence` 字段
- mock 一条 case 触发 data_failure → 该 case 标记后**整批继续**，下一条 case 正常完成
- 全部物料显式配置的 case → `data_confidence = "reliable"`
- 缺料但启发式自造成功的 case → `data_confidence = "synthesized"`

**会话启动模板**：
```
请执行 Task 9.5 - AssertionJudge + ExecutionEngine 编排。
依赖 Task 8.2 + 9.1-9.4 已完成。目标：完整执行流程编排 + 信心度评级 + 单 case 不阻断
新文件：app/modules/ui_automation/{assertion_judge,execution_engine}.py + persistence.py 实现 + alembic 迁移
设计文档：docs/PHASE2_DESIGN.md §3.3 + §3.6
```

---

### Task 9.6 — 执行 API + SSE 推送 + chat 集成 🟡 M

**目标**：执行触发 API、SSE 端点、停止/重跑、对话集成。

**前置依赖**：Task 9.5（ExecutionEngine）+ Task 7.1（StreamHub）

**产出文件**：
- `app/modules/ui_automation/router.py` 增量端点：
  - `POST /api/projects/{id}/ui-executions`（含 mode、token_budget、llm_config_id、loaded_set_ids、manual_overrides、strict_data_mode）
  - `GET /api/projects/{id}/ui-executions`
  - `GET /api/ui-executions/{id}`
  - `GET /api/ui-executions/{id}/stream` — SSE 订阅 EXECUTION_STREAM_HUB
  - `POST /api/ui-executions/{id}/stop`
  - `POST /api/ui-executions/{id}/retry-failed`
  - `GET /api/ui-executions/{id}/video`
  - `GET /api/ui-executions/{id}/trace`
- `app/modules/llm/intent_handler.py` 增量：`IntentType.RUN_UI_TEST` + 模式匹配
- `app/modules/llm/chat_service.py` 增量：`_handle_ui_test_intent`
  - 与 `_handle_review_intent` / `_handle_generate_intent` 同构
  - 解析用例 hint → 检索匹配用例 → 触发 ExecutionEngine
  - SSE 事件转发到 chat 流
  - 完成后 `ChatMessage.meta_data.action_type = "run_ui_test"`

**SSE 事件全集**（在文档化）：
- 进度类：`execution_start / case_start / step_progress / tool_call / tool_result / reasoning / step_screenshot / step_complete / case_complete / execution_complete`
- 物料类：`data_resolver_built / missing_data_warning / test_data_secret_used / data_synthesized / data_failure_marked / case_confidence`
- 控制类：`info / error / done`

**验证方式**：
- curl 触发执行 → SSE 流中收到完整事件序列
- 在 chat 中说"执行登录相关用例" → AI 触发 → SSE 并行流 → ChatMessage 卡片有 execution_id
- stop API → 中断
- 缺料场景：SSE 收到 missing_data_warning + data_synthesized

**会话启动模板**：
```
请执行 Task 9.6 - 执行 API + SSE + chat 集成。
依赖 Task 7.1 + 9.5 已完成。目标：执行触发 API + SSE + chat intent
增量文件：app/modules/ui_automation/router.py + app/modules/llm/{intent_handler,chat_service}.py
关键复用：app/modules/llm/chat_service.py 已有的 _handle_review_intent / _handle_generate_intent 模式
```

---

### Task 9.7 — 调试模式 + 历史回放 🟡 M

**目标**：调试体验（逐步暂停） + 历史回放（无浏览器）。

**前置依赖**：Task 9.5（ExecutionEngine）+ Task 9.6（API）

**产出文件**：
- `ExecutionEngine` 增量：支持 `mode="debug"`
  - 每个 step 完成后 publish `step_complete` + 等待 `continue` 信号
  - 30 分钟无 continue 自动 stop
- `app/modules/ui_automation/router.py` 增量：
  - `POST /api/ui-executions/{id}/continue`
  - `POST /api/ui-executions/{id}/replay` — SSE 接口
- `app/modules/ui_automation/replayer.py`
  - `async def replay(execution_id) -> AsyncGenerator[Event]`
  - 不启动浏览器；按 step_results 表中存的 snapshot/screenshot/tool_calls 重新通过 SSE 协议吐出
- 权限增量：`ui_automation:debug`

**验证方式**：
- 调试模式：执行卡在第一步 → POST continue → 推进
- 30 分钟无 continue → 自动 stopped
- replay：调 replay API → SSE 流按时间轴吐出全部事件 + 截图 URL

**会话启动模板**：
```
请执行 Task 9.7 - 调试模式 + 历史回放。
依赖 Task 9.5 + 9.6 已完成。目标：调试逐步 + 历史回放 SSE
增量文件：app/modules/ui_automation/{execution_engine,router}.py
新文件：app/modules/ui_automation/replayer.py
```

---

## Phase 10：前端执行与结果（6 个 task）

### Task 10.1 — 用例选择 + 单弹窗触发器 🟡 M

**目标**：用例列表批量选择 + 单弹窗折叠式执行配置（详见设计 §2.5.1）。

**前置依赖**：Task 9.3（配置 API）+ Task 9.6（执行 API）+ Task 8.7（物料前端基础组件）

**核心原则**（v3.0.1）：
- 零必填、一秒确认、accordion 折叠、缺料只警告不阻断、复用上次配置

**产出文件**：
- `src/views/testcases/TestcaseView.vue` 增量：批量操作栏
- `src/components/ui-automation/ExecuteDialog.vue` — 单弹窗折叠式
  - 顶部摘要行 + 物料折叠区 + 高级折叠区 + 黄色缺料提示条 + 单按钮"立即执行"
- `src/components/ui-automation/DataMergePreview.vue` — 物料合并表格 + 临时覆盖
- `src/components/ui-automation/MissingDataBanner.vue` — 黄色非阻断告警条
- `src/components/ui-automation/DataRecommendation.vue` — 推荐物料集
- `src/services/uiAutomation.ts` 增量：`getRecentConfig` + 触发执行 + preview-merge / missing-check

**验证方式**：
- 弹窗默认状态只显示顶部摘要 + 两个折叠标题 + 立即执行（一秒可确认）
- 缺料 captcha → 不展开任何区域直接点立即执行 → 执行成功
- 严格模式勾选 + 缺料 → 立即执行按钮变灰
- "↩ 复用上次"按钮 → 一键恢复配置
- 提交执行 → 跳转执行监控页

**会话启动模板**：
```
请执行 Task 10.1 - 用例选择 + 单弹窗触发器。
依赖 Task 8.7 + 9.3 + 9.6 已完成。目标：单弹窗折叠式执行配置（90% 场景一秒确认）
新文件：src/components/ui-automation/{ExecuteDialog,DataMergePreview,MissingDataBanner,DataRecommendation}.vue
增量文件：src/views/testcases/TestcaseView.vue + src/services/uiAutomation.ts
设计文档：docs/PHASE2_DESIGN.md §2.5.1
```

---

### Task 10.2 — 执行监控页 + SSE 处理 + 数据自造可视化 🟡 M

**目标**：实时进度监控页 + SSE 事件处理 + 数据自造/失败可视化。

**前置依赖**：Task 9.6（SSE 端点）+ Task 10.1（触发跳转）

**产出文件**：
- `src/views/ui-automation/ExecutionMonitor.vue` — 监控主页
- `src/components/ui-automation/CaseProgress.vue` — 单用例进度（含数据状态徽章实时更新）
- `src/components/ui-automation/StepDetail.vue` — 步骤详情卡片
  - reasoning / tool_calls 时间线 / 截图缩略图 / tokens
  - synth tool 显示 🟡 标签 + 自造 keys
  - mark_data_failure 显示 🟠 警告条
- `src/components/ui-automation/LiveScreenshot.vue` — 实时截图预览
- `src/composables/useExecutionSSE.ts` — SSE 连接（复用一期 useSSE.ts）
  - 处理事件：`missing_data_warning / data_synthesized / data_failure_marked / case_confidence` + 通用进度类
- 调试模式：`ExecutionMonitor.vue` 增加"继续下一步"按钮
- 路由：`/projects/:id/ui-executions/:execId/monitor`

**验证方式**：
- 执行中 → 实时更新各用例和步骤
- 缺料场景：顶部黄横幅 + case 徽章变 🟡
- 数据失败场景：case 徽章变 🟠 + 下一条 case 继续
- token 累计 + 80% 预警条
- 调试模式 → "继续"按钮推进

**会话启动模板**：
```
请执行 Task 10.2 - 执行监控页 + SSE 处理 + 数据自造可视化。
依赖 Task 9.6 + 10.1 已完成。目标：实时监控 + SSE 全事件处理 + 数据可视化
新文件：src/views/ui-automation/ExecutionMonitor.vue + src/components/ui-automation/{CaseProgress,StepDetail,LiveScreenshot}.vue + src/composables/useExecutionSSE.ts
关键复用：一期 src/composables/useSSE.ts、components/chat/MessageBubble.vue 时间线样式
```

---

### Task 10.3 — 执行历史列表 + 详情主页 + 物料快照面板 🟡 M

**目标**：历史列表 + 详情主页（不含徽章/视频组件，留下一 task）。

**前置依赖**：Task 9.6（API）

**产出文件**：
- `src/views/ui-automation/ExecutionHistory.vue` — 历史列表（表格 + 业务/执行视图切换）
- `src/views/ui-automation/ExecutionDetail.vue` — 详情主页
  - 顶部双进度条（业务 + 执行通过率）
  - 数据汇总卡片（🟢 / 🟡 / 🟠 计数）
  - 物料快照折叠面板
  - 用例折叠列表（每条标题左侧带数据可信度徽章占位，徽章组件在 Task 10.4）
  - 失败步骤高亮 + AssertionJudge 失败原因
  - "重放"按钮
- `src/components/ui-automation/TestDataSnapshotPanel.vue` — 物料快照面板（已加载集 / 临时覆盖 / 随机实例化 / 缺失被自造 keys）
- 路由：`/projects/:id/ui-executions` + `/projects/:id/ui-executions/:execId/detail`

**验证方式**：
- 历史列表展示所有执行记录，业务/执行视图通过率不同
- 详情页双进度条正确
- 物料快照面板正确展示（secret 遮蔽）
- "按本次配置重跑"按钮 → 复用快照参数触发执行

**会话启动模板**：
```
请执行 Task 10.3 - 执行历史列表 + 详情主页 + 物料快照面板。
依赖 Task 9.6 已完成。目标：历史列表 + 详情主页 + 物料快照
新文件：src/views/ui-automation/{ExecutionHistory,ExecutionDetail}.vue + src/components/ui-automation/TestDataSnapshotPanel.vue
注意：详情页里的徽章 + 自造卡片 + 失败卡片在 Task 10.4；视频/截图查看器在 Task 10.5
```

---

### Task 10.4 — 数据可信度徽章 + 自造/失败卡片 + 双视图 🟢 S

**目标**：详情页里数据可信度相关的可视化组件。

**前置依赖**：Task 10.3（详情主页框架）

**产出文件**：
- `src/components/ui-automation/DataConfidenceBadge.vue` — 三态徽章 🟢/🟡/🟠
- `src/components/ui-automation/SynthesizedDataCard.vue` — 🟡 自造数据卡片（key + value + source + steps）
- `src/components/ui-automation/DataFailureCard.vue` — 🟠 失败原因卡片
- `src/components/ui-automation/ToolCallTimeline.vue` — tool_call 时间线（secret 遮蔽 + synth/failure 高亮）
- `src/views/ui-automation/ExecutionDetail.vue` 增量：把组件挂到用例折叠列表里

**验证方式**：
- 🟡 用例展开 → 自造数据卡片显示 keys + values + sources
- 🟠 用例展开 → 失败原因卡片显示 reason
- ToolCallTimeline 中 platform_get_secret 显示遮蔽；synthesize 显示 🟡

**会话启动模板**：
```
请执行 Task 10.4 - 数据可信度徽章 + 自造/失败卡片。
依赖 Task 10.3 已完成。目标：详情页里的数据可信度可视化
新文件：src/components/ui-automation/{DataConfidenceBadge,SynthesizedDataCard,DataFailureCard,ToolCallTimeline}.vue
增量文件：src/views/ui-automation/ExecutionDetail.vue
```

---

### Task 10.5 — 视频播放 + 截图查看器 + Trace 下载 🟢 S

**目标**：详情页媒体回放组件。

**前置依赖**：Task 10.3（详情主页）

**产出文件**：
- `src/components/ui-automation/VideoPlayer.vue` — 视频播放器（时间轴标注各用例）
- `src/components/ui-automation/ScreenshotViewer.vue` — 截图轮播
- `src/components/ui-automation/SnapshotViewer.vue` — accessibility tree YAML 查看器（高亮关键元素）
- `src/views/ui-automation/ExecutionDetail.vue` 增量：挂载 VideoPlayer + Trace 下载链接

**验证方式**：
- 视频播放正常，时间轴跳转到指定 case
- 截图轮播能浏览所有 step 截图
- Snapshot 查看器 YAML 高亮 + 元素 ref 可点击
- Trace 文件可下载

**会话启动模板**：
```
请执行 Task 10.5 - 视频 + 截图 + Snapshot 查看器。
依赖 Task 10.3 已完成。目标：详情页媒体回放
新文件：src/components/ui-automation/{VideoPlayer,ScreenshotViewer,SnapshotViewer}.vue
增量文件：src/views/ui-automation/ExecutionDetail.vue
```

---

### Task 10.6 — 对话 UIExecutionCard 🟢 S

**目标**：对话气泡里渲染 UI 测试结果卡片。

**前置依赖**：Task 9.6（chat 集成已完成）

**产出文件**：
- `src/components/chat/UIExecutionCard.vue` — 执行结果摘要卡片（pass/fail 数 + 视频缩略图 + 跳转详情按钮 + 数据可信度饼图）
- `src/components/chat/MessageBubble.vue` 增量：识别 `meta_data.action_type === "run_ui_test"` → 渲染 UIExecutionCard
- 对话进行中：直接展示 SSE 事件（与监控页同款的简化版）

**验证方式**：
- 对话输入"用开发环境执行注册相关用例"
- AI 触发 → 对话中实时显示 step_progress + tool_call
- 完成 → 对话气泡显示 UIExecutionCard
- 卡片"查看详情"按钮 → 跳转 ExecutionDetail

**会话启动模板**：
```
请执行 Task 10.6 - 对话 UIExecutionCard。
依赖 Task 9.6 已完成。目标：对话气泡里渲染 UI 测试结果卡片
新文件：src/components/chat/UIExecutionCard.vue
增量文件：src/components/chat/MessageBubble.vue
```

---

## Phase 11：集成完善（4 个 task，11.4 可选）

### Task 11.1 — 仪表盘集成 + UI 统计（业务/执行双视图） 🟡 M

**目标**：Dashboard 增加 UI 自动化统计 + 数据可信度分布。

**前置依赖**：Task 9.5 / 9.6（执行数据已入库）

**产出文件**：
- 后端 `app/modules/dashboard/router.py` 增量：
  - `GET /api/projects/{id}/ui-stats?view=business|execution`
  - 返回字段：`pass_rate`, `total_cases`, `excluded_data_failure_cases`, `confidence_distribution`, `top_synthesized_keys`, `avg_duration`, `total_tokens`
- 前端 `src/views/dashboard/DashboardView.vue` 增量：
  - 最近执行记录列表
  - 业务/执行视图切换（默认业务视图）
  - 通过率趋势图（按视图切换）
  - 数据可信度分布饼图
  - 自造数据 Top 10 keys 列表（提示用户补料）
- 侧栏菜单增量：UI 自动化（环境配置、测试物料、执行历史）

**验证方式**：
- Dashboard 显示 UI 自动化数据
- 业务视图 vs 执行视图通过率不同
- 自造 Top 10 列表正确（按频次降序）
- 侧栏可导航到所有 UI 自动化页面

**会话启动模板**：
```
请执行 Task 11.1 - 仪表盘集成 + UI 统计。
依赖 Task 9.5 + 9.6 已完成。目标：Dashboard UI 自动化数据 + 业务/执行双视图
增量文件：app/modules/dashboard/router.py + src/views/dashboard/DashboardView.vue
```

---

### Task 11.2 — 媒体清理 + State 过期 + 物料文件清理 🟢 S

**目标**：cron 清理任务，防磁盘膨胀。

**前置依赖**：Task 9.5（执行已落数据）

**产出文件**：
- `app/modules/ui_automation/cleanup.py`
  - `async def cleanup_old_media(retention_days=30)`
  - 删过期截图/视频/trace；清空过期 snapshot；删孤立 state
- `app/modules/test_data/cleanup.py`
  - `async def cleanup_orphan_data_files(retention_days=90)`
  - 扫 `uploads/test-data/` 与 DB 做差集
- 启动时注册 APScheduler（或 `asyncio.create_task` 周期循环）
- `POST /api/admin/ui-media/cleanup`（管理员手动触发）
- 配置项：
  - `UI_MEDIA_RETENTION_DAYS=30`
  - `UI_STATE_RETENTION_DAYS=7`
  - `UI_SNAPSHOT_RETENTION_DAYS=7`
  - `TEST_DATA_FILE_RETENTION_DAYS=90`
  - `TEST_DATA_AUDIT_RETENTION_DAYS=180`

**验证方式**：
- 手动触发清理 API → 旧文件被删
- cron 模拟（retention 改 0）→ 全部清干净
- 上传文件物料后删除条目 → 90 天后文件被清

**会话启动模板**：
```
请执行 Task 11.2 - 清理 cron。
目标：媒体 + State + 物料文件 + 审计日志清理
新文件：app/modules/ui_automation/cleanup.py + app/modules/test_data/cleanup.py
配置：env.example 加 5 个 retention 配置项
```

---

### Task 11.3 — Dockerfile 更新 + README 完善 🟢 S

**目标**：Dockerfile 加 Node.js + Playwright；README 加 UI 自动化使用说明。

**前置依赖**：所有功能 task 完成

**⚠️ 施工蓝图已预制**：完整的 Dockerfile 增量片段、docker-compose volumes、`.env.example`
新增项、nginx 静态资源路由、镜像体积估算、常见坑预警，**全部已经写在
`docs/PHASE2_DEPLOYMENT_NOTES.md` §3**。本 task 只需照着 §3 的代码片段贴进去 +
对照 §2 表格把所有 ⏳ 行勾成 ✅，无需再现想。

**产出文件**：
- `backend/Dockerfile` —— 增量参照 `PHASE2_DEPLOYMENT_NOTES.md` §3.1
- `docker-compose.yml` —— 增量参照 §3.2（`test_data` / `ui_artifacts` named volumes）
- `.env.example` —— 增量参照 §3.3（`TEST_DATA_ENCRYPTION_KEY` 等）
- `README.md` —— 增量参照 §3.4（章节标题已列出）
- `frontend/nginx.conf` —— 增量参照 §3.5（`/uploads/ui_artifacts/` 静态资源）
- 反向更新 `docs/PHASE2_DEPLOYMENT_NOTES.md` §2：把所有 ⏳ 挪到 ✅

**验证方式**：
- `docker compose up --build` 起得来；镜像体积接近 §4 估算值（~970MB）
- 容器内：`npx @playwright/mcp --help`、`python -c "import playwright; from mcp import ClientSession"` 均成功
- 容器内能跑一遍真实 UI 用例（依赖 Task 9.5 / 10.x 已完成）

**会话启动模板**：
```
请执行 Task 11.3 - Dockerfile + README。
目标：把 docs/PHASE2_DEPLOYMENT_NOTES.md §3 的预制片段集成到 Dockerfile / docker-compose / .env.example / README / nginx.conf
关键：先读 PHASE2_DEPLOYMENT_NOTES.md，按 §2 ⏳ 清单逐项实施，完成后把 ⏳ 改为 ✅
```

---

### Task 11.4 — ARQ + Worker 容器（可选增强） 🟡 M

**目标**：当用户需要任务跨进程恢复或多 worker 弹性扩容时启用。

**前置依赖**：所有 task 完成；用户主动决定启用

**产出文件**：
- `app/modules/ui_automation/worker.py` — ARQ worker 入口
- `docker-compose.yml` 增量：Redis + Worker service
- `app/modules/ui_automation/stream_hub.py` 改造为 Redis Pub/Sub 订阅模式
- feature flag `USE_TASK_QUEUE=true|false`

**验证方式**：
- 起 Redis + Worker → execution 在 worker 进程跑
- backend 重启后 execution 仍可恢复
- USE_TASK_QUEUE=false → 退回 in-process 模式

**会话启动模板**：
```
请执行 Task 11.4 - ARQ + Worker 容器（可选）。
依赖：所有 task 完成 + 用户主动启用
目标：任务跨进程恢复 + 弹性扩容
新文件：app/modules/ui_automation/worker.py
增量文件：docker-compose.yml + stream_hub.py
```

---

## 执行节奏

```
对话 22: Task 7.1 — ExecutionStreamHub                                🟢
对话 23: Task 7.2 — Playwright MCP Bridge                             🟡
对话 24: Task 7.3 — BrowserBundle + SecurityGuard                     🟡
对话 25: Task 8.1 — 环境配置后端                                       🟡
对话 26: Task 8.2 — 前置步骤执行器 + State 自动复用                    🟡
对话 27: Task 8.3 — 验证码识别                                         🟢
对话 28: Task 8.4 — 环境配置前端                                       🟡
对话 29: Task 8.5 — 测试物料模型 + 基础 CRUD                           🟡
对话 30: Task 8.6 — 物料增强 API（导入 / 推荐 / save-as-set）          🟢
对话 31: Task 8.7 — 物料管理前端：列表 + 编辑器 + 6 种字段             🟡
对话 32: Task 8.8 — 物料前端增强：CSV 导入 + 用例/环境绑定             🟢
对话 33: Task 9.1 — TestDataResolver 核心                              🟡
对话 34: Task 9.2 — Platform Tools + DataSynthesizer                  🟡
对话 35: Task 9.3 — Preflight + 执行配置 API                          🟢
对话 36: Task 9.4 — StepRunner（含 data_manifest）                    🟡
对话 37: Task 9.5 — AssertionJudge + ExecutionEngine 编排             🟡
对话 38: Task 9.6 — 执行 API + SSE + chat 集成                        🟡
对话 39: Task 9.7 — 调试模式 + 历史回放                                🟡
对话 40: Task 10.1 — 用例选择 + 单弹窗触发器                           🟡
对话 41: Task 10.2 — 执行监控页 + SSE + 数据可视化                     🟡
对话 42: Task 10.3 — 执行历史列表 + 详情主页 + 物料快照                🟡
对话 43: Task 10.4 — 数据可信度徽章 + 自造/失败卡片                    🟢
对话 44: Task 10.5 — 视频 + 截图 + Snapshot 查看器                     🟢
对话 45: Task 10.6 — 对话 UIExecutionCard                              🟢
对话 46: Task 11.1 — 仪表盘集成 + UI 统计                              🟡
对话 47: Task 11.2 — 清理 cron                                         🟢
对话 48: Task 11.3 — Dockerfile + README                              🟢
对话 49（可选）: Task 11.4 — ARQ + Worker 容器                        🟡
```

### 预计工期

- **必须完成**：26 个 task / 26 次对话 / **约 22-30 天单人开发**
- **含可选**：27 个 task / 27 次对话 / 约 24-32 天

### 任务规模分布

| 等级 | 数量 | 占比 | 含义 |
|------|------|------|------|
| 🟢 S | 11 | 41% | 会话宽裕，能加点小调整 |
| 🟡 M | 16 | 59% | 会话舒适，按计划执行 |
| 🟠 L | 0 | 0% | 已全部拆解 |

---

## 任务依赖图

```
7.1 ExecutionStreamHub
 └─→ 9.6 执行 API + SSE
7.2 MCP Bridge
 └─→ 7.3 BrowserBundle ──┬──→ 8.2 前置步骤
                          └──→ 9.4 StepRunner
                          └──→ 9.5 ExecutionEngine
7.2 ─→ 9.2 Platform Tools

8.1 环境后端 ──┬──→ 8.2 前置步骤
                └──→ 8.4 环境前端

8.2 前置步骤 ──→ 8.3 验证码识别 ──→ 9.4 StepRunner（回填）
                                  ──→ 9.5 ExecutionEngine

8.5 物料模型 ──┬──→ 8.6 物料增强 API ──→ 9.3 Preflight & 配置 API
                ├──→ 8.7 物料前端基础 ──→ 8.8 物料前端增强
                └──→ 9.1 Resolver ──→ 9.2 Platform Tools
                                    ──→ 9.3 Preflight
                                    ──→ 9.5 ExecutionEngine

9.5 ExecutionEngine ──→ 9.6 执行 API ──┬──→ 9.7 调试 + 回放
                                        ├──→ 10.1 触发弹窗
                                        ├──→ 10.2 监控页
                                        └──→ 11.1 仪表盘

10.1 ──→ 10.2 ──→ 10.3 详情页 ──┬──→ 10.4 徽章/卡片
                                  └──→ 10.5 视频/截图

9.6 chat 集成 ──→ 10.6 对话卡片

所有完成后 → 11.2 清理 + 11.3 部署 + 11.4 (可选) ARQ
```

---

## 二期新增权限清单

```python
PHASE2_PERMISSIONS = {
    # UI 自动化执行
    "ui_automation:config",      # 创建/编辑测试环境和前置步骤
    "ui_automation:execute",     # 触发执行 UI 测试
    "ui_automation:view",        # 查看执行历史和结果
    "ui_automation:stop",        # 停止执行中的测试
    "ui_automation:debug",       # 使用调试模式（默认仅 admin / project_manager）
    "ui_automation:eval",        # 启用 browser_evaluate（默认仅 admin，单环境开关）
    # 测试物料
    "test_data:view",            # 查看物料集（项目级 + 环境级；个人级仅 owner 自己）
    "test_data:edit",            # 创建/编辑/删除物料集
    "test_data:reveal",          # 查看 secret 物料明文（每次调用写审计日志）
    "test_data:import",          # 批量导入 CSV/JSON
}

ROLE_UPDATES = {
    "admin":           ALL_PHASE2_PERMISSIONS,
    "project_manager": ALL_PHASE2_PERMISSIONS - {"ui_automation:eval"},
    "tester": {
        "ui_automation:execute", "ui_automation:view", "ui_automation:stop",
        "test_data:view", "test_data:edit", "test_data:reveal", "test_data:import",
    },
    "viewer": {"ui_automation:view", "test_data:view"},
}
```

权限增量分布到各 task：
- Task 8.1 加 `ui_automation:config / view`
- Task 8.5 加 `test_data:view / edit / reveal / import`
- Task 9.6 加 `ui_automation:execute / stop`
- Task 9.7 加 `ui_automation:debug`
- Task 7.3 / 8.1 设计 `ui_automation:eval`（环境级开关）

---

## 通用会话启动模板

每次新对话使用以下结构（替换 X.X）：

```
请执行 Task X.X — [任务名称]
项目路径：/Users/wxh/Downloads/长轻Job/AITestPlatform
当前进度：一期完成；二期已完成 Task 7.1 ~ Task X.(X-1)
设计文档：docs/PHASE2_DESIGN.md（v3.0.1）
开发计划：docs/PHASE2_IMPLEMENTATION_PLAN.md（v3.1）

按文档中 Task X.X 章节的"产出文件"清单逐项实现，并通过"验证方式"校验。
关键复用：[具体 task 章节里的"关键复用"提示]
约束：
- 完成后用 ReadLints 检查新文件无 lint 错误
- 严格按文档章节里列出的产出文件清单，不引入未列出的依赖
- 数据库迁移用 alembic（如有需要）
- 本 task 完成后给我一份"完成报告"：包含本次新增/修改的文件列表 + 验证结果 + 下一步建议
```

---

## v3.0.1 → v3.1 调整对照

| 维度 | v3.0.1 | v3.1（粒度优化）|
|---|---|---|
| Task 总数 | 17 个 | 27 个（含 1 可选）|
| 单 task 风险 | 5 个 L 级 task（容易超时）| 0 个 L 级 task |
| Task 7.2 | MCP Bridge + Browser Bundle + SecurityGuard | 拆为 7.2 + 7.3 |
| Task 8.4 | 物料后端 6 件事 | 拆为 8.5（基础）+ 8.6（增强）|
| Task 8.5 | 物料前端 8 件事 | 拆为 8.7（基础）+ 8.8（增强）|
| Task 9.0 | Resolver + 自造 + 评级 + API | 拆为 9.1（resolver）+ 9.2（tools）+ 9.3（preflight & API）|
| Task 10.3 | 详情 + 快照 + 徽章 + 双视图 | 拆为 10.3（主页+快照）+ 10.4（徽章+卡片）+ 10.5（视频）|
| Task 11.1 + 11.2 | 合并 1 次对话 | 拆为 11.1 / 11.2 / 11.3 三个 |
| 工期 | 18-19 次对话 | 26-27 次对话 |
| 对话稳定性 | ⚠️ 5 个 task 高风险超时 | ✅ 全部 task 单会话可完成 |

---

## 技术风险

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| `@playwright/mcp` API 演进 | 中 | tool 名 / args 变化 | 锁定 MCP server 版本 + bridge 抽象层屏蔽变化 |
| MCP 与 SDK 共享 BrowserContext 不稳定 | 中 | 状态污染 / 截图错位 | 通过 CDP endpoint 共享；失败回退到"纯 SDK + scripted_steps"模式 |
| Node.js 子进程管理复杂度 | 中 | subprocess 异常 | 启动重试 + 健康探测 + 进程退出自动重启 |
| Snapshot 裁剪不当导致 AI 找不到元素 | 中 | 步骤失败 | 失败时自动回退到完整 snapshot 重试一次（计入 budget）|
| 长时间执行占资源 | 中 | 服务器负载 | in-process 时单 backend 串行；ARQ 模式靠 worker 数限并发 |
| 截图/视频存储膨胀 | 中 | 磁盘占满 | Task 11.2 cron 清理 + retention 可配置 |
| 验证码 OCR 识别不准 | 中 | 登录失败 | 分层策略：state 复用优先 > 万能码 > ddddocr + 刷新重试 3 轮 |
| ddddocr 不支持复杂验证码 | 低 | 无法自动登录 | 建议被测系统测试环境设置万能码，ddddocr 仅覆盖简单图形码 |
| 调试模式被遗忘导致执行卡死 | 低 | 执行长期 pending | 调试模式默认 30 分钟无 continue 自动 stop |
| Token 预算估算不准 | 中 | 预算不够中途中止 | UI 提示历史均值；预算耗尽时清晰提示而非默默失败 |
| 物料 secret 泄露到 LLM 日志 | **高** | **敏感信息泄露** | 三重防护：模板 → `<secret:key>` 占位 / tool result 不写 reasoning_log / SSE 推送前端遮蔽 |
| 物料 key 命名冲突（多集合并）| 低 | AI 用错值 | UI 在合并预览里高亮"被覆盖"行 |
| 用例步骤含 `{{key}}` 但物料没提供 | 中 | 步骤跑错 | preflight 仅警告不阻断；AI 调 `platform_synthesize_data` 兜底（启发式 17+ 常见 key + AI 推断）|
| AI 自造的数据被业务系统拒绝 | 中 | 步骤失败 | AI 调 `platform_mark_data_failure` 主动上报；该 case 标记 `data_failure`；统计排除；继续跑下一条 |
| 用户依赖自造数据导致正式回归不可信 | 中 | 质量度量失真 | 仪表盘双视图，业务视图排除 data_failure；"自造 Top 10 keys"提示用户补料 |
| 物料文件存储膨胀 | 低 | 磁盘占用 | Task 11.2 cron 同时清理 90 天未引用文件 |
| **Task 拆得过细导致整合期 bug** | 中 | 集成时发现接口对不上 | Task 9.5 编排 + Task 9.6 集成 + Task 10.2 监控页是三个集成点，每个都跑端到端冒烟测试 |

---

*文档版本：v3.1 — 任务粒度优化版（在 v3.0.1 架构基础上重排开发节奏）*
*最后更新：2026-05-02*
*主要变更：见"v3.0.1 → v3.1 调整对照"表*
