# AITestPlatform

**AI 驱动的轻量测试管理平台 —— 让 AI 做重活，让人做决策。**

一站式覆盖 **需求评审 → 用例生成 → UI 自动化执行 → 报告分析** 的全链路；内置 LLM tool-calling 循环 + Playwright MCP，用自然语言描述用例、AI 自驱浏览器跑通业务，全程录屏 / 快照 / tool_call 可回放。

---

## 目录

- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [部署前提](#部署前提)
- [部署方案](#部署方案)
  - [方案 A：本地开发（前后端热更新）](#方案-a本地开发前后端热更新)
  - [方案 B：Docker 本地一键部署（推荐）](#方案-bdocker-本地一键部署推荐)
  - [方案 C：Linux 服务器部署](#方案-clinux-服务器部署)
  - [方案 D：被测系统在公司内网（VPN 场景）](#方案-d被测系统在公司内网vpn-场景)
    - [D-1：宿主机代理模式（macOS / Windows Docker Desktop）](#d-1宿主机代理模式macos--windows-docker-desktop)
    - [D-2：容器内 VPN sidecar 模式（不依赖宿主 VPN）](#d-2容器内-vpn-sidecar-模式不依赖宿主-vpn)
  - **方案 E：GHCR 拉取预构建镜像（最快，3-5 分钟部署）** → 详见 [`docs/DEPLOYMENT_GHCR.md`](docs/DEPLOYMENT_GHCR.md)
- [配置详解](#配置详解)
- [模块清单](#模块清单)
- [UI 自动化使用指南](#ui-自动化使用指南)
- [实时画面（noVNC）](#实时画面novnc)
- [运维常用命令](#运维常用命令)
- [排错速查](#排错速查)
- [开发与贡献](#开发与贡献)
- [路线图](#路线图)
- [进一步阅读](#进一步阅读)

---

## 核心特性

### 一期：测试管理 + AI 助手

| 模块 | 能力 | 优势 |
|---|---|---|
| **需求文档管理** | Word / PDF / Markdown 上传，AI 自动评审、抽取关键点、给改进建议 | 替代手工通读全文 |
| **测试用例管理** | 模块树组织、增删改查、Excel 导入导出 | 与项目 / 模块解耦的多对多结构 |
| **AI 用例批量生成** | 基于需求文档 + 系统提示词流式生成；支持中断 / 续接 | 一次生成数十条用例，token 预算自动控制 |
| **AI 智能对话** | 流式 SSE，自动识别"评审"/"生成"意图并触发后台任务；支持多会话、文件附件 | 用户用自然语言完成几乎所有操作 |
| **多 LLM 支持** | OpenAI 协议兼容（DeepSeek / 通义 / Ollama / GPT 等）；支持平台内多 Provider 配置切换 | 不绑定单一供应商 |
| **提示词管理** | 系统模板 + 自定义模板；按分类自动注入对话；版本号 + 历史回滚 | 提示词变更可追溯 |
| **项目 / 角色 / 用户** | 多项目隔离；RBAC 角色（admin / member / viewer）；项目成员 + 全局权限矩阵 | 简单清晰，覆盖小团队所有场景 |
| **数据仪表盘** | 项目进度、用例覆盖、AI 活动、UI 自动化双视图通过率（业务/执行/任务） | 单页一览所有运营指标 |

### 二期：UI 自动化

| 模块 | 能力 | 优势 |
|---|---|---|
| **执行环境** | URL / 浏览器配置 / 前置步骤模板（http_login / ai_login / state_inject）；登录态 storage_state 自动复用 | 一次配置，N 次复用 |
| **测试物料体系** | 6 种类型（string / secret / multiline / file / random / dataset）× 5 级层级（项目默认 / 环境 / 用例 / 个人 / 一次性覆盖） | 解决"用例只描述做什么、缺少具体数据"的真实痛点 |
| **AI 自驱执行** | LLM tool-calling 循环 + Playwright MCP；每步 accessibility 快照 + diff 增量；token 预算守卫 | 非 selector，靠语义定位元素，对页面 DOM 重构强健 |
| **三层数据可信度** | reliable（真实物料）/ synthesized（AI 自造）/ data_failure；业务通过率自动排除"数据问题导致的失败" | 区分"功能问题"与"测试数据问题" |
| **批量执行 + 用例间状态隔离** | 批量任务在每条用例之间执行 `reset_for_next_case`：关闭多余 page、回到 `about:blank`、保留登录态 | 避免上一条用例的弹窗 / 表单状态污染下一条 |
| **执行可观察性** | SSE 实时事件流；每步 snapshot before/after + tool_call 时间线；视频 + trace + 截图 | 失败现场可完整回放 |
| **实时画面（noVNC）** | 容器内 Xvfb + x11vnc + websockify，前端 iframe 直接看 chromium 实时画面 | 服务器部署也能"看见"AI 的浏览器操作 |
| **内网 VPN 兼容** | 双路代理（http_login 专用 + chromium 出口分别可控）；docker-compose.vpn.yml 一键开启 | 被测系统在公司内网时仍可用 |
| **自动清理 cron** | 视频 / 截图 / trace / storage_state / 物料 file 按保留天数自动回收 | 长期运行不爆盘 |

> **三期路线**：把一二期的"AI 主动操作"统一抽象为 Skill 体系（与 OpenClaw 协议对齐），支持自定义 skill 上传、触发词召回、Agent 自主调用。详见 [`docs/PHASE3_DESIGN.md`](docs/PHASE3_DESIGN.md)。

---

## 系统架构

### 容器拓扑

```
┌────────── User Browser ──────────┐
│ http://host           ws /novnc/ │
└─────────────┬────────────────────┘
              │
          (port 80)
              │
   ┌──────────┴──────────┐
   │  frontend (nginx)   │   ← 静态 SPA + /api/ 反代 + /novnc/ ws 反代
   │  Vue 3 + Naive UI   │
   └────────┬────────────┘
            │ /api/         /novnc/
       (port 8000)     (backend:6080)
            │
   ┌────────┴───────────────────────────────────┐
   │  backend (FastAPI / uvicorn 单 worker)      │
   │ ┌──────────────────────────────────────┐   │
   │ │  业务模块（auth/projects/llm/...)     │   │
   │ │  ChatStreamHub + ExecutionStreamHub  │   │
   │ │  ─────────────────────────────────   │   │
   │ │  Playwright MCP (Node 子进程)         │   │
   │ │  Chromium (有头 → Xvfb :99)           │   │
   │ │  Xvfb + x11vnc + websockify (:6080)  │   │
   │ │  Cleanup cron (asyncio task)         │   │
   │ └──────────────────────────────────────┘   │
   └────────┬───────────────────────────────────┘
            │
       (port 5432)
            │
   ┌────────┴─────────────┐
   │  PostgreSQL 16       │
   │  (named volume pgdata)│
   └──────────────────────┘
```

### 关键数据卷

| Volume | 容器路径 | 用途 | 是否被 nginx 暴露 |
|---|---|---|---|
| `pgdata` | DB 数据目录 | PostgreSQL 持久化 | 否 |
| `backend_uploads` | `/app/uploads` | 一期需求文档、向后兼容根挂载 | 否 |
| `test_data` | `/app/uploads/test-data` | 物料 file 类型的物理文件 | 否（走后端 reveal API） |
| `ui_artifacts` | `/app/uploads/ui_artifacts` | 视频 / trace / 截图 | 是（`/uploads/ui_artifacts/` 只读） |
| `ui_state` | `/app/uploads/ui_state` | BrowserContext storage_state（含登录 cookie） | 否（容器内 chmod 700） |

> 子挂载顺序很重要：父挂载（`backend_uploads`）在前，子挂载（`test_data` / `ui_artifacts` / `ui_state`）在后。Docker 会让子挂载覆盖父挂载里同路径的目录，**反过来则父挂载会把子挂载吞掉**。

### 端口

| 端口 | 服务 | 是否对外暴露 | 配置项 |
|---|---|---|---|
| 80 | frontend nginx | 是（生产 SPA + API 反代） | — |
| 8000 → host:`${BACKEND_PORT}` | backend uvicorn | 是（开发 / 直接调 API 用） | `.env` 里 `BACKEND_PORT=7008` 改宿主端口（容器内固定 8000） |
| 5432 → host:`${POSTGRES_PORT}` | PostgreSQL | 默认暴露（生产可关，仅留容器网络） | `.env` 里 `POSTGRES_PORT` |
| 6080 | websockify (noVNC) | **否**（仅容器网络，前端经 `/novnc/` 反代） | — |
| 5173 | vite dev server | 仅本地开发 | — |

> **端口冲突时怎么改？** 后端宿主端口在 `docker-compose.yml` 里写成 `${BACKEND_PORT:-8000}:8000`，
> 容器内部仍是 8000（前端 nginx 通过 docker 网络反代 `backend:8000`，不受宿主端口影响）。
> 服务器上若 8000 已被占用，只需在 `.env` 加一行 `BACKEND_PORT=7008`，
> 然后 `docker compose up -d backend` 重建容器即可，无需改任何代码。

---

## 技术栈

| 层 | 选型 | 关键考量 |
|---|---|---|
| **后端框架** | FastAPI 0.115+ | 原生异步、自动 OpenAPI、SSE 友好 |
| **ORM** | SQLAlchemy 2.0（async） + Alembic | 类型驱动、迁移可控 |
| **数据库** | PostgreSQL 16 | 成熟、JSONB / 全文检索、唯一外键 / 约束完整 |
| **认证** | JWT（python-jose）+ bcrypt | 无状态、可平移 |
| **加密** | Fernet（cryptography） | 密码 / API key / 物料 secret 列加密 |
| **AI 调用** | OpenAI SDK 2.x | 通用协议，可对接 DeepSeek / 通义 / Ollama 等 |
| **浏览器自动化** | Playwright 1.59+ + `@playwright/mcp` | LLM tool-calling 直接驱动 chromium |
| **OCR（验证码）** | ddddocr | 全离线、无外网依赖 |
| **文档解析** | python-docx / pypdf / antiword / catdoc | Word / PDF / 旧 .doc 全覆盖 |
| **远程画面** | Xvfb + x11vnc + websockify + noVNC | 容器内有头浏览器实时投屏 |
| **前端框架** | Vue 3.5 + TypeScript 5.6 + Vite 6 | 性能 + 类型安全 |
| **UI 组件库** | Naive UI 2.40 | 轻量、TS 原生、主题灵活 |
| **CSS** | UnoCSS + 设计 tokens 自定义 | 按需生成、无运行时 |
| **状态管理** | Pinia | Vue 官方推荐 |
| **HTTP 客户端** | ofetch | 体积小、原生 SSE |
| **包管理** | uv（后端）+ pnpm（前端） | 极速、严格 lockfile |
| **容器化** | Docker Compose | 三容器最小架构 |
| **进程模型** | uvicorn 单 worker | 内存内 ChatStreamHub / ExecutionStreamHub 不可跨进程；扩容需先迁移到 Redis pub/sub 或 PG LISTEN/NOTIFY |

---

## 项目结构

```
AITestPlatform/
├── docker-compose.yml          # 生产部署编排（默认）
├── docker-compose.dev.yml      # 开发数据库（仅 db）
├── docker-compose.vpn.yml      # VPN 场景 override（详见 §部署方案 D-1）
├── run.sh                      # 主命令入口（dev / up / down / install / db-* / test / lint / format ...）
├── Makefile                    # run.sh 的子集（兼容传统 make 用户）
├── scripts/init.sh             # 一键初始化（首次部署推荐）
├── .env.example                # 环境变量模板
├── docs/                       # 设计文档
│   ├── NEW_PLATFORM_DESIGN.md  # 一期总体设计
│   ├── IMPLEMENTATION_PLAN.md  # 一期实施计划
│   ├── PHASE2_DESIGN.md        # 二期 UI 自动化设计
│   ├── PHASE2_IMPLEMENTATION_PLAN.md
│   ├── PHASE3_DESIGN.md        # 三期 Skill 体系设计（路线图）
│   ├── PHASE3_IMPLEMENTATION_PLAN.md
│   └── PROMPT_MANAGEMENT_DESIGN.md
├── backend/                    # FastAPI 后端
│   ├── Dockerfile              # 含 Node + Chromium + Xvfb + noVNC
│   ├── entrypoint.sh           # 启动 Xvfb / x11vnc / websockify / 等待 DB / 迁移 / 建管理员
│   ├── pyproject.toml / uv.lock
│   ├── alembic.ini / alembic/  # 数据库迁移
│   └── app/
│       ├── main.py             # FastAPI 装载所有 router
│       ├── config.py           # Settings（Pydantic）
│       ├── database.py         # async session
│       ├── core/               # 通用：security / crypto / deps / exceptions
│       └── modules/
│           ├── auth/           # 登录、JWT、角色
│           ├── users/          # 用户 CRUD
│           ├── projects/       # 项目 + 成员 + 角色绑定
│           ├── requirements/   # 需求文档上传 / 解析 / 评审
│           ├── llm/            # LLM Provider 配置 + 对话 + 意图识别
│           ├── prompts/        # 提示词模板（系统/自定义/版本）
│           ├── testcases/      # 用例 + 模块树 + AI 生成
│           ├── dashboard/      # 项目维度统计（含 UI 双视图通过率）
│           ├── ui_automation/  # 二期：执行引擎 / 环境 / cleanup cron
│           ├── test_data/      # 二期：物料管理（6 种类型 × 5 级层级）
│           └── admin/          # 二期：超管 API（手动触发清理等）
└── frontend/                   # Vue 3 SPA
    ├── Dockerfile              # multi-stage：node 构建 → nginx 部署
    ├── nginx.conf              # SPA + /api/ 反代 + /novnc/ 反代 + 静态缓存
    ├── package.json / pnpm-lock.yaml
    └── src/
        ├── views/              # 页面（按业务域分组）
        ├── components/         # 组件
        ├── stores/             # Pinia
        ├── services/           # API 客户端
        ├── composables/        # useChat / useExecutionSSE / usePermission
        ├── router/             # 路由 + 守卫
        └── theme/              # NaiveUI 主题覆盖
```

---

## 部署前提

| 部署方式 | 必需 | 推荐版本 |
|---|---|---|
| **本地开发** | Docker（仅 DB） + Python 3.11 + Node 18+ + uv + pnpm | Docker Desktop 最新；Python 3.11；Node 20 LTS |
| **Docker 本地部署** | Docker 20.10+ + Compose v2 | Docker Desktop 4.30+ |
| **Linux 服务器部署** | Docker 20.10+ + Compose v2 | Ubuntu 22.04 / Debian 12 / RHEL 9 |
| **VPN 场景（D-1）** | 上面任一 + 宿主机已连接公司 VPN + 一个 HTTP 代理工具（pproxy / mitmproxy / tinyproxy 任一） | — |
| **VPN 场景（D-2）** | Linux 主机 + WireGuard 或 OpenVPN 配置文件 | Ubuntu 22.04+ |

最低硬件：

- CPU：2 核
- 内存：4 GB（Chromium + Node MCP 子进程吃 1-1.5 GB）
- 磁盘：10 GB（基础镜像 ~1 GB；视频 / trace 按 `UI_MEDIA_RETENTION_DAYS` 滚动）

---

## 部署方案

### 方案 A：本地开发（前后端热更新）

适合本地开发联调。数据库在容器里，前后端跑在宿主机。

```bash
# 1. 安装工具链
brew install uv node pnpm                # macOS
# Linux: curl -LsSf https://astral.sh/uv/install.sh | sh && nvm install 20 && npm i -g pnpm

# 2. 克隆 + 准备 env
git clone <repo-url> && cd AITestPlatform
cp .env.example .env

# 3. 安装依赖（首次执行）
./run.sh install
# 等价：cd backend && uv sync && cd ../frontend && pnpm install

# 4. 一键启动开发环境
./run.sh dev
# 自动完成：
#   - docker compose -f docker-compose.dev.yml up -d db   # 仅起 PostgreSQL
#   - 后端：uv run uvicorn app.main:app --reload  → :8000
#   - 前端：pnpm dev                              → :5173
```

访问：

| 服务 | 地址 |
|---|---|
| 前端（热更新） | http://localhost:5173 |
| 后端 API + Swagger | http://localhost:8000/docs |

> 默认管理员：`admin / admin123`，由 `backend/entrypoint.sh` 在容器**首次**启动时通过 inline Python 脚本创建（DB 中已存在 admin 时跳过）。本地开发模式下后端跑在宿主机、不走 entrypoint，**所以本地首次启动需要先建表 + 建管理员**：
>
> ```bash
> cd backend
>
> # 1. 建表（应用全部 alembic 迁移）
> uv run alembic upgrade head
>
> # 2. 建系统角色 + 默认 admin 用户
> #    复用 entrypoint.sh 里的同一段 Python；只在 admin 不存在时才插入
> uv run python -c "
> import asyncio, os
> from sqlalchemy import select, or_, insert
> from app.database import async_session_factory
> from app.modules.auth.models import User, Role, user_roles
> from app.modules.auth.init_data import init_roles
> from app.core.security import hash_password
>
> async def main():
>     await init_roles()
>     async with async_session_factory() as db:
>         exists = (await db.execute(
>             select(User).where(or_(User.username == 'admin', User.email == 'admin@aitest.local'))
>         )).scalar_one_or_none()
>         if exists:
>             print('admin already exists'); return
>         u = User(username='admin', email='admin@aitest.local',
>                  hashed_password=hash_password('admin123'),
>                  display_name='系统管理员', is_superuser=True, is_active=True)
>         db.add(u); await db.flush()
>         ar = (await db.execute(select(Role).where(Role.name == 'admin'))).scalar_one_or_none()
>         if ar:
>             await db.execute(insert(user_roles).values(user_id=u.id, role_id=ar.id))
>         await db.commit()
>         print('admin created: admin / admin123')
>
> asyncio.run(main())
> "
> ```
>
> 注意：`docker-compose.dev.yml`（开发用）与 `docker-compose.yml`（生产用）使用**不同的 PG named volume**（`pgdata_dev` vs `pgdata`），数据**不互通**。所以方案 A 模式下永远是从 `pgdata_dev` 起步的，第一次必须手动跑上面的两步。

数据库管理：

```bash
./run.sh db-migrate "add foo column"  # 生成迁移
./run.sh db-upgrade                    # 应用迁移
./run.sh db-reset                      # 重置（开发用，会清数据！）
```

---

### 方案 B：Docker 本地一键部署（推荐）

最常用方式。三个容器（db / backend / frontend），一行命令启动。

#### B-1：自动化（推荐首次部署）

```bash
git clone <repo-url> && cd AITestPlatform

bash scripts/init.sh
# 脚本会自动完成：
#   1. 检查 docker / docker compose 可用
#   2. 从 .env.example 复制 .env，并生成随机 SECRET_KEY
#   3. docker compose build         （首次约 5–10 分钟，含 Chromium）
#   4. docker compose up -d
#   5. 健康检查 /api/health 直到就绪
```

完成后：

| 服务 | 地址 |
|---|---|
| 前端 | http://localhost |
| 后端 Swagger | http://localhost:8000/docs（端口默认 8000，由 `.env` 的 `BACKEND_PORT` 控制） |

默认管理员：`admin / admin123`，**首次登录后立即修改！**

> **服务器上 8000 已被占用？** 在 `.env` 里加 `BACKEND_PORT=7008`（或其它空闲端口），
> 然后重启即可：`docker compose up -d backend`。容器内 uvicorn 仍监听 8000，
> 前端 nginx 反代不受影响（详见上文 [端口](#端口) 章节）。

#### B-2：手动逐步（看清楚每一步）

```bash
git clone <repo-url> && cd AITestPlatform

# 1. 准备 .env
cp .env.example .env
# 修改：SECRET_KEY / POSTGRES_PASSWORD / ADMIN_PASSWORD

# 2. （可选）生成 ENCRYPT_KEY；不设置则用 config.py 的开发默认值
python -c "from cryptography.fernet import Fernet; print('ENCRYPT_KEY=' + Fernet.generate_key().decode())" >> .env

# 3. 构建镜像
docker compose build

# 4. 启动
docker compose up -d

# 5. 看日志确认 backend 就绪
docker compose logs -f backend
# 看到 "Uvicorn running on http://0.0.0.0:8000" 即可（这是容器内端口，不变）

# 6. 健康检查（宿主机端口默认 8000；若 .env 里改了 BACKEND_PORT 就用新端口）
curl http://localhost:${BACKEND_PORT:-8000}/api/health
# {"status":"ok","service":"AITestPlatform"}
```

#### B-3：升级与重启

```bash
git pull
docker compose build                  # 重新构建
docker compose up -d                  # 增量重启（仅变化的服务）

# 仅重建 frontend（改 nginx.conf / Vue 代码常用）
docker compose up -d --build frontend

# 仅重建 backend（改 Python 代码常用）
docker compose up -d --build backend
```

> **关键坑**：`docker compose up -d --build frontend` 也会顺便 recreate 它依赖的 backend 容器（`depends_on`）。如果你刚刚通过 vpn override 启动过 backend，这次普通命令会把 vpn override 的环境变量清空。带 override 时**每次都要带全文件参数**：
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.vpn.yml up -d backend
> ```

---

### 方案 C：Linux 服务器部署

与方案 B 几乎相同，但有几个生产化要点。

#### 生产化清单

```bash
# 1. 安装 Docker（Ubuntu / Debian）
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# 2. 拷贝项目
scp -r AITestPlatform user@server:/opt/
ssh user@server
cd /opt/AITestPlatform

# 3. 准备生产 .env（强密码、关 DEBUG）
cp .env.example .env
vi .env
# 必改：
#   SECRET_KEY=$(openssl rand -base64 48)
#   ENCRYPT_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
#   POSTGRES_PASSWORD=<强密码>
#   ADMIN_PASSWORD=<强密码>
#   DEBUG=false

# 4. 启动
docker compose up -d --build

# 5. （可选）反向代理：在 nginx 前再套一层 Caddy / Traefik 加 HTTPS
```

#### 关闭对外 5432 端口（生产建议）

`docker-compose.yml` 默认把 PostgreSQL 5432 暴露到宿主机，方便本地连数据库排查。生产环境建议关掉：

```yaml
# docker-compose.yml
services:
  db:
    ports: []         # ← 注释或删除原 5432 行；不写 ports 即不对外
```

backend 容器仍可经容器网络访问 `db:5432`，无影响。

#### 系统服务化（开机自启）

`docker compose up -d` 加 `restart: unless-stopped` 已能自动重启。如要更严格的开机启动，写一份 systemd unit：

```ini
# /etc/systemd/system/aitest.service
[Unit]
Description=AITestPlatform
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/AITestPlatform
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now aitest
```

#### 资源约束

如服务器同时跑别的服务，给 backend 容器加资源上限（chromium 偶发吃内存）：

```yaml
# docker-compose.override.yml（与 docker-compose.yml 自动叠加）
services:
  backend:
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 3G
```

---

### 方案 D：被测系统在公司内网（VPN 场景）

> **现象**：宿主机 `curl https://你的内网/login` HTTP=200，但 `docker exec backend curl ...` 直接 ConnectTimeout。
>
> **根因**：被测域名解析到 RFC1918 内网地址（如 `172.17.x.x`），而 macOS Docker Desktop / Windows WSL 的容器跑在独立的 Linux VM 里，**这个 VM 不共享宿主的 VPN 路由表**。Linux 原生 Docker 在 `network_mode: host` 下没这个问题。

提供两种解法：D-1 让容器借宿主机 VPN（最常用），D-2 让容器自己建 VPN（最干净）。

#### D-1：宿主机代理模式（macOS / Windows Docker Desktop）

让容器把所有"访问内网"的流量经一个**跑在宿主机上的 HTTP 代理**出去；该代理进程持有宿主机的 VPN 路由，自然能命中内网。

```
┌─ container ─┐    ┌─── macOS host ───┐     ┌─ 公司 VPN ─┐
│ chromium    │───>│ pproxy:8118      │────>│ utun ...   │───> 内网
│ httpx       │    │ (持有 utun 路由) │     └────────────┘
└─────────────┘    └──────────────────┘
   通过 host.docker.internal:8118
```

**步骤一：在宿主机起一个 HTTP 代理（任选其一）**

```bash
# 方案 1：pproxy（一行 pip，零配置，推荐）
pip install pproxy
pproxy -l http://0.0.0.0:8118 &

# 方案 2：mitmproxy（功能多，能抓包）
pip install mitmproxy
mitmdump --listen-host 0.0.0.0 --listen-port 8118 &

# 方案 3：tinyproxy（brew 装，配置简单）
brew install tinyproxy
cat >/tmp/tinyproxy.conf <<EOF
Listen 0.0.0.0
Port 8118
Allow 127.0.0.1
Allow 192.168.65.0/24
EOF
tinyproxy -c /tmp/tinyproxy.conf
```

**步骤二：宿主机自验证（一定要做）**

```bash
curl --proxy http://localhost:8118 -sSI https://你的内网域名/api/health
# 必须返回 200；否则代理本身就不通，下面没意义
```

**步骤三：启动 backend 时叠加 vpn override**

```bash
docker compose -f docker-compose.yml -f docker-compose.vpn.yml up -d backend
```

`docker-compose.vpn.yml` 自动注入：

```yaml
UI_HTTP_LOGIN_PROXY=http://host.docker.internal:8118    # backend 走 http_login 时的专用代理
UI_BROWSER_PROXY=http://host.docker.internal:8118       # chromium 启动时透传给 --proxy-server
HTTP_PROXY=http://host.docker.internal:8118             # backend 其它出口（含 LLM）也走这条
HTTPS_PROXY=http://host.docker.internal:8118
NO_PROXY=localhost,127.0.0.1,host.docker.internal,db,backend,frontend
```

> **关键坑 1**：`UI_HTTP_LOGIN_PROXY` 是必填项，不能只设 `HTTP_PROXY` —— backend 的 http_login 模块用 `httpx(trust_env=False)` 主动忽略 `HTTP_PROXY`（避免污染 LLM 调用），必须显式声明。
>
> **关键坑 2**：`UI_BROWSER_PROXY_BYPASS` 必须包含 `localhost,127.0.0.1,host.docker.internal,db,backend,frontend`，否则 chromium 经代理回访自身 / 数据库时会断。
>
> **关键坑 3**：split-tunnel VPN（公网不走 VPN）下，`HTTP_PROXY=` / `HTTPS_PROXY=` 这两行可能让 LLM 调用变慢甚至失败 —— 因为 LLM 在公网，反而被代理回旋。这种情况下：删掉 `docker-compose.vpn.yml` 里的 `HTTP_PROXY/HTTPS_PROXY`，只保留 `UI_HTTP_LOGIN_PROXY` + `UI_BROWSER_PROXY`。

**步骤四：容器内自验证**

```bash
docker compose exec backend python -c "
import httpx, time, os
url = 'https://你的内网域名/api/health'
t = time.time()
r = httpx.get(url, timeout=8, proxy=os.getenv('UI_HTTP_LOGIN_PROXY'), trust_env=False)
print('OK', r.status_code, 'in', round(time.time()-t,2), 's')
"
# 期望：OK 200 in 0.3 s
```

**切回非 VPN 模式**

```bash
docker compose up -d backend     # 不带 -f vpn 即可，env 自动清空
```

> Linux 原生 Docker 不需要 D-1，直接用 `network_mode: host` 即可（宿主和容器共享网络栈）。在 `docker-compose.yml` 加 `network_mode: host` 给 backend 即生效（同时 db 和 frontend 互通方式略变，详细配置自行评估）。

#### D-2：容器内 VPN sidecar 模式（不依赖宿主 VPN）

服务器场景或希望"容器自带 VPN，不依赖宿主 OS 配置"时的方案。把 VPN 客户端跑在一个独立容器里，让 backend 容器**完全使用 VPN 容器的网络栈**。

```
┌───── docker network ─────┐
│                          │
│  ┌──────── vpn ────────┐ │     ┌─ 公司 VPN 服务端 ─┐
│  │ wireguard / openvpn │─┼───>│  (.conf / .ovpn) │
│  └─────────────────────┘ │     └──────────────────┘
│           ▲              │
│ network_mode: container:vpn
│           │              │
│  ┌──── backend ─────┐    │
│  │ chromium / httpx │    │   ← 出方向流量被 vpn 容器接管
│  └──────────────────┘    │
└──────────────────────────┘
```

**步骤一：准备 VPN 配置**

得到管理员发的 `.conf`（WireGuard）或 `.ovpn`（OpenVPN）配置文件，放到 `vpn/` 目录。

**步骤二：在项目根目录新增 `docker-compose.sidecar-vpn.yml`**

WireGuard 版本（最简）：

```yaml
# docker-compose.sidecar-vpn.yml —— 与 docker-compose.yml 叠加使用
services:
  vpn:
    image: lscr.io/linuxserver/wireguard:latest
    cap_add:
      - NET_ADMIN
      - SYS_MODULE
    sysctls:
      - net.ipv4.conf.all.src_valid_mark=1
    volumes:
      - ./vpn:/config             # 把 .conf 放在 ./vpn/wg_confs/
      - /lib/modules:/lib/modules:ro
    environment:
      - PUID=1000
      - PGID=1000
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "wg", "show"]
      interval: 30s

  backend:
    network_mode: "service:vpn"   # ← 关键：完全共享 vpn 容器的网络命名空间
    depends_on:
      vpn:
        condition: service_started
      db:
        condition: service_healthy
    # 注意：当使用 network_mode: service:xxx 时，本服务自身不能再声明 ports。
    # backend 的 8000 端口要由 vpn 容器代为暴露：
    ports: !reset []
  
  vpn:
    ports:
      - "${BACKEND_PORT:-8000}:8000"   # backend 的 API 端口（宿主机端口随 .env 走）
      # 6080 不暴露（前端经容器网络反代）
```

> 注意：`network_mode: service:vpn` 让 backend 完全没有自己的网络栈，**它的 `ports`、`networks`、`extra_hosts` 都不能再写，要写在 vpn 容器上**。

OpenVPN 版本（用 `kylemanna/openvpn` 或 `dperson/openvpn-client`）：

```yaml
services:
  vpn:
    image: dperson/openvpn-client:latest
    cap_add: [NET_ADMIN]
    devices: ["/dev/net/tun"]
    volumes:
      - ./vpn/client.ovpn:/vpn/client.ovpn:ro
    command: -f "" -r 192.168.0.0/16 -r 10.0.0.0/8 -r 172.16.0.0/12   # 推送内网网段路由
    restart: unless-stopped
    ports:
      - "8000:8000"

  backend:
    network_mode: "service:vpn"
    ports: !reset []
    depends_on: [vpn, db]
```

**步骤三：启动**

```bash
docker compose -f docker-compose.yml -f docker-compose.sidecar-vpn.yml up -d
```

**步骤四：验证 VPN 隧道与连通性**

```bash
# 1. VPN 容器握手
docker compose logs vpn | tail
# WireGuard 看到 "interface created"；OpenVPN 看到 "Initialization Sequence Completed"

# 2. backend 容器（实际是 vpn 容器的网络栈）能否访问内网
docker compose exec backend curl -sS -o /dev/null -w 'HTTP=%{http_code}\n' \
    --max-time 8 https://你的内网域名/api/health
# 期望：HTTP=200
```

**取舍**

| 维度 | D-1 宿主机代理 | D-2 容器内 VPN |
|---|---|---|
| 适用平台 | macOS Docker Desktop、Windows WSL | Linux 原生 Docker |
| VPN 客户端在哪 | 宿主机 OS 已经连接 | 容器里跑 wireguard/openvpn-client |
| 是否需要 cap_add | 否 | 是（NET_ADMIN / SYS_MODULE / /dev/net/tun） |
| 容器走 VPN 范围 | 通过 `UI_*_PROXY` 精细控制 | 全部出方向流量都走 VPN |
| LLM 是否被影响 | 可控（只让 UI 部分走代理） | 默认全走，需要配 split-tunnel |
| 复杂度 | 低 | 中 |
| 推荐场景 | 个人开发联调内网应用 | 服务器长期运行、不依赖宿主 |

---

## 配置详解

`.env`（基于 `.env.example`）所有变量按域分组：

### 数据库

```bash
POSTGRES_HOST=localhost              # 本地开发；docker 部署不要改（compose 自动覆盖）
POSTGRES_PORT=5432
POSTGRES_USER=aitest
POSTGRES_PASSWORD=aitest123          # 生产必改
POSTGRES_DB=aitest_platform
```

### 后端

```bash
SECRET_KEY=...                       # JWT 签名；生产必随机化（>=32 字节）
ENCRYPT_KEY=...                      # Fernet 32-byte url-safe base64 key；用于 secret 物料 / API key 加密
DEBUG=false                          # 生产 false：关闭 /docs，并禁用 LLM trace
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000                    # 宿主机映射端口（用户访问端口）；容器内 uvicorn 始终 8000
                                     # 服务器 8000 被占用时：BACKEND_PORT=7008
```

> **`ENCRYPT_KEY` 跨环境必须一致**。一旦换掉，所有已加密的物料 secret / LLM provider API key 将无法解密。生成命令：
> ```bash
> python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```

### 初始管理员

```bash
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123              # 生产必改
ADMIN_EMAIL=admin@aitest.local
```

> 仅在 `entrypoint.sh` 第一次运行（DB 中无 admin）时创建。改这些变量再启动**不会**修改已有用户密码 —— 改密码要从前端登录后操作。

### UI 自动化（二期）

```bash
# 物料文件 / 介质 / state 路径与上限
UI_STATE_DIR=uploads/ui_state
TEST_DATA_UPLOAD_DIR=uploads/test-data
TEST_DATA_MAX_FILE_SIZE=52428800     # 50 MB
UI_ARTIFACTS_DIR=uploads/ui_artifacts
UI_STEP_SCREENSHOT_TYPE=png          # png 清晰大 / jpeg 小失真

# Snapshot 裁剪（大 → LLM 看更全；小 → 省 token）
UI_SNAPSHOT_MAX_CHARS=3000
UI_SNAPSHOT_DIFF_CONTEXT=2

# 内网代理（VPN 场景；详见 §部署方案 D-1）
UI_HTTP_LOGIN_PROXY=                 # 仅 http_login 走它；空 = 关闭
UI_BROWSER_PROXY=                    # chromium 启动时透传给 --proxy-server
UI_BROWSER_PROXY_BYPASS=localhost,127.0.0.1,host.docker.internal,db,backend,frontend

# 实时画面（noVNC）
UI_NOVNC_ENABLED=true                # false 仅启 Xvfb（headed 仍可跑，但看不到画面）
UI_NOVNC_PORT=6080                   # 容器内端口；前端 nginx /novnc/ 反代过来
UI_VNC_DISPLAY=:99                   # Xvfb 显示器编号；改这条同步影响 chromium DISPLAY
```

### 清理 cron（Task 11.2）

```bash
CLEANUP_INTERVAL_HOURS=24            # 0 = 关闭周期清理（仅保留手动触发）
CLEANUP_RUN_ON_STARTUP=false         # 启动时是否立即跑一次

UI_MEDIA_RETENTION_DAYS=30           # 视频 / trace / 截图 / step screenshot
UI_STATE_RETENTION_DAYS=7            # 孤立 storage_state 文件
UI_SNAPSHOT_RETENTION_DAYS=7         # step 大字段（仅清空字段，行还在）
TEST_DATA_FILE_RETENTION_DAYS=90     # 物料 file 类型的孤立物理文件
TEST_DATA_AUDIT_RETENTION_DAYS=180   # 审计日志（预留）
```

### 前端

`.env.example` 里的 `VITE_API_BASE_URL` 是历史残留，**当前版本前端代码并不读它**：

- 本地开发模式：`vite.config.ts` 的 `server.proxy["/api"]` 把 `/api/*` 反代到 `http://localhost:8000`
  - 若本地把 `BACKEND_PORT` 改成了别的端口，需要同步修改 `vite.config.ts` 的 target，
    或临时把 `vite.config.ts` 改成读 env：`target: process.env.VITE_API_BASE_URL || 'http://localhost:8000'`
- 容器部署模式：`frontend/nginx.conf` 的 `location /api/` 反代到 `http://backend:8000/api/`
  - 这是 docker 内部网络，**不受 `BACKEND_PORT`（宿主机端口）影响**，无需修改

所以前端只调用相对路径 `/api/...`，不需要任何 env。

---

## 模块清单

### 后端 API（按模块）

| 模块 | 主要资源路径 | 主要能力 |
|---|---|---|
| `auth` | `/api/auth/*` | 登录、注册、刷新、修改密码、退出 |
| `users` | `/api/users/*` | 用户 CRUD + 个人资料 |
| `projects` | `/api/projects/*` | 项目 + 成员 + 角色绑定 |
| `requirements` | `/api/requirements/*` | 文档上传 / 解析 / 评审 / SSE 流式生成 |
| `llm` | `/api/llm-configs/*`（兼容 `/api/llm/*`） | LLM Provider 配置 |
| `chat` | `/api/chat/*` | AI 对话 SSE、会话管理、意图识别 |
| `prompts` | `/api/prompts/*`、`/api/projects/{id}/prompts/*` | 系统 / 自定义模板、版本管理 |
| `testcases` | `/api/testcases/*` | 用例 CRUD、模块树、AI 批量生成（流式） |
| `dashboard` | `/api/dashboard/*`、`/api/projects/{id}/ui-stats` | 项目维度指标聚合（含 UI 双视图 + 任务通过率） |
| `ui_automation` | `/api/ui-environments/*`、`/api/ui-preconditions/*`、`/api/ui-executions/*`、`/api/ui-automation/live-view/*`、`/api/projects/{id}/ui-environments/*`、`/api/projects/{id}/ui-executions/*` | 环境、前置步骤、执行、SSE 进度、live-view 状态、视频/trace/截图下载 |
| `test_data` | `/api/test-data-sets/*`、`/api/test-data-items/*`、`/api/projects/{id}/test-data-sets/*` | 物料集 / 物料 / 推荐 / 合并预览 / reveal |
| `admin` | `/api/admin/*` | 超管能力（手动触发清理 cron 等） |
| 健康检查 | `/api/health` | 不需鉴权 |

完整 API 文档：开发模式 `DEBUG=true` 下访问 `http://localhost:${BACKEND_PORT:-8000}/docs`（生产模式自动关闭）。

### 前端页面

```
/login                登录
/                     仪表盘（项目筛选）
/projects             项目列表 / 设置 / 成员
/requirements         需求列表 / 详情（评审）
/testcases            用例列表 / 模块树 / 详情 / AI 生成 / 执行 UI
/test-data            测试物料管理（物料集 + 条目 + 导入导出）
/chat                 AI 对话（多会话）
/ui-automation
  /environments       UI 执行环境
  /history            执行历史
  /executions/:id     执行详情（含视频 / trace / 时间线）
  /executions/:id/monitor   实时监控（SSE + 实时画面 noVNC）
/settings
  /llm                LLM 配置
  /prompts            提示词管理
  /users              用户管理
  /roles              角色管理
```

---

## UI 自动化使用指南

### 准备物料

1. 左侧菜单 `测试物料` → 选项目 → 创建物料集（如"登录账号-测试环境"）
2. 添加条目，按敏感度选类型：

| 类型 | 用途 | 加密 |
|---|---|---|
| `string` | 普通文本（用户名、邮箱、URL） | 否 |
| `secret` | 密码 / API key / token | Fernet 加密 |
| `multiline` | 多行文本 / JSON 配置 | 否 |
| `file` | 上传文件（如待签合同） | 否（文件本体） |
| `random` | 随机生成器（手机号、身份证、邮箱等） | 否 |
| `dataset` | 表格化数据集（CSV 导入） | 否 |

3. 物料集可绑定到环境（仅该环境跑时注入）或设为项目默认（每次执行自动加载）。

### 配置环境

1. `UI 自动化 → 环境列表` → 创建
2. 关键字段：
   - **Base URL**：被测系统首页
   - **Browser**：chromium / firefox / webkit
   - **Headless**：是否无头；服务器场景设 false 配合 noVNC 看画面
   - **前置步骤**：
     - `http_login`：直接调登录接口拿 token，最快
     - `ai_login`：让 AI 在登录页操作（复杂场景）
     - `state_inject`：注入预录制的 storage_state
   - **默认物料集**：每次执行自动加载

### 触发执行

进入 `测试用例` 页面 → 勾选要执行的用例 → 点击 `执行 UI 测试` → 选环境 / LLM / 物料集 → 开始。

> AI 对话内的「关键词驱动执行」入口已暂时移除（二期反馈不好用）；三期会通过 Skill 体系重新落地。当前阶段统一从用例列表触发。

### 看结果

- 执行监控页：实时 SSE 事件流；可开"实时画面"看 chromium 操作
- 执行历史：按时间倒序
- 详情页：业务/执行/任务三套通过率、step 时间线、tool_call 日志、视频 + trace + 截图下载

### 三套通过率口径

| 指标 | 计算 | 用途 |
|---|---|---|
| **业务通过率** | `passed / (total - data_failure)` | "假设数据是对的，功能本身通过率" |
| **执行通过率** | `passed / total` | 包含 data_failure 在内的整体通过率 |
| **任务通过率** | `任务级 succeeded / total` | 看 N 次重跑里有多少任务整体成功 |

仪表盘默认展示**任务通过率**（之前用户反馈两次失败仍 100% 即业务/执行口径，已优化为任务口径）。

---

## 实时画面（noVNC）

容器内有头浏览器（headless=false）的画面通过浏览器实时投出。

```
chromium ─→ Xvfb :99 ─→ x11vnc 5900 ─→ websockify 6080 ─→ nginx /novnc/ ─→ <iframe>
```

- 入口：执行监控页右上角 `实时画面` 按钮
- 关闭：再点该按钮、或抽屉右上角 `×`
- 鉴权：noVNC 端口仅容器网络可访问；前端经登录态保护的 nginx 反代过来；外部扫端口扫不到

> 已知不可关闭按钮 / 画面只显示顶部 150px 是因为旧代码漏 import `NDrawer` / `NDrawerContent`，导致 Vue 把标签作为未知元素直接放到 DOM 里。已在最新版本修复。

`UI_NOVNC_ENABLED=false` 时跳过 VNC 桥接，仍启动 Xvfb，chromium 仍能跑 headed，但看不到画面。

---

## 运维常用命令

```bash
# ── 服务生命周期 ──
docker compose up -d                       # 启动
docker compose down                         # 停止
docker compose restart backend              # 重启单服务
docker compose ps                           # 状态
docker compose logs -f backend              # 跟随日志
docker compose logs --tail=200 backend      # 最近 200 行

# ── 进容器排查 ──
docker compose exec backend bash
docker compose exec backend python -c "from app.config import settings; print(settings)"
docker compose exec db psql -U aitest aitest_platform

# ── 数据库迁移（容器外）──
docker compose exec backend alembic current
docker compose exec backend alembic history
docker compose exec backend alembic upgrade head

# ── 备份 / 恢复 ──
# 备份 PG
docker compose exec -T db pg_dump -U aitest aitest_platform | gzip > backup-$(date +%F).sql.gz
# 恢复（注意：会覆盖现有数据）
gunzip -c backup-2026-05-01.sql.gz | docker compose exec -T db psql -U aitest aitest_platform

# 备份卷数据
docker run --rm -v aitestplatform_ui_artifacts:/data -v $(pwd):/backup alpine \
    tar czf /backup/ui_artifacts-$(date +%F).tar.gz -C /data .

# ── 清理 cron 手动触发（超管 token）──
curl -X POST -H "Authorization: Bearer <admin-token>" http://localhost:${BACKEND_PORT:-8000}/api/admin/ui-media/cleanup
```

---

## 排错速查

按现象索引；每条给出根因和最简解法。

### 部署 / 启动

| 现象 | 根因 | 解法 |
|---|---|---|
| `docker compose build` 卡在 `playwright install` | 拉 Chromium 二进制慢（~300MB） | 等待，或预先 `docker pull mcr.microsoft.com/playwright/python:v1.59.0` 套层 base |
| `entrypoint.sh: chmod 700 ...: Operation not permitted` | volume 挂载点权限被宿主映射成 root，容器里非 root 改不了 | 不影响功能，可忽略；或 `volumes:` 加 `:Z`（SELinux）/ `:U`（rootless） |
| backend 启动卡在 "Waiting for database" 然后 30s 退出 | DB 容器没起来 / 健康检查失败 | `docker compose logs db`；检查端口冲突 5432 |
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | 服务器上 8000 已被其它项目占用 | `.env` 加 `BACKEND_PORT=7008`（或其它空闲端口），`docker compose up -d backend` 重建即可。容器内 uvicorn 仍是 8000，前端反代不受影响 |
| `alembic upgrade head` 报 `target database is not up to date` | 上次部署中断，`alembic_version` 表残留 | `docker compose exec backend alembic stamp head` 然后重启 |

### UI 自动化

| 现象 | 根因 | 解法 |
|---|---|---|
| `mcp_unavailable` | 容器内 Node 未装 / `@playwright/mcp` 缺 | 重新构建镜像（Dockerfile §1 §2 必须执行成功） |
| `chromium not found` / `Executable does not exist` | `playwright install chromium --with-deps` 没跑 | 重新构建镜像（Dockerfile §5） |
| 截图里中文显示成豆腐块 | 缺 CJK 字体 | 重新构建镜像（Dockerfile §3） |
| `TypeError: BrowserType.launch_persistent_context() got an unexpected keyword argument 'storage_state'` | Playwright 1.59+ 的 `launch_persistent_context` 不接受 `storage_state` | 升级到含 `_inject_storage_state_after_launch` 的版本（已修复） |
| 前置 `http_login` `ConnectTimeout` | 被测系统在内网，容器路由不可达 | 见 [§方案 D](#方案-d被测系统在公司内网vpn-场景) |
| 浏览器执行时 `ERR_CONNECTION_TIMED_OUT` | chromium 出口未走代理 | 设 `UI_BROWSER_PROXY` + `UI_BROWSER_PROXY_BYPASS` |
| 批量执行第二条用例直接接着上一条页面操作 | 没做用例间状态重置 | 已修复：`reset_for_next_case` 在每条用例开始前关闭多余 page、回到 `about:blank` |
| 媒体清理后视频还在磁盘上 | DB 路径已 NULL 但 named volume 不对应 | `docker volume inspect aitestplatform_ui_artifacts` 确认挂载点 |
| Secret 物料 reveal 报 "无法解密" | `ENCRYPT_KEY` 在不同环境之间不一致 | 所有部署使用同一个 `ENCRYPT_KEY` |
| 物料文件上传超 50MB | `TEST_DATA_MAX_FILE_SIZE` 默认 50MB | 调高 `.env` 或拆小文件 |

### 实时画面（noVNC）

| 现象 | 根因 | 解法 |
|---|---|---|
| 看不到"实时画面"按钮 | `UI_NOVNC_ENABLED=false`，或当前执行已结束（终态） | 设 true 并重启 backend；监控页只在执行中显示按钮 |
| 抽屉打开但只显示 150px 顶部 | 旧版本漏 import `NDrawer/NDrawerContent` | 升级到含修复的版本 |
| 抽屉打开后再点按钮无法关闭 | 旧版本按钮逻辑只单向 set true | 已改为 toggle |
| 抽屉里显示 "WebSocket connection failed" | `websockify` 未启动 / nginx `/novnc/` 反代配置错 | `docker compose logs backend \| grep websockify`；检查 nginx.conf 的 `^~ /novnc/` block |

### 前端 / 浏览器

| 现象 | 根因 | 解法 |
|---|---|---|
| 改完前端代码、强刷后页面没变化 | 浏览器还使用旧 `index.html` 引用旧 chunk hash | 已修复：nginx 给 `index.html` 加 `Cache-Control: no-cache, no-store, must-revalidate` |
| `<n-xxx>` 组件不生效（DOM 里残留 `<n-xxx>` 标签） | 该组件没在 `.vue` 文件 `<script>` 里 import | 在 `import { ... } from "naive-ui"` 中补上对应组件名（项目走显式 import 不是 auto-import） |
| 仪表盘 UI 自动化卡片不显示 | 未选项目 / 项目无执行记录 | 选项目；至少触发一次执行 |

### 网络 / VPN

| 现象 | 根因 | 解法 |
|---|---|---|
| 容器走 `host.docker.internal` 不通 | Linux 原生 Docker 默认无此别名 | `extra_hosts: ["host.docker.internal:host-gateway"]` |
| `挑战接口请求失败 (GET .../verification/getCode): ConnectTimeout` | 内网域 + 容器无代理 | 启用 D-1 或 D-2 |
| 走 ClashX 7890 报 `SSL UNEXPECTED_EOF` | ClashX 走公网节点，到不了 RFC1918 内网 | 不要用 ClashX 中转；用 D-1 的 pproxy 直接利用宿主 utun 路由 |

---

## 开发与贡献

### 代码风格

- Python：`ruff format` + `ruff check`（pyproject.toml 已配置）
- TypeScript / Vue：ESLint + Prettier
- 提交前自动化：

```bash
./run.sh lint && ./run.sh typecheck
```

### 数据库迁移

```bash
# 改完模型之后
./run.sh db-migrate "add foo column"     # 自动生成
# 检查 alembic/versions/<ts>_*.py 内容是否合理
./run.sh db-upgrade                       # 应用

# Docker 部署的服务器上：
docker compose exec backend alembic upgrade head
```

### 添加依赖

```bash
./run.sh add-backend openai              # 后端：自动改 pyproject + uv.lock
./run.sh add-frontend dayjs              # 前端：自动改 package.json + pnpm-lock
```

### 测试

```bash
./run.sh test                            # 全部
./run.sh test tests/ui_automation/ -v    # 指定路径
./run.sh test -k test_reset_for_next_case
```

### 常见误区（避免新成员踩坑）

1. **uvicorn 必须 `--workers 1`**：`ChatStreamHub` / `ExecutionStreamHub` 是进程内字典，多 worker 会让 SSE 订阅者落到另一个 worker 上，订到空 hub 后立刻收到 `done`。如要扩容先迁移到 Redis pub/sub。
2. **NaiveUI 是显式 import 模式，不是 auto-import**：每个 `.vue` 文件都得 `import { NXxx } from "naive-ui"` 才能用 `<n-xxx>`。漏掉时 prod 模式不报错，dev 模式有 console warning 但很容易忽略。
3. **加密的物料 / API key 用 `ENCRYPT_KEY`**：跨环境必须同步；忘了备份这个 key 等于丢失所有 secret。
4. **alembic 迁移要审一遍**：autogenerate 偶尔会漏 server_default 或乱加 drop。

---

## 路线图

| 阶段 | 状态 | 内容 |
|---|---|---|
| 一期 | 已完成 | 测试管理 + AI 助手（需求评审 / 用例生成 / 对话） |
| 二期 | 已完成 | UI 自动化（环境 / 物料 / 执行 / 报告 / 实时画面） |
| 三期 | 设计完成，待实施 | Skill 体系（与 OpenClaw 协议对齐）、关键词召回升级为 Lazy Tool 化 |
| Phase 11 增强 | 可选 | ARQ + Redis 异步任务队列；多 worker / 多副本部署 |

---

## 进一步阅读

| 文档 | 内容 |
|---|---|
| [`docs/NEW_PLATFORM_DESIGN.md`](docs/NEW_PLATFORM_DESIGN.md) | 一期总体设计（定位 / 技术栈 / 模块拆分 / 数据模型） |
| [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) | 一期分步实施计划 |
| [`docs/PHASE2_DESIGN.md`](docs/PHASE2_DESIGN.md) | 二期设计 v3.0（MCP 选型 / 物料体系 / 三层数据可信度 / Snapshot 治理） |
| [`docs/PHASE2_IMPLEMENTATION_PLAN.md`](docs/PHASE2_IMPLEMENTATION_PLAN.md) | 二期 Task 1–11 实施计划 |
| [`docs/PHASE3_DESIGN.md`](docs/PHASE3_DESIGN.md) | 三期 Skill 体系设计 |
| [`docs/PHASE3_IMPLEMENTATION_PLAN.md`](docs/PHASE3_IMPLEMENTATION_PLAN.md) | 三期实施计划 |
| [`docs/PROMPT_MANAGEMENT_DESIGN.md`](docs/PROMPT_MANAGEMENT_DESIGN.md) | 提示词管理子系统设计 |

---

## License

MIT
