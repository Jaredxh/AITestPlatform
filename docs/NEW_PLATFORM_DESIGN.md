# AI 驱动测试管理平台 - 第一期设计文档

## 一、项目定位与设计原则

### 1.1 定位
一个**轻量、易用、AI 驱动**的测试管理平台。核心理念是"让 AI 做重活，让人做决策"。

### 1.2 对比 WHartTest 的改进方向

| 维度 | WHartTest 问题 | 新平台改进 |
|------|---------------|-----------|
| 复杂度 | 16 个 Django App，功能耦合多 | 精简为 5-6 个核心模块，渐进式扩展 |
| 前端体验 | 单文件超 2000 行，操作路径长 | 组件化拆分，减少操作步骤，AI 对话即操作 |
| 技术栈 | Django 同步为主 + Celery 异步补充 | FastAPI 原生异步，天然适合 AI 流式场景 |
| AI 集成 | LangGraph/LangChain 全家桶过重 | 轻量封装，按需引入，直接调用 LLM API |
| 部署 | 依赖 PostgreSQL + Redis + Qdrant + Celery | 最小部署仅需 PostgreSQL，逐步可选加 Redis |
| 权限 | Django 模型权限过于细碎 | RBAC 角色模型，简单直观 |
| 前端框架 | Arco Design（相对小众） | Ant Design Vue / Naive UI（生态更好） |

### 1.3 核心设计原则

1. **简单优先**：能用一步完成的不用两步
2. **AI 原生**：对话即操作，AI 不是附加功能而是核心交互方式
3. **渐进增强**：第一期最小可用，后续逐期叠加
4. **类型安全**：前后端严格类型约束
5. **流式优先**：AI 回复全部流式，体验流畅

---

## 二、技术栈选型

### 2.1 后端

| 组件 | 选型 | 理由 |
|------|------|------|
| Web 框架 | **FastAPI** | 原生异步、自动 OpenAPI 文档、类型驱动、SSE/WebSocket 原生支持 |
| ORM | **SQLAlchemy 2.0** + Alembic | 异步支持好，类型提示完整，迁移灵活 |
| 数据库 | **PostgreSQL** | 成熟稳定，支持全文搜索和 JSON 字段 |
| 认证 | **JWT** (python-jose) | 无状态、轻量 |
| AI 调用 | **OpenAI SDK** (兼容协议) | 统一接口调各家 LLM（OpenAI/通义/DeepSeek/Ollama） |
| 流式输出 | **SSE** (Server-Sent Events) | 比 WebSocket 简单，适合 AI 对话场景 |
| 文档解析 | **python-docx / pypdf / unstructured** | 按需解析 Word/PDF |
| 任务队列 | **第一期不需要**（后续可加 Redis + ARQ） | FastAPI 后台任务足够处理文档解析 |
| 缓存 | **第一期不需要**（后续可加 Redis） | 减少部署复杂度 |

### 2.2 前端

| 组件 | 选型 | 理由 |
|------|------|------|
| 框架 | **Vue 3** + TypeScript + Vite | 成熟稳定，团队熟悉 |
| UI 库 | **Naive UI** | 轻量、TypeScript 原生、主题定制灵活、组件质量高 |
| 状态管理 | **Pinia** | Vue 3 官方推荐 |
| HTTP | **ofetch** (替代 axios) | 更轻量，原生支持 SSE stream |
| CSS | **UnoCSS** (替代 Tailwind) | 更快、按需生成、预设丰富 |
| 富文本/Markdown | **Milkdown** 或 **ByteMD** | 编辑需求文档评审结果 |
| 代码高亮 | **Shiki** | 展示生成的测试用例 |

### 2.3 依赖与包管理

| 端 | 工具 | 锁文件 | 说明 |
|-----|------|--------|------|
| 后端 | **uv** | `uv.lock` | 替代 pip/poetry，极快，原生 lockfile |
| 前端 | **pnpm** | `pnpm-lock.yaml` | 严格依赖隔离，磁盘友好 |
| 统一入口 | **Makefile** | — | 所有操作通过 `make xxx` 执行 |

### 2.4 部署

| 组件 | 选型 |
|------|------|
| 容器 | Docker + docker-compose |
| 后端运行 | uvicorn (多 worker) |
| 前端运行 | Nginx (静态托管) |
| 数据库 | PostgreSQL 16 |
| 最小部署 | 3 个容器（前端 + 后端 + 数据库） |
| 开发模式 | 1 个容器（仅数据库）+ 本地热重载 |

### 2.5 开发体验保障

| 需求 | 方案 |
|------|------|
| 首次上手 | `make install && make dev` 两步搞定 |
| 日常开发 | `make dev` 一键启动全部 |
| 加依赖 | `make add-backend pkg=xxx` / `make add-frontend pkg=xxx` |
| 数据库迁移 | `make db-migrate msg="xxx"` + `make db-upgrade` |
| 生产部署 | `make up` 一键拉起 |
| 代码检查 | `make lint` / `make format` / `make typecheck` |

---

## 三、系统架构设计

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    前端 (Vue 3 SPA)                       │
│  ┌──────────┬──────────┬──────────┬──────────┬────────┐ │
│  │ AI 对话  │ 项目管理  │ 需求管理  │ 用例管理  │ 系统设置│ │
│  └──────────┴──────────┴──────────┴──────────┴────────┘ │
└────────────────────────────┬────────────────────────────┘
                             │ HTTP/SSE
┌────────────────────────────┴────────────────────────────┐
│                  后端 (FastAPI)                           │
│  ┌──────────────────────────────────────────────────┐   │
│  │              API 路由层 (Router)                    │   │
│  ├──────────────────────────────────────────────────┤   │
│  │              业务服务层 (Service)                   │   │
│  ├──────────────────────────────────────────────────┤   │
│  │              数据访问层 (Repository)                │   │
│  ├──────────────────────────────────────────────────┤   │
│  │              AI 服务层 (LLM Client)                │   │
│  └──────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────┘
                             │
                    ┌────────┴────────┐
                    │   PostgreSQL    │
                    └─────────────────┘
```

### 3.2 模块划分

```
backend/
├── app/
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # 配置管理
│   ├── database.py             # 数据库连接
│   ├── deps.py                 # 依赖注入（当前用户、数据库会话等）
│   │
│   ├── modules/
│   │   ├── auth/               # 认证模块
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── models.py
│   │   │   └── schemas.py
│   │   │
│   │   ├── users/              # 用户与权限
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── models.py
│   │   │   └── schemas.py
│   │   │
│   │   ├── projects/           # 项目管理
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── models.py
│   │   │   └── schemas.py
│   │   │
│   │   ├── requirements/       # 需求文档
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── models.py
│   │   │   ├── schemas.py
│   │   │   └── parser.py       # 文档解析
│   │   │
│   │   ├── testcases/          # 测试用例
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── models.py
│   │   │   └── schemas.py
│   │   │
│   │   └── llm/                # LLM 配置与对话
│   │       ├── router.py
│   │       ├── service.py
│   │       ├── models.py
│   │       ├── schemas.py
│   │       ├── providers.py    # 多 LLM 供应商适配
│   │       └── prompts/        # 系统 Prompt 模板
│   │           ├── review.py
│   │           └── testcase_gen.py
│   │
│   └── core/
│       ├── security.py         # JWT、密码哈希
│       ├── permissions.py      # RBAC 权限检查
│       ├── pagination.py       # 统一分页
│       └── response.py         # 统一响应格式
│
├── alembic/                    # 数据库迁移
├── tests/                      # 测试
├── requirements.txt
├── Dockerfile
└── .env.example
```

前端结构：

```
frontend/
├── src/
│   ├── main.ts
│   ├── App.vue
│   ├── router/
│   │   └── index.ts
│   ├── stores/
│   │   ├── auth.ts
│   │   ├── project.ts
│   │   └── llm.ts
│   ├── layouts/
│   │   ├── MainLayout.vue      # 主布局（极简侧栏+内容）
│   │   └── AuthLayout.vue      # 登录页布局
│   ├── views/
│   │   ├── login/
│   │   ├── dashboard/
│   │   ├── projects/
│   │   ├── requirements/
│   │   ├── testcases/
│   │   ├── chat/               # AI 对话（核心交互入口）
│   │   └── settings/           # LLM 配置、用户管理、权限
│   ├── components/
│   │   ├── common/             # 通用组件
│   │   ├── chat/               # 对话相关组件（每个 <300 行）
│   │   │   ├── ChatSidebar.vue
│   │   │   ├── ChatMessages.vue
│   │   │   ├── ChatInput.vue
│   │   │   ├── MessageBubble.vue
│   │   │   └── StreamingText.vue
│   │   ├── requirements/
│   │   └── testcases/
│   ├── composables/            # 组合式函数
│   │   ├── useChat.ts
│   │   ├── useSSE.ts
│   │   └── usePermission.ts
│   ├── services/               # API 调用层
│   │   ├── request.ts          # ofetch 封装
│   │   ├── auth.ts
│   │   ├── projects.ts
│   │   ├── requirements.ts
│   │   ├── testcases.ts
│   │   └── llm.ts
│   ├── types/                  # 全局类型定义
│   └── utils/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── uno.config.ts
└── Dockerfile
```

---

## 四、数据库设计

### 4.1 ER 图（核心表）

```
┌──────────┐     ┌──────────────┐     ┌──────────────┐
│   User   │────<│ ProjectMember│>────│   Project    │
└──────────┘     └──────────────┘     └──────────────┘
     │                                       │
     │                                       │
     │           ┌───────────────────────────┤
     │           │                           │
┌────┴─────┐  ┌──┴───────────┐    ┌─────────┴─────┐
│   Role   │  │ Requirement  │    │   TestCase    │
└──────────┘  │   Document   │    └───────────────┘
              └──────────────┘           │
                     │              ┌────┴─────┐
              ┌──────┴──────┐      │ TestStep │
              │  AIReview   │      └──────────┘
              └─────────────┘

┌──────────────┐     ┌──────────────┐
│  LLMConfig   │     │ ChatSession  │───< ChatMessage
└──────────────┘     └──────────────┘
```

### 4.2 核心表结构

```sql
-- ===== 用户与权限 =====

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    display_name VARCHAR(100),
    avatar_url VARCHAR(500),
    is_active BOOLEAN DEFAULT TRUE,
    is_superuser BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(50) UNIQUE NOT NULL,        -- admin, project_manager, tester, viewer
    display_name VARCHAR(100) NOT NULL,
    description TEXT,
    permissions JSONB NOT NULL DEFAULT '[]',  -- ["project:create", "testcase:edit", ...]
    is_system BOOLEAN DEFAULT FALSE,          -- 系统内置角色不可删
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE user_roles (
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    role_id UUID REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- ===== 项目管理 =====

CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    description TEXT,
    status VARCHAR(20) DEFAULT 'active',     -- active, archived
    owner_id UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE project_members (
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) DEFAULT 'member',       -- owner, admin, member, viewer
    joined_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (project_id, user_id)
);

-- ===== 需求文档 =====

CREATE TABLE requirement_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_size INTEGER,
    content_text TEXT,                        -- 解析后的纯文本（用于 AI 分析）
    content_structured JSONB,                 -- 结构化解析结果（章节/段落）
    status VARCHAR(20) DEFAULT 'uploaded',    -- uploaded, parsing, parsed, reviewed
    uploaded_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE ai_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES requirement_documents(id) ON DELETE CASCADE,
    review_type VARCHAR(50) NOT NULL,         -- completeness, clarity, testability, risk
    result JSONB NOT NULL,                    -- 评审结果（结构化）
    summary TEXT,                             -- 概要总结
    score INTEGER,                            -- 0-100 评分
    llm_config_id UUID REFERENCES llm_configs(id),
    model_used VARCHAR(100),
    tokens_used INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ===== 测试用例 =====

CREATE TABLE testcase_modules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES testcase_modules(id),
    name VARCHAR(100) NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE testcases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    module_id UUID REFERENCES testcase_modules(id),
    requirement_id UUID REFERENCES requirement_documents(id), -- 关联需求
    title VARCHAR(300) NOT NULL,
    precondition TEXT,
    priority VARCHAR(10) DEFAULT 'medium',   -- critical, high, medium, low
    case_type VARCHAR(20) DEFAULT 'functional', -- functional, performance, security, etc.
    status VARCHAR(20) DEFAULT 'draft',      -- draft, review, approved, deprecated
    source VARCHAR(20) DEFAULT 'manual',     -- manual, ai_generated
    ai_generation_id UUID,                   -- 关联 AI 生成批次
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE testcase_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    testcase_id UUID REFERENCES testcases(id) ON DELETE CASCADE,
    step_number INTEGER NOT NULL,
    action TEXT NOT NULL,                     -- 操作步骤
    expected_result TEXT NOT NULL,            -- 预期结果
    test_data TEXT,                           -- 测试数据
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ===== LLM 配置与对话 =====

CREATE TABLE llm_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    provider VARCHAR(50) NOT NULL,            -- openai, deepseek, qwen, ollama, custom
    model VARCHAR(100) NOT NULL,              -- gpt-4o, deepseek-chat, qwen-turbo, etc.
    api_key_encrypted VARCHAR(500),           -- 加密存储
    base_url VARCHAR(500),                    -- API 端点
    temperature FLOAT DEFAULT 0.7,
    max_tokens INTEGER DEFAULT 4096,
    is_default BOOLEAN DEFAULT FALSE,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id),  -- 可选关联项目
    title VARCHAR(200),
    llm_config_id UUID REFERENCES llm_configs(id),
    system_prompt TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,                -- system, user, assistant
    content TEXT NOT NULL,
    tokens_used INTEGER,
    model_used VARCHAR(100),
    metadata JSONB,                           -- 附加信息（引用文档、生成用例等）
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ===== AI 用例生成批次 =====

CREATE TABLE ai_generation_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    document_id UUID REFERENCES requirement_documents(id),
    prompt_used TEXT,
    llm_config_id UUID REFERENCES llm_configs(id),
    model_used VARCHAR(100),
    total_cases INTEGER DEFAULT 0,
    accepted_cases INTEGER DEFAULT 0,
    status VARCHAR(20) DEFAULT 'generating',  -- generating, completed, partial
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 4.3 权限设计（RBAC 简化版）

```python
# 预定义权限列表
PERMISSIONS = {
    # 项目
    "project:create",
    "project:edit",
    "project:delete",
    "project:view",
    # 需求
    "requirement:upload",
    "requirement:delete",
    "requirement:review",     # 触发 AI 评审
    "requirement:view",
    # 用例
    "testcase:create",
    "testcase:edit",
    "testcase:delete",
    "testcase:view",
    "testcase:generate",      # AI 生成
    "testcase:approve",       # 审批 AI 生成的用例
    # LLM
    "llm:config",             # 配置 LLM
    "llm:chat",               # 使用对话
    # 用户管理
    "user:manage",
    "role:manage",
}

# 预置角色
SYSTEM_ROLES = {
    "admin": ALL_PERMISSIONS,
    "project_manager": {"project:*", "requirement:*", "testcase:*", "llm:chat"},
    "tester": {"project:view", "requirement:view", "testcase:*", "llm:chat"},
    "viewer": {"project:view", "requirement:view", "testcase:view"},
}
```

---

## 五、核心 API 设计

### 5.1 API 路由规划

```
POST   /api/auth/login              # 登录
POST   /api/auth/register           # 注册
POST   /api/auth/refresh            # 刷新 Token
GET    /api/auth/me                 # 当前用户信息

GET    /api/users                   # 用户列表
PATCH  /api/users/{id}              # 更新用户
DELETE /api/users/{id}              # 删除用户
GET    /api/roles                   # 角色列表
POST   /api/roles                   # 创建角色
PATCH  /api/roles/{id}              # 编辑角色

GET    /api/projects                # 项目列表
POST   /api/projects                # 创建项目
GET    /api/projects/{id}           # 项目详情
PATCH  /api/projects/{id}           # 编辑项目
DELETE /api/projects/{id}           # 删除/归档项目
POST   /api/projects/{id}/members   # 添加成员
DELETE /api/projects/{id}/members/{uid} # 移除成员

# 需求文档（项目维度）
GET    /api/projects/{id}/requirements          # 需求列表
POST   /api/projects/{id}/requirements/upload   # 上传文档
GET    /api/requirements/{id}                   # 文档详情
DELETE /api/requirements/{id}                   # 删除文档
POST   /api/requirements/{id}/review            # 触发 AI 评审
GET    /api/requirements/{id}/reviews           # 查看评审结果

# 测试用例（项目维度）
GET    /api/projects/{id}/testcases             # 用例列表（支持筛选/分页）
POST   /api/projects/{id}/testcases             # 创建用例
GET    /api/testcases/{id}                      # 用例详情
PATCH  /api/testcases/{id}                      # 编辑用例
DELETE /api/testcases/{id}                      # 删除用例
POST   /api/projects/{id}/testcases/generate    # AI 生成用例（SSE 流式）
POST   /api/testcases/batch-accept              # 批量接受 AI 用例
GET    /api/projects/{id}/modules               # 模块树
POST   /api/projects/{id}/modules               # 创建模块

# LLM 配置
GET    /api/llm/configs                         # 配置列表
POST   /api/llm/configs                         # 创建配置
PATCH  /api/llm/configs/{id}                    # 编辑配置
DELETE /api/llm/configs/{id}                    # 删除配置
POST   /api/llm/configs/{id}/test               # 测试连通性

# AI 对话
GET    /api/chat/sessions                       # 会话列表
POST   /api/chat/sessions                       # 创建会话
DELETE /api/chat/sessions/{id}                  # 删除会话
GET    /api/chat/sessions/{id}/messages         # 消息历史
POST   /api/chat/sessions/{id}/send             # 发送消息（SSE 流式响应）
POST   /api/chat/sessions/{id}/stop             # 停止生成
```

### 5.2 统一响应格式

```json
// 成功
{
  "success": true,
  "data": { ... },
  "message": null
}

// 列表（带分页）
{
  "success": true,
  "data": {
    "items": [...],
    "total": 100,
    "page": 1,
    "page_size": 20
  }
}

// 错误
{
  "success": false,
  "data": null,
  "message": "具体错误信息",
  "code": "PERMISSION_DENIED"
}
```

### 5.3 AI 流式响应设计（SSE）

```
POST /api/chat/sessions/{id}/send
Content-Type: application/json
Body: { "content": "帮我分析这个需求文档的可测试性" }

Response: text/event-stream

event: start
data: {"message_id": "uuid"}

event: delta
data: {"content": "根据"}

event: delta
data: {"content": "文档分析"}

event: delta
data: {"content": "，以下是..."}

event: done
data: {"message_id": "uuid", "tokens_used": 256, "model": "deepseek-chat"}
```

---

## 六、核心功能详细设计

### 6.1 AI 对话模块

**设计理念**：对话不仅是聊天，更是操作入口。用户可以通过对话：
- 直接让 AI 评审当前项目的需求文档
- 让 AI 基于需求生成测试用例
- 询问项目相关的任何问题

**关键实现**：

```python
# backend/app/modules/llm/providers.py
from openai import AsyncOpenAI

class LLMProvider:
    """统一 LLM 调用接口，利用 OpenAI SDK 兼容协议"""

    def __init__(self, config: LLMConfig):
        self.client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,  # 支持 DeepSeek/通义/Ollama 等
        )
        self.model = config.model
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens

    async def chat_stream(self, messages: list[dict], **kwargs):
        """流式对话"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
            **kwargs,
        )
        async for chunk in response:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def chat(self, messages: list[dict], **kwargs) -> str:
        """非流式对话（用于后台任务如评审）"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=False,
            **kwargs,
        )
        return response.choices[0].message.content
```

### 6.2 需求评审模块

**流程**：
1. 用户上传需求文档（Word/PDF）
2. 后端解析为结构化文本
3. 用户点击"AI 评审"或在对话中说"评审这个文档"
4. AI 从多个维度分析并给出报告

**评审维度**：

```python
# backend/app/modules/llm/prompts/review.py

REVIEW_DIMENSIONS = {
    "completeness": {
        "name": "完整性",
        "prompt": """请从以下维度评审需求文档的完整性：
1. 功能需求是否有遗漏
2. 非功能需求（性能、安全、可用性）是否覆盖
3. 边界条件和异常场景是否考虑
4. 输入输出是否明确定义

请输出 JSON 格式：
{
  "score": 0-100,
  "issues": [{"severity": "high/medium/low", "description": "...", "suggestion": "..."}],
  "summary": "一句话总结"
}"""
    },
    "testability": {
        "name": "可测试性",
        "prompt": """请评估需求文档的可测试性：
1. 需求是否可量化验证
2. 验收标准是否明确
3. 是否存在模糊描述（如"快速"、"友好"）
4. 测试覆盖的难易程度

同样以 JSON 格式输出..."""
    },
    "clarity": {
        "name": "清晰性",
        "prompt": "..."
    },
    "risk": {
        "name": "风险识别",
        "prompt": "..."
    }
}
```

### 6.3 AI 生成测试用例

**流程**：
1. 用户选择需求文档或在对话中指定
2. AI 解析需求，生成测试用例（流式输出过程可见）
3. 生成的用例进入"待确认"列表
4. 用户可逐条/批量接受、修改或拒绝
5. 接受后进入正式用例库

**Prompt 设计**：

```python
# backend/app/modules/llm/prompts/testcase_gen.py

TESTCASE_GENERATION_PROMPT = """你是一位资深测试工程师。请根据以下需求文档内容，生成全面的功能测试用例。

## 需求文档内容
{document_content}

## 生成要求
1. 覆盖正常流程、异常流程、边界条件
2. 每个用例包含：标题、前置条件、操作步骤、预期结果
3. 标注优先级（critical/high/medium/low）
4. 按功能模块分组

## 输出格式（严格 JSON）
{
  "module": "模块名称",
  "testcases": [
    {
      "title": "用例标题",
      "priority": "high",
      "precondition": "前置条件描述",
      "steps": [
        {"action": "操作步骤1", "expected": "预期结果1"},
        {"action": "操作步骤2", "expected": "预期结果2"}
      ]
    }
  ]
}

请开始生成："""
```

### 6.4 前端对话组件设计（解决 WHartTest 单文件过大问题）

```
chat/
├── ChatView.vue              # 页面容器，<200 行
├── components/
│   ├── SessionList.vue       # 左侧会话列表
│   ├── ChatHeader.vue        # 顶部（模型选择、系统Prompt）
│   ├── MessageList.vue       # 消息列表滚动区
│   ├── MessageBubble.vue     # 单条消息气泡
│   ├── StreamingText.vue     # 流式文字渲染
│   ├── ChatInput.vue         # 输入框+发送
│   ├── ReviewResult.vue      # 评审结果展示卡片
│   └── GeneratedCases.vue    # 生成用例预览+操作
├── composables/
│   ├── useChat.ts            # 会话管理逻辑
│   ├── useSSE.ts             # SSE 流式连接
│   └── useAutoScroll.ts      # 自动滚动
└── types.ts                  # 类型定义
```

---

## 七、关键技术实现要点

### 7.1 文档解析策略

```python
# 不引入过重的 unstructured，用轻量方案
async def parse_document(file_path: str, file_type: str) -> ParseResult:
    if file_type == "docx":
        from docx import Document
        doc = Document(file_path)
        sections = []
        for para in doc.paragraphs:
            if para.style.name.startswith("Heading"):
                sections.append({"type": "heading", "level": int(para.style.name[-1]), "text": para.text})
            elif para.text.strip():
                sections.append({"type": "paragraph", "text": para.text})
        # 表格也要提取
        for table in doc.tables:
            ...
        return ParseResult(text=full_text, structured=sections)

    elif file_type == "pdf":
        from pypdf import PdfReader
        ...
```

### 7.2 SSE 流式实现（后端）

```python
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import json

router = APIRouter()

@router.post("/chat/sessions/{session_id}/send")
async def send_message(session_id: str, body: SendMessageSchema, user=Depends(get_current_user)):
    session = await chat_service.get_session(session_id, user.id)
    messages = await chat_service.build_context(session, body.content)

    async def event_stream():
        msg_id = str(uuid4())
        yield f"event: start\ndata: {json.dumps({'message_id': msg_id})}\n\n"

        full_content = ""
        async for chunk in llm_provider.chat_stream(messages):
            full_content += chunk
            yield f"event: delta\ndata: {json.dumps({'content': chunk})}\n\n"

        # 保存完整消息到数据库
        await chat_service.save_message(session_id, "assistant", full_content)
        yield f"event: done\ndata: {json.dumps({'message_id': msg_id})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

### 7.3 SSE 流式实现（前端）

```typescript
// composables/useSSE.ts
export function useSSE() {
  const isStreaming = ref(false)

  async function streamChat(sessionId: string, content: string, onDelta: (text: string) => void) {
    isStreaming.value = true
    const response = await fetch(`/api/chat/sessions/${sessionId}/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ content }),
    })

    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = JSON.parse(line.slice(6))
          if (data.content) onDelta(data.content)
        }
      }
    }
    isStreaming.value = false
  }

  return { isStreaming, streamChat }
}
```

### 7.4 权限中间件

```python
# backend/app/core/permissions.py
from functools import wraps
from fastapi import HTTPException, Depends

def require_permission(*permissions: str):
    """装饰器：检查用户是否拥有指定权限"""
    async def checker(user=Depends(get_current_user)):
        if user.is_superuser:
            return user
        user_permissions = await get_user_permissions(user.id)
        for perm in permissions:
            if perm not in user_permissions:
                raise HTTPException(403, f"缺少权限: {perm}")
        return user
    return Depends(checker)

# 使用方式
@router.post("/projects")
async def create_project(body: CreateProjectSchema, user=require_permission("project:create")):
    ...
```

---

## 八、UI/UX 设计原则

### 8.1 页面布局

```
┌─────────────────────────────────────────────────────────────┐
│  Logo    [项目选择器▼]              [主题] [通知] [头像▼]    │
├────────┬────────────────────────────────────────────────────┤
│        │                                                    │
│  侧栏   │              内容区                                │
│        │                                                    │
│ 📊 概览 │                                                    │
│ 💬 对话 │    （对话页面时，左侧为会话列表，右侧为对话区）      │
│ 📋 需求 │                                                    │
│ 🧪 用例 │                                                    │
│ ⚙️ 设置 │                                                    │
│        │                                                    │
├────────┴────────────────────────────────────────────────────┤
│                       (无 footer)                            │
└─────────────────────────────────────────────────────────────┘
```

### 8.2 关键交互优化（对比 WHartTest）

| 场景 | WHartTest 操作路径 | 新平台操作路径 |
|------|-------------------|---------------|
| AI 评审需求 | 上传文档 → 进入详情 → 点评审 → 等待 → 查看报告 | 上传文档后自动提示"是否立即评审"；或在对话中直接说"评审最新需求" |
| 生成测试用例 | 需要找到对应功能入口 → 配置参数 → 等待 | 需求详情页一键"生成用例"；或对话中说"根据XX需求生成用例" |
| 切换 LLM | 进入配置页 → 修改 → 保存 → 回到对话 | 对话顶部直接下拉选择模型 |
| 查看用例 | 需要切页面 → 筛选 → 查看 | 对话中 AI 回复直接内嵌可操作的用例卡片 |

### 8.3 对话中的富交互

AI 的回复不是纯文本，而是可以包含**交互卡片**：

- **评审结果卡片**：分数、问题列表、一键查看详情
- **生成用例卡片**：用例预览、"接受全部"/"逐条审查"按钮
- **确认卡片**：AI 不确定时请求用户确认

---

## 九、实现步骤规划

### 第一阶段：基础框架搭建（3-4 天）

| 步骤 | 任务 | 产出 |
|------|------|------|
| 1.1 | 初始化后端项目（FastAPI + SQLAlchemy + Alembic） | 可运行的空项目骨架 |
| 1.2 | 初始化前端项目（Vue 3 + Naive UI + UnoCSS） | 可运行的空项目骨架 |
| 1.3 | Docker Compose 编排（3 容器） | `docker compose up` 一键启动 |
| 1.4 | 实现统一响应格式、异常处理、日志 | 后端基础中间件就绪 |
| 1.5 | 实现前端 HTTP 封装、路由守卫、布局 | 前端基础框架就绪 |

### 第二阶段：用户认证与权限（2-3 天）

| 步骤 | 任务 | 产出 |
|------|------|------|
| 2.1 | 用户注册/登录 API + JWT | 认证流程 |
| 2.2 | RBAC 角色权限模型 | 角色/权限表 + CRUD API |
| 2.3 | 前端登录页 + Token 管理 | 可登录 |
| 2.4 | 前端权限指令 + 菜单控制 | v-permission 指令 |
| 2.5 | 用户管理页面（列表/编辑/角色分配） | 管理界面 |

### 第三阶段：项目管理（1-2 天）

| 步骤 | 任务 | 产出 |
|------|------|------|
| 3.1 | 项目 CRUD API + 成员管理 | API 就绪 |
| 3.2 | 前端项目列表/创建/设置/成员管理 | 项目功能完整 |
| 3.3 | 全局项目选择器 + 路由守卫 | 项目上下文 |

### 第四阶段：LLM 配置与对话（3-4 天）

| 步骤 | 任务 | 产出 |
|------|------|------|
| 4.1 | LLM 配置 CRUD + 连通性测试 API | 配置管理 |
| 4.2 | LLM Provider 统一抽象层 | 支持多供应商 |
| 4.3 | 对话 API + SSE 流式 | 对话后端完整 |
| 4.4 | 前端 LLM 配置管理页 | 配置界面 |
| 4.5 | 前端对话页面（会话列表 + 消息 + 流式渲染） | **核心交互** |
| 4.6 | 停止生成、重新生成、上下文管理 | 对话体验完善 |

### 第五阶段：需求文档与 AI 评审（3-4 天）

| 步骤 | 任务 | 产出 |
|------|------|------|
| 5.1 | 文件上传 API + 文档解析 | 支持 Word/PDF |
| 5.2 | 需求文档 CRUD + 列表 | 文档管理 |
| 5.3 | AI 评审 Prompt 设计与调优 | 评审能力 |
| 5.4 | 评审 API（多维度评审 + 结果存储） | 评审后端 |
| 5.5 | 前端需求列表/上传/详情/评审结果展示 | 需求功能完整 |
| 5.6 | 对话中触发评审（"评审一下这个文档"） | AI 原生交互 |

### 第六阶段：测试用例与 AI 生成（3-4 天）

| 步骤 | 任务 | 产出 |
|------|------|------|
| 6.1 | 用例模块树 + 用例 CRUD API | 用例管理后端 |
| 6.2 | AI 用例生成 Prompt + 结果解析 | 生成能力 |
| 6.3 | 用例生成 API（流式输出 + 批量确认） | 生成后端 |
| 6.4 | 前端用例管理（树形模块 + 列表 + 详情编辑） | 用例管理界面 |
| 6.5 | 前端 AI 生成用例（预览 + 接受/修改/拒绝） | **核心功能** |
| 6.6 | 对话中生成用例（"根据这个需求生成用例"） | AI 原生交互 |

### 第七阶段：集成与优化（2-3 天）

| 步骤 | 任务 | 产出 |
|------|------|------|
| 7.1 | 仪表盘数据统计 | 概览页 |
| 7.2 | 全局搜索 | 快速定位 |
| 7.3 | 操作日志 | 可追溯 |
| 7.4 | 性能优化（虚拟滚动、懒加载） | 大数据量下的体验 |
| 7.5 | 部署文档 + 初始化脚本 | 可交付 |

### 总计：约 17-24 天（单人开发）

---

## 十、关键设计决策说明

### 10.1 为什么选 FastAPI 而非 Django？

| 对比项 | Django (WHartTest) | FastAPI (新平台) |
|--------|-------------------|-----------------|
| 异步支持 | 部分异步，需 Channels 辅助 | 原生全异步 |
| AI 流式 | 需额外封装 StreamingHttpResponse | SSE/WebSocket 一等公民 |
| 类型安全 | 运行时验证 (serializers) | 编译时 + 运行时 (Pydantic) |
| API 文档 | 需 drf-spectacular 插件 | 自动生成 OpenAPI |
| 学习曲线 | 约定多、概念重 | 简单直接 |
| 性能 | 中等（WSGI 为主） | 高（ASGI 原生） |
| ORM | Django ORM（同步为主） | SQLAlchemy 2.0（异步优先） |

### 10.2 为什么不用 LangChain/LangGraph？

WHartTest 引入了完整的 LangChain 生态，导致：
- 依赖数量爆炸（几十个 langchain-* 包）
- 抽象层过多，调试困难
- 版本兼容性问题频发
- 对于"对话 + 评审 + 生成用例"场景其实用不到复杂 Agent 编排

**新平台方案**：直接使用 OpenAI SDK 兼容协议，一个 SDK 调用所有兼容供应商（DeepSeek、通义千问、Ollama 等都支持）。简单、可控、依赖少。

### 10.3 为什么用 RBAC 而非 Django 模型权限？

WHartTest 使用 Django 的 `auth.Permission`（基于模型 + 操作），需要为每张表配置 view/add/change/delete，管理复杂且不直观。

新平台使用**自定义 RBAC**：
- 权限是一组有业务含义的字符串（如 `testcase:generate`）
- 角色是权限集合
- 用户可有多个角色
- 前端可据此精确控制 UI 元素显隐
- 管理者看得懂、配得明白

### 10.4 第二期可扩展方向

- **UI 自动化**：录制/回放（可复用 WHartTest 的 Actuator 架构）
- **MCP 工具集成**：调用外部工具
- **知识库 RAG**：向量检索辅助更精准的用例生成
- **CI 集成**：对接 Jenkins/GitLab CI
- **缺陷跟踪**：与 Jira/飞书等联动
- **数据看板**：测试覆盖率、质量趋势

---

## 十一、与 WHartTest 可复用部分

以下 WHartTest 的设计/代码可以参考或直接复用：

1. **前端 HTTP 封装模式**（`request.ts` 的 Token 刷新队列逻辑）
2. **统一响应渲染器**（`UnifiedResponseRenderer` 的思路）
3. **项目成员权限模型**（项目维度的 owner/admin/member 分层）
4. **Prompt 模板管理**的数据结构设计
5. **Docker 多阶段构建**的 Dockerfile 写法
6. **CI/CD 流程**（GitHub Actions 构建镜像 + 部署）

---

## 十二、技术风险与规避

| 风险 | 影响 | 规避措施 |
|------|------|----------|
| AI 输出格式不稳定 | 用例生成/评审结果解析失败 | JSON Schema 约束 + 重试 + 容错解析 |
| 长文档 Token 超限 | 无法一次性分析完整文档 | 分段策略：按章节切分 → 分别分析 → 汇总 |
| LLM API 延迟高 | 用户体验差 | 流式输出 + 加载骨架屏 + 超时提示 |
| 权限设计过简 | 后期不够用 | 预留扩展点（项目级权限覆盖） |
| 前后端联调成本 | 开发效率低 | OpenAPI 自动生成前端类型（openapi-typescript） |

---

*文档版本：v1.0*
*最后更新：2026-04-29*
