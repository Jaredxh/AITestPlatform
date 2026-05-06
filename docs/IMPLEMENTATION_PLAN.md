# 第一期实现计划 - 分步执行指南

## 执行原则

1. **每个 Task 独立可验证**：完成后可以通过具体命令/操作验证结果
2. **每个 Task 控制在 30 分钟内**：避免单次对话过长导致异常
3. **严格顺序依赖**：后面的 Task 依赖前面的产出
4. **每步结束时项目可运行**：不会因为中断而处于不可用状态
5. **一键启动**：任何时候 `make dev` 或 `make up` 即可运行整个项目

---

## 依赖管理与启动策略（贯穿全程）

### 核心理念：**开发一条命令、部署一条命令、绝不手动装依赖**

### 后端依赖管理

- **工具**：`uv`（替代 pip/pip-tools，极快且带 lockfile）
- **文件**：
  - `pyproject.toml` — 声明依赖（带版本范围）
  - `uv.lock` — 精确锁定版本（自动生成，提交到 git）
- **规则**：
  - 新增依赖统一通过 `uv add <package>` 执行，自动更新 lock
  - Docker 构建时用 `uv sync --frozen` 确保与 lock 一致
  - **禁止** 手动编辑 lock 文件

### 前端依赖管理

- **工具**：`pnpm`（替代 npm，更快、磁盘友好、严格依赖隔离）
- **文件**：
  - `package.json` — 声明依赖
  - `pnpm-lock.yaml` — 精确锁定（提交到 git）
- **规则**：
  - 新增依赖统一通过 `pnpm add <package>` 执行
  - Docker 构建时用 `pnpm install --frozen-lockfile`
  - **禁止** 使用 `npm install`

### 项目启动管理 - Makefile 统一入口

项目根目录一个 `Makefile`，所有操作通过 `make xxx` 完成：

```makefile
# ==================== 开发环境 ====================

dev:                    # 一键启动开发环境（本地前后端 + Docker 数据库）
    docker compose -f docker-compose.dev.yml up -d db
    @echo "✓ PostgreSQL 已启动 (localhost:5432)"
    cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
    @echo "✓ 后端已启动 (localhost:8000)"
    cd frontend && pnpm dev &
    @echo "✓ 前端已启动 (localhost:5173)"

dev-backend:            # 仅启动后端
    cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend:           # 仅启动前端
    cd frontend && pnpm dev

# ==================== Docker 部署 ====================

up:                     # 一键启动全部容器（生产模式）
    docker compose up -d --build

down:                   # 停止全部容器
    docker compose down

logs:                   # 查看日志
    docker compose logs -f

# ==================== 依赖管理 ====================

install:                # 安装全部依赖（首次 clone 后执行）
    cd backend && uv sync
    cd frontend && pnpm install

add-backend:            # 添加后端依赖 (usage: make add-backend pkg=fastapi)
    cd backend && uv add $(pkg)

add-frontend:           # 添加前端依赖 (usage: make add-frontend pkg=naive-ui)
    cd frontend && pnpm add $(pkg)

# ==================== 数据库 ====================

db-migrate:             # 生成迁移文件 (usage: make db-migrate msg="add user table")
    cd backend && uv run alembic revision --autogenerate -m "$(msg)"

db-upgrade:             # 执行迁移
    cd backend && uv run alembic upgrade head

db-reset:               # 重置数据库（危险！）
    docker compose -f docker-compose.dev.yml down -v
    docker compose -f docker-compose.dev.yml up -d db
    sleep 2
    cd backend && uv run alembic upgrade head

# ==================== 质量检查 ====================

lint:                   # 代码检查
    cd backend && uv run ruff check .
    cd frontend && pnpm lint

format:                 # 代码格式化
    cd backend && uv run ruff format .
    cd frontend && pnpm format

typecheck:              # 类型检查
    cd frontend && pnpm typecheck

# ==================== 构建 ====================

build:                  # 构建生产镜像
    docker compose build

build-frontend:         # 仅构建前端
    cd frontend && pnpm build
```

### Docker Compose 文件策略

两个 compose 文件，职责清晰：

| 文件 | 用途 | 包含服务 |
|------|------|----------|
| `docker-compose.dev.yml` | 本地开发 | 仅 PostgreSQL（前后端本地热重载运行） |
| `docker-compose.yml` | 生产部署 | PostgreSQL + Backend + Frontend（Nginx） |

### 环境变量管理

```
.env.example            # 模板（提交到 git）
.env                    # 实际值（.gitignore 忽略）
```

开发环境默认值写在代码的 config.py 中，生产环境必须通过 `.env` 或 Docker 环境变量覆盖。

---

## Phase 0：项目初始化

### Task 0.1 - 创建项目目录结构和配置文件

**目标**：创建完整的项目骨架，所有配置文件就位，`make install && make dev` 可运行

**产出文件**：
```
AITestPlatform/
├── Makefile                     # 统一命令入口
├── docker-compose.yml           # 生产部署
├── docker-compose.dev.yml       # 开发用（仅数据库）
├── .env.example
├── .gitignore
├── README.md
│
├── backend/
│   ├── Dockerfile               # 多阶段构建（uv）
│   ├── pyproject.toml           # 依赖声明 + 项目元数据
│   ├── uv.lock                  # 依赖锁定（自动生成）
│   ├── alembic.ini
│   ├── alembic/
│   │   └── env.py              # 异步迁移配置
│   └── app/
│       ├── __init__.py
│       ├── main.py              # FastAPI 入口
│       ├── config.py            # Pydantic Settings
│       ├── database.py          # 异步 SQLAlchemy
│       └── core/
│           ├── __init__.py
│           ├── response.py      # 统一响应
│           ├── exceptions.py    # 异常处理
│           └── deps.py          # 依赖注入
│
└── frontend/
    ├── Dockerfile               # 多阶段构建（pnpm）
    ├── package.json
    ├── pnpm-lock.yaml           # 依赖锁定（自动生成）
    ├── vite.config.ts
    ├── tsconfig.json
    ├── uno.config.ts
    ├── index.html
    ├── .npmrc                   # pnpm 配置（registry mirror 等）
    └── src/
        ├── main.ts
        ├── App.vue
        ├── env.d.ts
        ├── router/index.ts
        ├── stores/
        ├── services/request.ts
        ├── layouts/
        ├── views/
        ├── components/
        └── types/
```

**验证方式**：
```bash
make install            # → 后端和前端依赖全部安装完毕
make dev                # → 数据库+后端+前端一起启动
                        #   访问 localhost:8000/docs 看到 Swagger
                        #   访问 localhost:5173 看到前端页面
make up                 # → Docker 生产模式一键启动
make down               # → 一键停止
```

---

### Task 0.2 - 数据库基础和迁移配置

**目标**：SQLAlchemy 模型基类、Alembic 迁移配置、数据库连接验证

**产出**：
- `app/database.py` - 异步引擎 + 会话工厂
- `app/models/base.py` - 带 UUID 主键和时间戳的 Base 类
- `alembic/env.py` - 配置好异步迁移
- 能成功执行 `alembic revision --autogenerate` 和 `alembic upgrade head`

**验证方式**：
- `alembic upgrade head` 无报错
- PostgreSQL 中能看到 alembic_version 表

---

## Phase 1：用户认证系统

### Task 1.1 - 用户模型 + 注册/登录 API

**目标**：实现用户表、注册、登录、获取当前用户信息

**产出**：
- `app/modules/auth/models.py` - User 模型
- `app/modules/auth/schemas.py` - 请求/响应 Schema
- `app/modules/auth/service.py` - 注册、登录、密码哈希
- `app/modules/auth/router.py` - POST /register, POST /login, GET /me
- `app/core/security.py` - JWT 编解码、密码 hash
- `app/core/deps.py` - get_current_user 依赖

**验证方式**：
- Swagger 中测试注册 → 返回用户信息
- 登录 → 返回 access_token + refresh_token
- 带 Token 访问 /me → 返回当前用户

---

### Task 1.2 - 角色与权限模型

**目标**：RBAC 角色表、用户-角色关联、权限检查

**产出**：
- `app/modules/auth/models.py` - 增加 Role, UserRole 模型
- `app/modules/auth/permissions.py` - 权限常量定义 + 检查器
- `app/core/deps.py` - require_permission 依赖
- 初始化脚本创建默认角色（admin, project_manager, tester, viewer）
- 迁移文件

**验证方式**：
- 数据库中有 roles 表和预置角色数据
- 受保护的接口在无权限时返回 403

---

### Task 1.3 - 前端登录页 + Token 管理

**目标**：实现前端登录/注册页面，Token 存储和自动携带

**产出**：
- `src/views/login/LoginView.vue` - 登录页面
- `src/stores/auth.ts` - 认证状态管理
- `src/services/request.ts` - 完善 Token 注入 + 401 刷新
- `src/router/index.ts` - 路由守卫
- `src/layouts/AuthLayout.vue` - 认证页布局

**验证方式**：
- 浏览器访问 → 跳转登录页
- 输入账号密码 → 登录成功进入主页
- 刷新页面 → 保持登录态
- Token 过期 → 自动刷新或跳转登录

---

### Task 1.4 - 用户管理页面（后端 + 前端）

**目标**：管理员可以查看用户列表、编辑用户信息、分配角色

**产出**：
- `app/modules/users/router.py` - 用户列表/详情/编辑/删除 API
- `app/modules/users/service.py` - 用户管理业务逻辑
- `src/views/settings/UserManagement.vue` - 用户管理页
- `src/views/settings/RoleManagement.vue` - 角色管理页
- `src/composables/usePermission.ts` - 权限判断组合式函数

**验证方式**：
- 管理员可以看到用户列表、修改角色
- 普通用户看不到用户管理菜单
- 角色变更后权限立即生效

---

## Phase 2：项目管理

### Task 2.1 - 项目 CRUD + 成员管理（后端）

**目标**：项目的创建/编辑/删除/列表，项目成员的添加/移除

**产出**：
- `app/modules/projects/models.py` - Project, ProjectMember 模型
- `app/modules/projects/schemas.py`
- `app/modules/projects/service.py`
- `app/modules/projects/router.py`
- 迁移文件

**验证方式**：
- API 可创建项目、添加成员
- 项目成员可查看项目，非成员返回 403
- 项目列表只返回用户有权限的项目

---

### Task 2.2 - 项目管理前端

**目标**：项目列表页、创建弹窗、项目设置（成员管理）、全局项目切换器

**产出**：
- `src/views/projects/ProjectList.vue`
- `src/views/projects/ProjectSettings.vue`
- `src/components/common/ProjectSelector.vue` - 全局顶部项目选择器
- `src/stores/project.ts` - 当前项目状态
- `src/layouts/MainLayout.vue` - 主布局（侧栏 + 顶栏 + 内容区）

**验证方式**：
- 能创建项目并在列表中显示
- 顶部选择器可切换项目
- 切换项目后，相关页面内容跟随变化

---

## Phase 3：LLM 配置与对话

### Task 3.1 - LLM 配置管理（后端）

**目标**：LLM 供应商配置的增删改查，API Key 加密存储，连通性测试

**产出**：
- `app/modules/llm/models.py` - LLMConfig 模型
- `app/modules/llm/schemas.py`
- `app/modules/llm/service.py`
- `app/modules/llm/router.py` - CRUD + /test 接口
- `app/modules/llm/providers.py` - OpenAI SDK 统一封装
- `app/core/crypto.py` - AES 加密 API Key

**验证方式**：
- 创建一个 DeepSeek 配置 → 测试连通性 → 返回成功
- API Key 在数据库中是加密的
- 列表接口不返回明文 Key

---

### Task 3.2 - AI 对话（后端 SSE 流式）

**目标**：对话会话管理、消息存储、SSE 流式响应

**产出**：
- `app/modules/llm/models.py` - 增加 ChatSession, ChatMessage 模型
- `app/modules/llm/chat_service.py` - 会话逻辑、上下文组装
- `app/modules/llm/chat_router.py` - 会话 CRUD + 发送消息（SSE）+ 停止
- 迁移文件

**验证方式**：
- curl 测试 SSE 端点 → 看到流式数据输出
- 消息保存到数据库
- 历史消息可以查询

---

### Task 3.3 - LLM 配置管理前端

**目标**：配置列表、添加/编辑弹窗、测试连通性按钮

**产出**：
- `src/views/settings/LLMConfigView.vue`
- `src/components/settings/LLMConfigForm.vue`
- `src/services/llm.ts`

**验证方式**：
- 能添加/编辑/删除 LLM 配置
- 点击"测试连接"能看到结果反馈

---

### Task 3.4 - AI 对话前端（核心）

**目标**：对话界面——会话列表、消息流、流式渲染、输入发送

**产出**：
- `src/views/chat/ChatView.vue` - 页面容器
- `src/components/chat/SessionList.vue` - 左侧会话列表
- `src/components/chat/ChatHeader.vue` - 模型选择
- `src/components/chat/MessageList.vue` - 消息列表
- `src/components/chat/MessageBubble.vue` - 消息气泡（Markdown 渲染）
- `src/components/chat/ChatInput.vue` - 输入框
- `src/composables/useChat.ts` - 对话逻辑
- `src/composables/useSSE.ts` - SSE 流式处理
- `src/services/chat.ts` - API 调用

**验证方式**：
- 选择 LLM 配置 → 新建对话 → 发送消息 → 看到流式回复
- 多轮对话上下文正确
- 可以停止生成
- 切换会话 → 显示历史消息

---

## Phase 4：需求文档与 AI 评审

### Task 4.1 - 文档上传与解析（后端）

**目标**：支持 Word/PDF 上传，解析为文本，存储到数据库

**产出**：
- `app/modules/requirements/models.py` - RequirementDocument 模型
- `app/modules/requirements/schemas.py`
- `app/modules/requirements/parser.py` - docx/pdf 解析器
- `app/modules/requirements/router.py` - 上传/列表/详情/删除
- `app/modules/requirements/service.py`
- 文件存储目录配置

**验证方式**：
- 上传一个 .docx 文件 → 返回成功，content_text 有内容
- 上传一个 .pdf 文件 → 同上
- 列表接口返回已上传的文档

---

### Task 4.2 - AI 评审（后端）

**目标**：触发 AI 评审文档，多维度分析，存储评审结果

**产出**：
- `app/modules/requirements/models.py` - 增加 AIReview 模型
- `app/modules/requirements/review_service.py` - 评审逻辑
- `app/modules/llm/prompts/review.py` - 评审 Prompt 模板
- `app/modules/requirements/router.py` - 增加 POST /review, GET /reviews

**验证方式**：
- 对已上传文档触发评审 → AI 返回多维度评分和问题列表
- 评审结果持久化到数据库
- 多次评审不会覆盖历史结果

---

### Task 4.3 - 提示词管理（后端）

**目标**：项目级提示词模板系统——CRUD、二级分类、变量系统、版本历史、内置模板初始化

**产出**：
- `app/modules/prompts/models.py` — PromptTemplate, PromptVersion
- `app/modules/prompts/schemas.py` — 请求/响应 Schema
- `app/modules/prompts/service.py` — CRUD + 模板渲染（`{{变量}}` 替换）+ 版本管理
- `app/modules/prompts/router.py` — API 端点
- `app/modules/prompts/built_in.py` — 内置模板数据（对话角色、评审维度、用例生成）
- 初始化命令：项目创建时自动写入内置模板
- 迁移文件

**API**：
```
GET    /api/projects/{id}/prompts              # 列表（支持 category 筛选）
POST   /api/projects/{id}/prompts              # 创建
GET    /api/prompts/{id}                       # 详情
PATCH  /api/prompts/{id}                       # 编辑（自动保存版本）
DELETE /api/prompts/{id}                       # 删除（系统内置不可删）
GET    /api/prompts/{id}/versions              # 版本历史
POST   /api/prompts/{id}/set-default           # 设为该分类默认
```

**验证方式**：
- 创建项目 → 自动生成内置提示词（对话/评审/生成三类）
- 编辑提示词 → 版本号 +1，旧版本可查
- 按 category 筛选 → 只返回对应分类
- 系统内置模板可编辑但不可删除

---

### Task 4.4 - 提示词管理前端 + 对话集成

**目标**：提示词管理页面 + 对话顶部提示词选择器 + 评审自动调用提示词

**产出**：
- `src/views/settings/PromptManagement.vue` — 提示词列表（左侧分类筛选 + 右侧列表）
- `src/components/prompts/PromptEditor.vue` — 编辑器（变量点击插入、预览）
- `src/components/prompts/PromptVersionHistory.vue` — 版本历史抽屉
- `src/components/chat/ChatHeader.vue` — 增加提示词选择器下拉
- `src/services/prompts.ts` — API 调用
- 评审触发时自动查找 `category=review, auto_apply=true` 的提示词

**验证方式**：
- 管理页：按分类查看/编辑提示词，查看版本历史
- 对话页：顶部可选择 chat 类提示词，选择后作为 system prompt
- 触发评审 → 自动使用 review 类提示词（无需手动选择）

---

### Task 4.5 - 需求管理前端

**目标**：需求文档列表、上传、详情查看、触发评审、评审结果展示

**产出**：
- `src/views/requirements/RequirementList.vue`
- `src/views/requirements/RequirementDetail.vue`
- `src/components/requirements/UploadDialog.vue`
- `src/components/requirements/ReviewResult.vue` - 评审结果卡片
- `src/services/requirements.ts`

**验证方式**：
- 上传文档 → 列表显示
- 点击"AI 评审" → 加载动画 → 显示评审结果（评分+问题）
- 可查看历史评审记录

---

## Phase 5：测试用例管理与 AI 生成

### Task 5.1 - 用例模块树 + CRUD（后端）

**目标**：模块树结构、用例增删改查、分页/筛选

**产出**：
- `app/modules/testcases/models.py` - TestcaseModule, Testcase, TestcaseStep
- `app/modules/testcases/schemas.py`
- `app/modules/testcases/service.py`
- `app/modules/testcases/router.py` - 模块 CRUD + 用例 CRUD

**验证方式**：
- 创建模块树 → 在模块下创建用例 → 列表接口按模块筛选
- 用例支持步骤的增删改

---

### Task 5.2 - AI 生成测试用例（后端）

**目标**：根据需求文档 AI 生成用例，流式输出，支持批量确认

**产出**：
- `app/modules/testcases/generation_service.py` - 生成逻辑
- `app/modules/llm/prompts/testcase_gen.py` - 生成 Prompt
- `app/modules/testcases/router.py` - 增加 POST /generate (SSE), POST /batch-accept
- `app/modules/testcases/models.py` - 增加 AIGenerationBatch

**验证方式**：
- 指定需求文档 → 调用生成接口 → 流式返回用例 JSON
- 生成结果可以批量接受入库
- 入库的用例 source 标记为 "ai_generated"

---

### Task 5.3 - 用例管理前端

**目标**：模块树、用例列表（带筛选排序分页）、用例详情编辑

**产出**：
- `src/views/testcases/TestcaseView.vue` - 左侧模块树 + 右侧用例列表
- `src/components/testcases/ModuleTree.vue`
- `src/components/testcases/TestcaseTable.vue`
- `src/components/testcases/TestcaseDetail.vue` - 抽屉/弹窗详情编辑
- `src/services/testcases.ts`

**验证方式**：
- 模块树可展开折叠、新建/重命名/删除
- 点击模块 → 右侧显示该模块下用例
- 可以新建/编辑/删除用例

---

### Task 5.4 - AI 生成用例前端

**目标**：选择需求文档生成用例，预览AI输出，逐条/批量接受

**产出**：
- `src/components/testcases/GenerateDialog.vue` - 生成配置弹窗
- `src/components/testcases/GeneratePreview.vue` - 预览生成结果
- `src/components/testcases/GeneratedCaseCard.vue` - 单条用例卡片（接受/编辑/拒绝）

**验证方式**：
- 选择需求文档 → 点击"AI 生成" → 流式看到用例产出
- 可以逐条接受/拒绝/编辑后接受
- "全部接受" → 用例入库，列表中可见

---

## Phase 6：集成完善

### Task 6.1 - 对话中的智能操作

**目标**：在对话中支持"评审文档"、"生成用例"等指令（通过意图识别或固定命令）

**产出**：
- `app/modules/llm/intent_handler.py` - 简单意图识别
- `app/modules/llm/chat_service.py` - 增强：支持调用评审和生成服务
- 前端对话消息中嵌入操作结果卡片

**验证方式**：
- 对话中输入"帮我评审项目X的最新需求文档" → AI 执行评审并返回结果
- 对话中输入"根据需求XX生成测试用例" → 流式生成用例

---

### Task 6.2 - 仪表盘 + 全局优化

**目标**：首页概览数据、全局样式调优、响应式适配

**产出**：
- `src/views/dashboard/DashboardView.vue` - 项目概览统计
- 全局 UI 调优（加载状态、空状态、错误提示）
- `app/modules/dashboard/router.py` - 统计数据 API

**验证方式**：
- 登录后看到项目维度的数据概览
- 各页面空状态、加载态、错误态表现正常

---

### Task 6.3 - 部署配置 + 初始化脚本

**目标**：完善 Docker 部署配置，编写初始化脚本

**产出**：
- `docker-compose.yml` 完善
- `backend/entrypoint.sh` - 自动迁移 + 初始化管理员
- `frontend/nginx.conf` - Nginx 配置
- `scripts/init.sh` - 一键初始化
- `README.md` - 部署指南

**验证方式**：
- 新环境 `docker compose up` → 所有服务正常
- 首次启动自动创建管理员账号
- 浏览器访问正常使用全部功能

---

## 执行节奏建议

```
推荐每次对话执行 1-2 个 Task，具体取决于 Task 复杂度：

对话 1:  Task 0.1（项目骨架）              ✅ 已完成
对话 2:  Task 0.2（数据库配置）
对话 3:  Task 1.1（用户注册登录）
对话 4:  Task 1.2（角色权限）
对话 5:  Task 1.3（前端登录页）
对话 6:  Task 1.4（用户管理页面）
对话 7:  Task 2.1 + 2.2（项目管理后端+前端）
对话 8:  Task 3.1（LLM 配置后端）
对话 9:  Task 3.2（对话 SSE 后端）
对话 10: Task 3.3 + 3.4（LLM 前端 + 对话前端）
对话 11: Task 4.1（文档上传解析）
对话 12: Task 4.2（AI 评审后端）
对话 13: Task 4.3（提示词管理后端）
对话 14: Task 4.4（提示词管理前端 + 对话集成）
对话 15: Task 4.5（需求管理前端）
对话 16: Task 5.1（用例 CRUD 后端）
对话 17: Task 5.2（AI 生成用例后端）
对话 18: Task 5.3（用例管理前端）
对话 19: Task 5.4（AI 生成前端）
对话 20: Task 6.1（对话智能操作）
对话 21: Task 6.2 + 6.3（仪表盘 + 部署）
```

### 总计：约 19-26 天（单人开发，21 个 Task）

## 每次对话的启动模板

在新对话开始时，可以这样告诉我：

> 请执行 Task X.X - [任务名称]。
> 项目路径：/path/to/AITestPlatform
> 当前进度：已完成 Task X.X

我会：
1. 确认前置依赖已就位
2. 实现当前 Task 的所有产出
3. 进行验证
4. 报告完成状态和下一步

---

## 注意事项

1. **每个 Task 结束时确保代码可运行** - 不留半成品
2. **后端每个 Task 后跑一次 `alembic upgrade head`** - 确保迁移正确
3. **前端每个 Task 后确认 `npm run build` 无报错** - 确保类型正确
4. **如果某个 Task 超出预期复杂** - 可以拆成 Task X.Xa 和 Task X.Xb
5. **AI 相关功能可以先 mock** - 如果 LLM API Key 暂时没有，用假数据先跑通流程
