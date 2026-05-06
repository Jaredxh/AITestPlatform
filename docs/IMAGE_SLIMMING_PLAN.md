# 镜像瘦身计划（v1）

> **核心约束**：不影响当前一期 / 二期任何功能、不影响本地开发与生产部署体验、不引入新的运行时依赖故障点。
> 任何一项改动都必须有"回滚操作"和"功能验证清单"。
>
> **当前基线**（实测，2026-05-06 macOS arm64 docker desktop）：
> - `aitestplatform-backend:latest` = **3.91 GB**
> - `aitestplatform-frontend:latest` = **94.4 MB**
>
> **目标**：
> - 保守目标：backend 降到 **≤ 2.8 GB**（节省 ≥ 1.1 GB / 约 28%）
> - 激进目标：backend 降到 **≤ 2.4 GB**（节省 ≥ 1.5 GB / 约 38%），需要做语言包裁剪
> - frontend 已经很小（94 MB），不需要专门优化

---

## 一、现状逐层分析

`docker history aitestplatform-backend:latest` 实测，按层大小降序：

| 序 | 层 | 大小 | 来源 | 是否可优化 |
|---|---|---|---|---|
| 1 | Chromium + 系统依赖 | **1.32 GB** | `playwright install chromium --with-deps` | ✅ locales 可裁 ~50MB |
| 2 | Python 依赖（uv venv） | **579 MB** | `uv sync --frozen --no-dev` | ✅ uv cache 未清理 ~150MB |
| 3 | 中文字体 | **314 MB** | `fonts-noto-cjk fonts-noto-cjk-extra` | ✅ extra 可去 ~150MB |
| 4 | Node.js 20 (NodeSource) | **211 MB** | `setup_20.x` + apt | ✅ 改用官方 slim image 复制 ~−130MB |
| 5 | Debian trixie base | **109 MB** | python:3.11-slim 底层 | ❌ 不可改（alpine 不兼容 Playwright） |
| 6 | Xvfb + noVNC 栈 | **75 MB** | xvfb x11vnc websockify novnc | ⚠️ novnc 静态文件可裁 ~10MB |
| 7 | @playwright/mcp | **57 MB** | npm install -g | ❌ MCP 必须 |
| 8 | Python 编译产物 | **52 MB** | python:3.11 base 自带 | ❌ 来自 base，无法剔除 |
| 9 | uv binary | **49 MB** | COPY --from=ghcr.io/astral-sh/uv | ✅ multi-stage 后可不进 runtime |
| 10 | 一期基础包 | **24 MB** | libpq5 curl ca-certs antiword catdoc | ❌ 一期文档解析必需 |
| ... | 其它（COPY 代码 / 目录权限 / tz） | < 15 MB | — | — |

> **不要碰的层**（动了一定出问题）：
> - Chromium 二进制本身（Playwright 官方包，裁了会启动失败）
> - xvfb / x11vnc（headed 模式 + Live View 必需）
> - antiword / catdoc（一期 .doc / .ppt 需求文档解析必需）
> - libpq5（PostgreSQL client，asyncpg 依赖）

---

## 二、瘦身机会清单（按 ROI 排序）

| 编号 | 项 | 预估节省 | 风险 | 阶段 |
|---|---|---|---|---|
| **P1-1** | uv sync 后 `uv cache clean` | ~150 MB | 零 | Phase 1 |
| **P1-2** | `.dockerignore` 加严（tests / docs / .pytest_cache 等） | ~10 MB | 零 | Phase 1 |
| **P1-3** | Chromium locales 裁剪（保留 zh / en） | ~50 MB | 零 | Phase 1 |
| **P1-4** | apt cache 残留扫尾（已基本做到，查漏） | ~5 MB | 零 | Phase 1 |
| **P2-1** | 移除 `fonts-noto-cjk-extra`（仅保留主包） | ~150 MB | 低 | Phase 2 |
| **P2-2** | 多阶段构建：builder 装 uv，runtime 只复制 .venv | ~80 MB | 低 | Phase 2 |
| **P2-3** | Node.js 改用官方 `node:20-bookworm-slim` 多阶段复制 | ~130 MB | 低 | Phase 2 |
| **P2-4** | noVNC 静态包仅保留 `vnc_lite.html` 入口 + core/ | ~10 MB | 低 | Phase 2 |
| **P3-1** | 中文字体改用 `fonts-wqy-microhei`（10MB 替代 314MB） | ~290 MB | **中** | Phase 3（按需） |
| **P3-2** | `--squash` / `docker buildx --output=image,compression=zstd` | ~5-10% | **中** | Phase 3（按需） |

**保守路径**（Phase 1 + Phase 2）：节省 ~585 MB → **3.91GB → ~3.3GB**
**激进路径**（再叠加 P3-1）：再省 ~290 MB → **~3.0 GB**
**极限路径**（再叠加 squash）：再省 ~10% → **~2.7 GB**

---

## 三、Phase 1：零风险快赢（建议立刻执行）

总节省预估：**~215 MB**，全部不影响任何功能。

### P1-1：清理 uv cache（节省 ~150 MB）

**现状**：

```dockerfile
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python
```

执行后 `~/.cache/uv` 留下了 wheel 缓存和源码，~100-150 MB。

**改动**：

```dockerfile
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python \
    && uv cache clean
```

> 注意：`uv cache clean` 只清 uv 自己的下载缓存（`~/.cache/uv` / `XDG_CACHE_HOME/uv`），
> 不会动已经装好的 `.venv`，运行时无任何影响。

**验证**：
- [ ] `docker compose up -d backend` 后 `/api/health` 返回 ok
- [ ] 试跑 1 条 UI 用例正常（Playwright / httpx / sqlalchemy 都装在 venv，与 cache 无关）

**回滚**：删除 `&& uv cache clean` 即可。

---

### P1-2：`.dockerignore` 加严（节省 ~10 MB）

**现状** `backend/.dockerignore`：

```
.venv/
__pycache__/
*.pyc
.env
.git/
.ruff_cache/
uploads/
```

漏了不少开发产物 / 测试缓存。

**改动**：扩展为

```
# 已有
.venv/
__pycache__/
*.pyc
.env
.git/
.ruff_cache/
uploads/

# 新增：开发 / CI 产物
.pytest_cache/
.mypy_cache/
.coverage
htmlcov/
*.egg-info/
*.egg
build/
dist/

# 新增：测试与文档（生产不需要）
tests/
docs/
*.md
!README.md       # README 还是保留方便容器内查阅

# 新增：IDE / OS 杂项
.vscode/
.idea/
.DS_Store
*.swp
```

> ⚠️ **慎做**：把 `tests/` 排除会让生产镜像无法在容器内跑测试。
> 如果团队习惯进容器跑 pytest 做线上排查，**保留 tests/**，只去掉 `*cache/` 和 `*.egg-info/` 等纯开发产物。
> **本计划默认保留 tests/**，预估节省退化到 ~5 MB。

**验证**：
- [ ] `docker compose build backend` 成功
- [ ] 容器启动正常

**回滚**：删掉新增行。

---

### P1-3：Chromium locales 裁剪（节省 ~50 MB）

**现状**：Playwright 安装的 Chromium 在 `~/.cache/ms-playwright/chromium-*/chrome-linux/locales/` 下有 ~100 个 .pak 文件，每个 0.4-1.0 MB，总共 ~80-100 MB。

只保留 `en-US.pak` 和 `zh-CN.pak`（项目用例都是中文界面 + 英文 fallback）。

**改动**：在 `playwright install chromium --with-deps` 之后追加一条 RUN：

```dockerfile
# 5b. 裁剪 Chromium 多语言资源（保留 en + zh），节省 ~50 MB
RUN find /root/.cache/ms-playwright -type f -name '*.pak' -path '*/locales/*' \
        ! -name 'en-US.pak' ! -name 'zh-CN.pak' \
        -delete
```

**验证**：
- [ ] Chromium 启动正常（`docker compose exec backend chromium --version` 不报错）
- [ ] 一条用例完整跑通（Playwright 不依赖 .pak 文件，仅 Chromium UI 用）
- [ ] 截图中文显示正常（依赖字体，不依赖 .pak）

**回滚**：删除该 RUN，重新 build 会重新装全。

---

### P1-4：apt cache 残留扫尾（节省 ~5 MB）

**现状**：6 处 `RUN apt-get install` 已经都加了 `rm -rf /var/lib/apt/lists/*`，但有些层漏了 `apt-get clean`（清 `/var/cache/apt/archives/*.deb`）。

**改动**：每个 apt 层补 `apt-get clean`：

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends xxx \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
```

**验证**：build 通过即可。

**回滚**：删除 `&& apt-get clean`。

---

## 四、Phase 2：多阶段构建（建议中期执行，需冒烟测试）

总节省预估：**~370 MB**。需要重写 Dockerfile 结构，**强烈建议先在 feature 分支做完整 e2e 测试**。

### P2-1：移除 `fonts-noto-cjk-extra`（节省 ~150 MB）

**背景**：
- `fonts-noto-cjk` (~150 MB) 包含简体中文、繁体中文、日文、韩文常规字体（Regular / Bold）
- `fonts-noto-cjk-extra` (~150 MB) 包含 OpenType 变体、历史字形、甲骨文、楷书 / 行书 / 草书等

**项目实际使用场景**：
- UI 自动化截图：中文 GUI（侧边栏 / 表单 / 表格） — 主包足够
- LLM 视觉断言：识别中文文本 — 主包足够
- AI 评审里没有富文本 / 古文渲染需求

**改动**：

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
```

（移除 `fonts-noto-cjk-extra`）

**验证**：
- [ ] 跑一条登录用例，截图能看清中文（用例名 / 按钮 / 表单）
- [ ] LLM 视觉断言能正确识别中文（让 LLM 描述截图，看是否乱码）
- [ ] 历史用例的视频回放中文显示正常

**风险点**：如果 PRD 截图含古文 / 不常见字符，会回退成 Noto Sans CJK 默认字形（Regular），不影响识别但视觉会略不同。

**回滚**：恢复 `fonts-noto-cjk-extra`。

---

### P2-2：多阶段构建：分离 builder / runtime（节省 ~80 MB）

**现状**：uv binary（49 MB）和 uv cache（已在 P1-1 清掉）都在 runtime 镜像里。uv binary 只用于 `uv sync` 一次性安装，runtime 不需要。

**改动**：把 Dockerfile 改成两阶段：

```dockerfile
# ========== Stage 1: Python 依赖构建 ==========
FROM python:3.11-slim AS python-builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock* ./

# 装到 /app/.venv，runtime 阶段直接 COPY
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python \
    && uv cache clean

# ========== Stage 2: Runtime ==========
FROM python:3.11-slim

# 一期基础包 ...
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl ca-certificates gnupg antiword catdoc \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Node + MCP + 字体 + uploads + Chromium + xvfb 等
# ... (与现状一致，不变)

# ⭐ 只复制 .venv，不带 uv binary
COPY --from=python-builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY . .

ENV PYTHONUNBUFFERED=1
RUN chmod +x /app/entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
```

**验证**：
- [ ] `docker compose build backend` 成功
- [ ] `docker compose exec backend python -c "import sqlalchemy, playwright, httpx; print('OK')"` 正常
- [ ] 试跑用例完整链路 OK（http_login + chromium + LLM）
- [ ] 镜像大小确实下降 ~80 MB（`docker images`）

**风险点**：
- Playwright 的浏览器路径默认在 `~/.cache/ms-playwright`，多阶段时要确保该目录在 runtime 阶段，**不能放到 builder**（否则 1.3GB 复制会非常慢，反而比单阶段更糟）。本方案 Chromium 装在 runtime stage，不影响。
- `.venv` 路径在 builder 和 runtime 都是 `/app/.venv`，**保持一致**避免 shebang 失效。

**回滚**：恢复单阶段 Dockerfile。

---

### P2-3：Node.js 用官方 slim 多阶段复制（节省 ~130 MB）

**现状**：`curl ... setup_20.x | bash` + `apt-get install nodejs` 这一连串占了 211 MB（NodeSource 把 npm + 大量依赖都装上了）。

**改动**：用官方 `node:20-bookworm-slim` 作 builder，只复制 node binary 和 npm 到 runtime。

```dockerfile
# 在 Stage 1 之后再加一个 Node builder
FROM node:20-bookworm-slim AS node-source
# 不用 RUN，直接被下一阶段 COPY

# 在 Runtime stage：
COPY --from=node-source /usr/local/bin/node /usr/local/bin/node
COPY --from=node-source /usr/local/bin/npm /usr/local/bin/npm
COPY --from=node-source /usr/local/bin/npx /usr/local/bin/npx
COPY --from=node-source /usr/local/lib/node_modules /usr/local/lib/node_modules

# @playwright/mcp 全局安装（与现状一致）
RUN npm install -g @playwright/mcp@latest \
    && npm cache clean --force
```

**验证**：
- [ ] `docker compose exec backend node -v` 输出 v20.x
- [ ] `docker compose exec backend npx @playwright/mcp --help` 正常
- [ ] UI 用例的 MCP bridge 启动正常（看 `mcp_unavailable` 错误是否出现）

**风险点**：Node 官方镜像的 node 是动态链接 musl 还是 glibc 取决于 tag —— 必须用 `bookworm-slim`（glibc，与 python:3.11-slim 同 base）。**绝不能**用 `node:20-alpine`（musl 不兼容）。

**回滚**：恢复 NodeSource 安装方式。

---

### P2-4：noVNC 静态包裁剪（节省 ~10 MB）

**现状**：apt 装的 `novnc` 包含完整 demo（vnc.html、vnc_lite.html、tutorial、test/、karma/、core/、app/、vendor/）共 ~14 MB，其中只有 `vnc_lite.html`（前端 iframe 入口）+ `core/` 是必需的。

**改动**：

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb x11vnc websockify novnc \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && find /usr/share/novnc -mindepth 1 -maxdepth 1 \
        ! -name 'vnc_lite.html' \
        ! -name 'core' \
        ! -name 'vendor' \
        -exec rm -rf {} +
```

**验证**：
- [ ] 启动 Live View，前端能正常打开 iframe
- [ ] noVNC 客户端 reconnect / 断线重连正常

**风险点**：未来如果想用 noVNC 完整 UI（settings / clipboard 工具栏），需要 `vnc.html` —— 当前项目不用，可裁。

**回滚**：删除 `find` 那一行。

---

## 五、Phase 3：按需考虑（有取舍，需要项目方决策）

### P3-1：换文泉驿微米黑（节省 ~290 MB，但视觉变化）

**取舍**：
- ✅ 节省最多（314 MB → ~10 MB）
- ❌ Noto CJK 是 Adobe / Google 出品，覆盖 GB18030 / 日韩 / 繁体；wqy-microhei 仅简体，且字形比 Noto 略硬
- ❌ 截图 / 视频外观会变化；如果团队有视觉回归测试基线（baseline screenshot），会全失效
- ❌ 不含韩文 / 复杂繁体字符

**适用场景**：纯简体中文 UI、不在意视觉品质、追求镜像极致小（如 ARM / 低带宽部署）。

**不推荐默认启用**。如要做，建议出 build arg：

```dockerfile
ARG CJK_FONT=fonts-noto-cjk
RUN apt-get install -y --no-install-recommends ${CJK_FONT}
```

`docker compose build --build-arg CJK_FONT=fonts-wqy-microhei`

---

### P3-2：BuildKit `--squash` / zstd 压缩（节省 ~10%）

**取舍**：
- ✅ 多阶段构建后剩余的 layer 元数据 / 重复文件可被压缩
- ❌ 失去 layer cache（每次改代码全部 push）
- ❌ 需要 BuildKit + 实验特性

**不推荐用于本地开发**。仅在 CI 产 release 镜像时考虑。

---

## 六、推荐 rollout 路线（PR 拆分建议）

| PR | 内容 | 预估节省 | 风险 | 验证方式 |
|---|---|---|---|---|
| #slim-1 | P1-1 + P1-2 + P1-4（uv cache + .dockerignore + apt clean） | ~165 MB | 零 | build 通过 + /api/health |
| #slim-2 | P1-3（Chromium locales） | ~50 MB | 零 | 跑 1 条 UI 用例 |
| #slim-3 | P2-1（去除 fonts-noto-cjk-extra） | ~150 MB | 低 | 截图视觉对比 + LLM 视觉断言冒烟 |
| #slim-4 | P2-2 + P2-3（多阶段 builder） | ~210 MB | 中 | 完整 e2e 一遍（uv / node / mcp / chromium） |
| #slim-5 | P2-4（noVNC 裁剪） | ~10 MB | 低 | Live View 打开 |
| #slim-6 | P3-1（按需，加 build arg） | 可选 ~290 MB | 中 | 团队评审通过后再上 |

**建议节奏**：
1. PR #slim-1 / #slim-2 当周可上（零风险）
2. PR #slim-3 / #slim-4 / #slim-5 一起做一个完整冒烟测试（半天工作量），通过后合并
3. PR #slim-6 视部署场景再决定

---

## 七、验证矩阵

每次 PR 合并前必跑的清单（除特别说明外，均在本地 `docker compose up -d` 后执行）：

### 7.1 镜像层面

- [ ] `docker compose build backend` 成功
- [ ] `docker images aitestplatform-backend:latest --format '{{.Size}}'` 实际下降 ≥ 预估值的 80%
- [ ] `docker history aitestplatform-backend` 各层无异常

### 7.2 运行时基础

- [ ] `docker compose up -d` 三个容器全 running
- [ ] `curl http://localhost:8000/api/health` 返回 `{"status":"ok"}`
- [ ] `docker compose exec backend python -c "import app.main; print('imports ok')"`
- [ ] `docker compose logs backend | grep -iE 'error|traceback'` 无异常

### 7.3 一期功能

- [ ] 登录 admin / admin123 成功
- [ ] 上传一份 .docx 需求文档（验证 antiword/catdoc 还在）
- [ ] AI 评审一遍，能正常出评审报告
- [ ] AI 生成一组用例，能写入数据库

### 7.4 二期 UI 自动化

- [ ] 创建 / 编辑 1 个 UI 环境（含 http_login 前置）
- [ ] 试跑前置成功（http_login challenge → token）
- [ ] 完整执行一条 UI 用例：
  - [ ] Playwright Chromium 启动
  - [ ] Xvfb 启动（headed 模式时）
  - [ ] 截图中文正常显示（视觉抽查）
  - [ ] LLM 视觉断言能识别中文
  - [ ] 用例 verdict 生成正常
- [ ] Live View 抽屉能打开，画面正常显示
- [ ] 批量执行 2 条用例，第二条用例前页面正确 reset

### 7.5 性能 / 启动

- [ ] backend 容器从 `up -d` 到 `/api/health` 200 的时间 ≤ 现状 + 5s
- [ ] `docker compose pull` 下载时间（GHCR 场景）按比例下降

---

## 八、frontend 镜像（无需大动）

frontend 已经只有 94 MB，构成：
- nginx:alpine base (~40 MB)
- vite build dist (~5-10 MB)
- nginx 默认配置 / 静态资源 (~5 MB)
- alpine 系统 (~40 MB)

**唯一可优化**：vite build 时开 gzip / brotli 预压缩，nginx 直接 serve `.gz` / `.br`。
预估收益：响应体积 -70%（用户加载快），但镜像大小变化 < 2 MB。
**不在镜像瘦身计划范围内**，归类到"前端性能优化"专项。

---

## 九、对比业界同类项目

| 项目 | 镜像大小 | 备注 |
|---|---|---|
| **本项目（基线）** | 3.91 GB | 含 Chromium + Node + xvfb + 全字体 |
| Playwright 官方 `mcr.microsoft.com/playwright/python:v1.59.0` | 2.26 GB | 仅 Chromium / Firefox / Webkit + Python |
| Selenium standalone-chrome | 1.34 GB | Chromium + selenium-server，无 Python |
| browsers/playwright noble (Ubuntu) | 1.74 GB | 多浏览器 + xvfb |
| **本项目（Phase 1+2 后）** | ~3.3 GB | 已与 playwright 官方差距合理 |
| **本项目（Phase 3 全开）** | ~2.7 GB | 接近 playwright 官方水平 |

**结论**：本项目镜像大头是 Chromium + Python deps + 中文字体，这与业界同类项目的瓶颈一致。
通过 Phase 1+2 可以达到合理水平（~3.3 GB），无需冒着影响功能的风险去做激进瘦身。

---

## 十、后续追踪

- [ ] 在每次 PR 合并后，更新本文件 §一 的"基线"表格
- [ ] CI 加一步 `docker images --format '{{.Size}}'` 输出，便于在 PR review 看大小变化
- [ ] 如果 GHCR 拉取速度成为新瓶颈，再考虑 GHA `--platform linux/amd64,linux/arm64` 拆开按架构推

---

## 附录 A：实测命令速查

```bash
# 看当前镜像大小
docker images | grep aitestplatform

# 看 backend 各层占用
docker history aitestplatform-backend:latest --no-trunc \
    --format 'table {{.Size}}\t{{.CreatedBy}}' | head -30

# 进 backend 容器看哪些目录占大头（瘦身后验证）
docker compose exec backend du -sh /usr/local /root/.cache /app /var 2>/dev/null

# Chromium 路径
docker compose exec backend ls -la /root/.cache/ms-playwright/

# 字体安装位置
docker compose exec backend fc-list | grep -i noto | head

# noVNC 文件清单
docker compose exec backend ls /usr/share/novnc/
```

## 附录 B：每个改动对应的 PR 模板

```
## 镜像瘦身 - <P1-x / P2-x>: <一句话描述>

### 改动
- ...

### 预估节省
- 改动前：<size> MB
- 改动后：<size> MB
- 实测下降：<size> MB

### 验证清单
- [ ] §7.1 镜像层面
- [ ] §7.2 运行时基础
- [ ] §7.3 一期功能（仅相关项）
- [ ] §7.4 二期 UI 自动化（仅相关项）

### 回滚方案
- ...
```
