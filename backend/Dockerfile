FROM python:3.11-slim

# 构建阶段网络代理（宿主通过 docker-compose ``build.args`` / ``--build-arg`` 传入）。
# ``docker-compose.vpn.yml`` 只作用于**运行中**容器，默认不会带进 ``docker build``；
# 若构建时 apt/playwright 访问 deb.debian.org 超时，请在 .env 里配下面三条后
# 再 ``docker compose up -d --build``（详见根目录 ``.env.example`` §Docker 构建代理）。
#
# Docker Desktop（Mac）：代理在宿主机上监听时，构建容器内请写
# ``http://host.docker.internal:<端口>``，不要写 127.0.0.1（那会指向构建容器自身）。
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY}

# 构建期 Debian 网络加固（不传变量时也可用，不影响海外用户等价性）：
# - 拉高 apt 重试与超时 —— CDN 上大列表「中途几个 .deb」偶发超时很常见（你贴的日志是先
#   更新了索引再卡在池子里的 xfonts-* / xvfb）。
# - APT_MIRROR_HOST 非空时把 ``deb.debian.org`` 换成镜像 hostname（阿里云等），绕过
#   部分网络访问 Fastly 不稳；留空则不替换，与历史行为一致。
#
# Docker Desktop（macOS）：系统 VPN **往往不会**带进构建用的 Linux VM，所以「电脑上
# VPN 开了」≠ ``docker build`` 里也走 VPN；要么配 ``HTTP_PROXY=host.docker.internal:…``
#（见上文），要么配 ``APT_MIRROR_HOST``（详见根 ``.env.example``）。
ARG APT_MIRROR_HOST=
RUN set -eux; \
    mkdir -p /etc/apt/apt.conf.d; \
    printf '%s\n' \
      'Acquire::Retries "8";' \
      'Acquire::http::Timeout "120";' \
      'Acquire::https::Timeout "120";' \
    > /etc/apt/apt.conf.d/80-docker-build; \
    if [ -n "${APT_MIRROR_HOST}" ]; then \
      find /etc/apt -type f \( -name '*.sources' -o -name '*.list' \) \
        -exec sed -i \
          -e "s|https://deb.debian.org|http://${APT_MIRROR_HOST}|g" \
          -e "s|http://deb.debian.org|http://${APT_MIRROR_HOST}|g" \
          {} +; \
    fi

# ──────────────────────────────────────────────────────────────────────
# 基础依赖
#
# - libpq5 / curl / ca-certificates / gnupg：一期基线
# - antiword / catdoc：一期需求文档解析（.doc / .ppt 等老格式）
# - git：三期 Skills 导入（``importer.py`` 通过 ``git+url`` / ``*.git`` 链接
#   导入 SKILL 时直接 ``subprocess.run(["git", "clone", ...])``）。Debian
#   slim 默认不带，缺这一行容器里调 ``/api/skills/import-url`` 会直接
#   FileNotFoundError: [Errno 2] No such file or directory: 'git'。
# ──────────────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl ca-certificates gnupg antiword catdoc git \
    && rm -rf /var/lib/apt/lists/*

# ──────────────────────────────────────────────────────────────────────
# 二期 UI 自动化运行时（Task 11.3 集成；蓝图见 docs/PHASE2_DEPLOYMENT_NOTES.md §3）
#
# 顺序：Node → MCP → 中文字体 → Chromium 二进制（最大）→ uploads 目录
# 每一层独立 RUN 是为了让 Docker layer cache 命中更精细：动 README/前端
# 不会 invalidate ~970MB 的 Chromium 层。
# ──────────────────────────────────────────────────────────────────────

# 1. Node.js 20 LTS（@playwright/mcp 子进程依赖）
#    NodeSource 官方 setup 脚本 + 系统包安装，避免 nvm/手动解 tar 的脆弱
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# 2. @playwright/mcp 全局安装
#    构建期一次拉好；不要等运行时 npx 第一次启动 60s+ 拉包卡死
RUN npm install -g @playwright/mcp@latest

# 3. 中文字体（截图 + 视觉断言必备）
#    缺这步：截图里中文 = 豆腐块，断言 LLM 会误判
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk fonts-noto-cjk-extra \
    && rm -rf /var/lib/apt/lists/*

# 4. uploads 目录（含 4 个子目录）+ 权限
#    - test-data        物料文件（与 settings.TEST_DATA_UPLOAD_DIR 一致）
#    - ui_artifacts     视频 / trace / 截图（nginx 暴露成静态资源）
#    - ui_state         BrowserContext storage_state（含登录 cookie）
#                       ⚠️ chmod 700：绝不可被 nginx 静态化暴露
#    - requirements     一期需求文档（向后兼容）
#    顺序提到 uv sync 之前以便 cache 不被代码变更打掉
RUN mkdir -p \
        /app/uploads/test-data \
        /app/uploads/ui_artifacts \
        /app/uploads/ui_state \
        /app/uploads/requirements \
    && chmod 755 /app/uploads/test-data /app/uploads/ui_artifacts /app/uploads/requirements \
    && chmod 700 /app/uploads/ui_state

# ──────────────────────────────────────────────────────────────────────
# 应用代码 + Python 依赖
# ──────────────────────────────────────────────────────────────────────
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python

# 5. Chromium 二进制 + 系统依赖（最大的一层 ~300MB；放最后让 cache 更稳）
#    --with-deps 内部会跑 apt-get install，但前面的层都 rm 掉了 apt lists；
#    所以这里先 apt-get update 再让 playwright 自己处理依赖列表，最后清缓存
RUN apt-get update \
    && uv run playwright install chromium --with-deps \
    && rm -rf /var/lib/apt/lists/*

# 6. 「有头浏览器」远程观察栈（Xvfb + x11vnc + noVNC）
#    用途：服务器部署场景下让 environment.headless=False 真的有效，并把 chromium
#    实时画面通过浏览器投给运维 / 用例作者远程观察。
#
#    技术栈链路：
#       chromium → Xvfb（虚拟 X 显示器 :99）
#                 ↓
#               x11vnc（把 :99 当成 VNC 服务暴露在 5900 端口，loopback only）
#                 ↓
#               websockify（VNC TCP → WebSocket，并自带 --web 直接 serve noVNC 静态页）
#                 ↓
#               6080 端口对内（frontend nginx 反代 /novnc/ → backend:6080）
#                 ↓
#               浏览器里 ``<iframe src="/novnc/vnc_lite.html...">`` 实时看画面
#
#    包大小说明：Xvfb ~30MB, x11vnc ~3MB, websockify ~1MB, novnc 静态 ~5MB。总
#    增量 ~40MB，相对于 chromium 的 ~970MB 可以忽略。
#
#    关闭路径：``UI_NOVNC_ENABLED=false`` env → entrypoint 跳过 x11vnc/websockify
#    启动；Xvfb 仍会起（chromium headless=False 也需要它），但不会暴露任何 VNC 端口。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb x11vnc websockify novnc \
    && rm -rf /var/lib/apt/lists/*

COPY . .

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
