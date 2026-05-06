# 二期部署影响清单（PHASE2_DEPLOYMENT_NOTES）

> 为什么这份文档：二期 UI 自动化引入了 **Python + Node + 浏览器二进制 + Redis（可选）** 多层运行时。如果到 Task 11.3 才一次性把这些塞进 Dockerfile，会出现"开发期能跑、部署期卡半天"的尴尬。本文档随每个 Task 的开发进度同步更新，作为 Task 11.3 / 11.4 的施工蓝图，也是新人接手时的"为什么 Dockerfile 长这样"答疑表。

> **维护原则**：每个改了部署相关文件 / 引入了新依赖的 task，都必须在本文档 ✅ 一项；Task 11.3 做的事就是把这里所有"⏳ 待集成"项一次性落到 Dockerfile / docker-compose / README / .env.example。

---

## 1. 运行时层栈总览

```
┌──────────────────────────────────────────────────────────────┐
│ backend 容器（python:3.11-slim 基底）                          │
│                                                              │
│  Python 3.11 venv (.venv)                                     │
│  ├─ fastapi / sqlalchemy / openai / ...（一期已具备）         │
│  ├─ mcp ≥1.27           ✅ Task 7.2 已加入 pyproject.toml      │
│  ├─ playwright (Python) ✅ Task 7.3 已加入（仅 Python 包，二进制⏳）│
│  ├─ ddddocr 1.6+        ✅ Task 8.3 已加入（含 onnxruntime+numpy+cv2）│
│  └─ pillow              ✅ Task 8.3 间接依赖（ddddocr 拉入）   │
│                                                              │
│  Node.js 20 LTS         ⏳ Task 11.3 装入镜像                  │
│  └─ @playwright/mcp     ⏳ Task 11.3 全局安装                  │
│                                                              │
│  Chromium + 系统依赖    ⏳ Task 11.3 通过 `playwright install` │
│  （fonts-noto-cjk / libnss3 / libxss1 等 ~30 个 .so）          │
│                                                              │
│  执行物料目录           ⏳ Task 8.5（代码就绪）/ 11.3（挂载）   │
│  /app/uploads/test-data/                                     │
│                                                              │
│  录屏 / trace 目录      ⏳ Task 9.5 / 11.3                     │
│  /app/uploads/ui_artifacts/                                  │
└──────────────────────────────────────────────────────────────┘

外部依赖：
  - PostgreSQL 16+（一期已有）
  - Redis 7+      ⏳ Task 11.4（可选，目前进程内 ExecutionStreamHub
                  足够，本期不强制）
```

---

## 2. 按 Task 维度的部署变更清单

### 已落地 ✅

| Task | 变更项 | 文件 | 部署影响 |
|---|---|---|---|
| 7.1 | `app/modules/ui_automation/` 模块骨架 + pytest 基础设施 | `backend/pyproject.toml`（`[tool.pytest.ini_options]`）、`run.sh test` | 无新外部依赖；只是把 dev 单测路径标准化 |
| 7.2 | Python MCP 客户端 SDK | `backend/pyproject.toml` 加 `mcp>=1.27` | `uv sync` 后自动装到 `.venv`；**镜像构建无需额外 RUN**，已被 `uv sync --frozen` 覆盖 |
| 7.3 | Playwright Python SDK | `backend/pyproject.toml` 加 `playwright>=1.59`（实际 1.59.0，含 pyee 依赖） | `uv sync` 后自动装入 `.venv`（约 +40MB）。**注意：仅 Python 包就绪，Chromium 二进制推迟到 Task 11.3 装** |
| 7.3 | `BrowserBundle` / `SecurityGuard` / `SnapshotClipper` 模块 | `backend/app/modules/ui_automation/{browser_bundle,security,snapshot_clipper}.py` | 全部"代码层"；含 EnvironmentLike Protocol 让本期不依赖 Task 8.1 model |
| 8.1 | `TestEnvironment` + `PreconditionTemplate` 模型 + CRUD + State 治理 | `backend/app/modules/ui_automation/{models,schemas,service,router,state_manager}.py` + `alembic/versions/f1e2d3c4b5a6_*.py` | 数据库迁移：新增 2 张表 `ui_test_environments` / `ui_precondition_templates`；新增 4 个权限常量 `UI_ENV_*`（启动时自动同步到内置角色）；新增 `settings.UI_STATE_DIR`（默认 `uploads/ui_state`）需在容器中提前 mkdir |
| 8.1 | `uploads/ui_state/` 目录 | 当前 dev 环境会按需自动创建 | **生产部署需在 entrypoint / Dockerfile 提前 `mkdir -p` 并挂载** —— 详见 Task 11.3 施工蓝图 |
| 8.2 | `precondition_executor.py` + 试跑端点 `POST /preconditions/{id}/test` | `backend/app/modules/ui_automation/precondition_executor.py` + service/router/schemas 增量 | **0 新依赖**：完全复用 7.3 的 `BrowserBundle` + 8.1 的凭据加密。开发期 Chromium 未装时端点会返回明确 `error_kind=browser_error` 而非 500 |
| 8.2 | `AILoginRunner` Protocol（stub 占位） | `precondition_executor.py` | 真 runner 由 Task 9.4 注入；本期 stub 返回 `error_kind=not_implemented`，前端可据此区分"等 9.4"和"环境配置错" |
| 8.3 | `ddddocr 1.6.1` + 间接依赖 (`onnxruntime` 1.25 / `numpy` 2.4 / `opencv-python` 4.13 / `pillow` 12 / `protobuf` 7 / `flatbuffers` 25) | `backend/pyproject.toml` 加 `ddddocr` | `uv sync` 后自动装到 `.venv`（**约 +150MB**：onnxruntime ~17MB / opencv ~44MB / ddddocr 含 ONNX 模型 ~72MB / numpy ~5MB）。镜像构建期已被 `uv sync --frozen` 覆盖，**无需额外 Dockerfile RUN** |
| 8.3 | `app/modules/ui_automation/captcha_solver.py` + `platform_solve_captcha` tool | 新增模块 | 0 部署变更：纯代码层。bypass 模式不依赖 ddddocr，即使 ONNX 加载失败也能工作 |
| 8.5 | `TestDataSet` + `TestDataItem` 模型 + CRUD + 文件上传 + Fernet 加密 + reveal API | `backend/app/modules/test_data/{models,schemas,service,router,random_generator}.py` + `alembic/versions/a7b2c8d1e4f5_*.py` + `testcases.default_data_set_ids` 列 | 数据库迁移：新增 2 张表 `test_data_sets` / `test_data_items`（含 CHECK 约束）+ `testcases` 新列；新增 4 个权限 `test_data:{view,edit,reveal,import}`（**复用启动时 `init_roles` 角色同步机制**，已改为自动追加新权限到 system 角色）；新增 `settings.TEST_DATA_UPLOAD_DIR`（默认 `uploads/test-data`）+ `TEST_DATA_MAX_FILE_SIZE`（50MB）；**加密密钥复用一期 `ENCRYPT_KEY`**（无需单独新密钥） |
| 8.5 | `uploads/test-data/` 目录 | 当前 dev 环境按需自动 `mkdir` | **生产部署需在 Task 11.3 `mkdir -p` 并挂载 named volume**，卷策略与 `ui_state` 相同（不经 nginx 暴露为静态资源） |
| 8.6 | 物料批量导入 / 克隆 / 推荐 / save-as-set API | `backend/app/modules/test_data/{service,router,schemas}.py` 增量（新 4 endpoints：`POST /import`（JSON）/ `POST /import/csv`（multipart）/ `POST /clone` / `GET /recommend` / `POST /save-as-set`） | **0 新依赖、0 迁移**：CSV 解析用 stdlib `csv`；clone 时 file 物料走 `shutil.copy2` 复制到新 set 目录；单 CSV 硬上限 10MB / 单次 ≤10000 行（防滥用）；全部端点复用已有 `TEST_DATA_EDIT/IMPORT/VIEW` 权限 |
| 8.7 | 物料管理前端（列表 / 编辑器 / 6 种字段） | `frontend/src/views/test-data/{TestDataView,DataSetEditor}.vue` + `frontend/src/components/test-data/{SecretField,FileField,RandomField,DatasetField}.vue` + `frontend/src/services/testData.ts` + router/menu/permission 增量 | **0 后端变更、0 新依赖**：纯前端产物。新路由 `/projects/:projectId/test-data[/sets/:setId]` + 全局入口 `/test-data`（自动跳当前项目）；菜单增加「测试物料」条目，仅对 `test_data:view` 权限可见；角色管理页自动带入新权限分组；`vite build` 新增 2 个 view chunk (≈42KB gzip 合计) + 一个 service chunk；Docker frontend 镜像需重建才能看到（已在任务内完成） |
| 8.8 | 物料前端增强（CSV/JSON 导入 + 克隆 + SetSelector + 用例/环境绑定） | `frontend/src/components/test-data/{ImportDialog,SetSelector}.vue` + DataSetEditor / TestcaseDetail / EnvironmentWizard 增量 + `frontend/src/services/{testData,testcases}.ts` 增量；**后端小量增量**：`backend/app/modules/testcases/{schemas,service}.py` 开放 `default_data_set_ids` 读写 | **0 迁移、0 新依赖**：`testcases.default_data_set_ids` 列 Task 8.5 已建，这里只是补齐 schemas / service 的对外读写；前端端点全部调用 Task 8.6 已落地的 API（import / import/csv / clone / recommend / save-as-set）；CSV 限 10MB / 10000 行仍为后端硬限；典型部署流程 = 重建 backend + frontend 镜像（已在任务内完成） |
| 11.1 | Dashboard UI 自动化双视图统计 + 项目维度 ui-stats API | `backend/app/modules/dashboard/{ui_stats,router}.py` + `frontend/src/views/dashboard/DashboardView.vue` + `frontend/src/services/uiStats.ts` | **0 迁移、0 新依赖**：纯查询聚合（PG 端 `jsonb_array_elements` + GROUP BY）。Dashboard router prefix 从 `/api/dashboard` 重构成 `/api`，新 endpoint `GET /api/projects/{id}/ui-stats?view=business|execution`。两个口径同时返回，前端切换无需重新请求。typical: 重建 backend + frontend 镜像 |
| 11.2 | 清理 cron + admin 触发 API + 5 个 retention 配置 | `backend/app/modules/ui_automation/{cleanup,cleanup_scheduler}.py` + `backend/app/modules/test_data/cleanup.py` + `backend/app/modules/admin/router.py` + `app/main.py`（startup/shutdown 钩子）+ `app/config.py`（7 个新 settings） | **0 新依赖、0 迁移**：用 `asyncio.create_task` 周期循环（不引 APScheduler/ARQ）。新 endpoint `POST /api/admin/ui-media/cleanup`，超管手动触发。新 settings：`CLEANUP_INTERVAL_HOURS=24` / `CLEANUP_RUN_ON_STARTUP=false` / `UI_MEDIA_RETENTION_DAYS=30` / `UI_STATE_RETENTION_DAYS=7` / `UI_SNAPSHOT_RETENTION_DAYS=7` / `TEST_DATA_FILE_RETENTION_DAYS=90` / `TEST_DATA_AUDIT_RETENTION_DAYS=180`（最后一个预留）。typical: 重建 backend 镜像即可（前端无变更） |
| 11.3 | **集大成**：Dockerfile (Node + Chromium + CJK + uploads 目录) + docker-compose (3 个 named volume + frontend ui_artifacts:ro 挂载) + .env.example + README + nginx.conf (`/uploads/ui_artifacts/` 静态资源) | `backend/Dockerfile` / `docker-compose.yml` / `.env.example` / `README.md` / `frontend/nginx.conf` | **镜像体积从 ~380MB → ~970MB**（Chromium 占大头）；分层 RUN 让 README/前端变更不会 invalidate 浏览器层。新 named volume：`test_data` / `ui_artifacts` / `ui_state` 跟旧 `backend_uploads` 子路径覆盖共存（向后兼容旧部署）；nginx 通过 `:ro` 挂 `ui_artifacts` 卷直接 alias 出媒体，不走 backend 流式转发 |

### 待集成 ⏳

| 计划 Task | 变更项 | 涉及文件 | 当前 Dockerfile 影响 | 备注 |
|---|---|---|---|---|
| 9.0 | platform_synthesize_data 的 LLM fallback | 无新依赖 | — | 只复用一期已配置的 LLM provider（沿用 `LLMConfig.is_default`） |
| ~~8.5~~ | ~~独立 Fernet 密钥~~ | ~~`.env.example`~~ | ~~新增 `TEST_DATA_ENCRYPTION_KEY`~~ | ✅ **改为复用一期 `ENCRYPT_KEY`**，避免多密钥管理成本；部署时只要保证 `ENCRYPT_KEY` 按一期要求生成即可（同 `/app/core/crypto.py`） |
| ~~9.3~~ → 已提前到 8.3 | ~~验证码 OCR~~ | ~~`backend/pyproject.toml` 加 `ddddocr`~~ | ✅ Task 8.3 已落地（见上方"已落地"表） | — |
| 11.4（可选）| ARQ + Redis worker 容器 | `docker-compose.yml`（加 `redis:7-alpine` + `worker` 服务）、`backend/Dockerfile`（同镜像不同 ENTRYPOINT） | Redis 持久化 volume；worker 共享 `.venv` 镜像 | **本期不强制**：进程内 ExecutionStreamHub 已能扛刷新 / 切页 / 短断网；只有"多 worker 弹性扩容 / 跨进程任务恢复"时才需要 |

---

## 3. Task 11.3 施工蓝图（预制版）

> 这是给执行 Task 11.3 时的清单。直接对着 ✅ 勾选即可，不必再现想。

### 3.1 `backend/Dockerfile` 增量片段

```dockerfile
# ─── Node.js 20 LTS（Playwright MCP 子进程需要）────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# 全局装 Playwright MCP，避免运行时 npx 第一次拉包卡顿
RUN npm install -g @playwright/mcp@latest

# ─── 中文字体（截图 + 视觉断言）────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk fonts-noto-cjk-extra \
    && rm -rf /var/lib/apt/lists/*

# ─── Playwright Chromium + 系统依赖（约 300MB）────────────────────────
# --with-deps 会自动装 libnss3 / libxss1 等 30+ 个 .so，比手动列 apt 稳
RUN uv run playwright install chromium --with-deps

# ─── 物料 + UI artifacts + state 目录 ────────────────────────────────
# ui_state 存 BrowserContext storage_state（cookie + localStorage）；含登录态
# 不可暴露成静态资源；test_data 存物料文件；ui_artifacts 存 trace/视频/截图
RUN mkdir -p /app/uploads/test-data /app/uploads/ui_artifacts /app/uploads/ui_state \
    && chmod 755 /app/uploads/test-data /app/uploads/ui_artifacts \
    && chmod 700 /app/uploads/ui_state
```

### 3.2 `docker-compose.yml` 增量

```yaml
services:
  backend:
    volumes:
      - test_data:/app/uploads/test-data  # Task 8.5：物料文件（含加密 secret 指向的 meta，不含明文）
      - ui_artifacts:/app/uploads/ui_artifacts
      - ui_state:/app/uploads/ui_state  # ⚠️ 含登录 cookie，绝不能挂到 nginx 静态目录

volumes:
  test_data: {}
  ui_artifacts: {}
  ui_state: {}
```

### 3.3 `.env.example` 增量

```bash
# ─── 二期 UI 自动化 ───────────────────────────────────
# 物料 secret 加密密钥（必填）。生成命令：
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TEST_DATA_ENCRYPTION_KEY=

# UI state 文件存放目录（BrowserContext storage_state，含登录 cookie）
# 默认 uploads/ui_state；生产建议用挂载卷 + chmod 700
UI_STATE_DIR=uploads/ui_state

# 浏览器执行超时（秒）。单条用例最长执行时间，超过会被强制停掉
UI_EXEC_CASE_TIMEOUT_SECONDS=600

# Token 预算（per execution），AI 用工具的总 token 上限
UI_EXEC_TOKEN_BUDGET=200000

# 录屏开关。off=不录；fail=失败时录；always=全部录
UI_EXEC_VIDEO_MODE=fail

# Snapshot 裁剪上限（Task 7.3 snapshot_clipper 用）
UI_SNAPSHOT_MAX_CHARS=3000
UI_SNAPSHOT_DIFF_CONTEXT=2
```

### 3.4 `README.md` 增量章节标题

- `## UI 自动化执行（二期）`
  - 子节：环境准备（Node + Chromium 系统依赖）
  - 子节：物料管理（如何创建数据集、加密 secret）
  - 子节：执行触发与查看报告
  - 子节：故障排查（MCP 子进程起不来 / 浏览器闪退 / 截图无中文）

### 3.5 `frontend/nginx.conf` 增量

```nginx
location /uploads/ui_artifacts/ {
    alias /app/uploads/ui_artifacts/;
    add_header Cache-Control "public, max-age=86400";
}
```

---

## 4. 镜像体积估算

| 层 | 估算增量 | 累计 |
|---|---|---|
| python:3.11-slim 基底 | 130MB | 130MB |
| Python deps（一期）| 250MB | 380MB |
| Python deps（二期：mcp / playwright / ddddocr）| 80MB | 460MB |
| Node 20 LTS | 90MB | 550MB |
| @playwright/mcp（npm 全局）| 60MB | 610MB |
| 中文字体（fonts-noto-cjk）| 60MB | 670MB |
| Chromium + 系统依赖（`--with-deps` 装 30+ .so）| 300MB | **~970MB** |

**Task 11.3 实测（arm64 / Debian 13 trixie / multi-arch build）**：

| 指标 | 值 | 说明 |
|---|---|---|
| Content size | **1.09 GB** | 实际镜像内容；跟估算 970MB 接近，差额来自 Debian 13 (trixie) 比预期 12 大 + 8.3 ddddocr 拉的 ONNX 模型/opencv ~150MB |
| Disk usage | 3.81 GB | 含 multi-platform manifest 重复存储 + buildkit attestation；single-arch 部署不会这么大 |
| Top 1 层 | 1.32 GB | `playwright install chromium --with-deps`（含 30+ 系统 .so） |
| Top 2 层 | 579 MB | `uv sync --frozen --no-dev`（含 ddddocr/onnxruntime/opencv） |

> 接近 1GB。如果后续要瘦身，优先考虑：
> - 多阶段构建：build 阶段装 Node 拉 npm 包 + 编译，runtime 阶段只复制必要文件
> - Chromium 二进制单独 image layer，方便 cache hit
> - `fonts-noto-cjk-extra` 可换 `fonts-wqy-microhei`（小 30MB，但视觉断言精度可能下降）
> - 若不需要 ddddocr 的 ONNX 模型（验证码场景全 bypass），可以从 pyproject 移除，省 ~150MB

---

## 5. 部署期常见坑（提前预警）

| 现象 | 根因 | 提前规避 |
|---|---|---|
| `npx @playwright/mcp` 首次启动 60s+ | 容器内 npm 缓存为空，要去线上拉包 | Dockerfile 构建期 `npm install -g`，运行时直接命中本地 |
| Playwright 报 `chromium not found` | 装了 SDK 但没跑 `playwright install` | `--with-deps` 一并装系统库；CI 不要漏这步 |
| 截图里中文是豆腐块 | 没装 CJK 字体 | `fonts-noto-cjk` 必装 |
| MCP 子进程内存爆 | 浏览器没设 `--memory-pressure-off`，长跑积内存 | BrowserBundle 启动时传 Chromium flag；execution 间 close + 重开 |
| Secret 落库后无法解密 | `TEST_DATA_ENCRYPTION_KEY` 在不同环境间不同步 | 部署文档强调"密钥同步"；建议密钥进 K8s Secret / Vault |
| 录屏文件越积越多 | `ui_artifacts` 没有 TTL 清理 | Task 11.3 加 cron / Task 9.5 落库时记 expires_at + 后台 sweeper |

---

## 6. 升级到 ARQ + Redis（Task 11.4 可选增强）

什么时候要做：
- 需要在多台机器分布式跑 execution
- 需要"backend 进程重启后任务能自动恢复"（一期 chat 也没这个能力，可接受）
- 需要"任务队列优先级 / 限流 / 重试" 复杂调度

不做的话也 OK：进程内 `ExecutionStreamHub` 已经覆盖：
- ✅ 后台跑、不阻塞请求
- ✅ 客户端断开 / 重连无损续看
- ✅ 多 tab 同时订阅
- ✅ 30 分钟自动 evict

---

## 7. 文档维护规则

每个 Task 7.x ~ 11.x 的合并都必须做以下之一：
1. 如果引入新依赖 / 新外部进程 / 新挂载目录 → 在 §2 表格添一行 ⏳，并简述为什么
2. 如果落实了某个 ⏳ 项 → 把对应行从"待集成"挪到"已落地" ✅
3. 如果改动只在应用代码层、不影响部署 → 不需要更新本文档

> 这样到 Task 11.3 真要打包镜像时，本文档 §3 就是一份**已经审过、不会有遗漏**的施工单。
