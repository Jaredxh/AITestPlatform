# GHCR 部署教程（GitHub Container Registry）

> **TL;DR**：本地推代码 → GitHub Actions 自动构建镜像推到 `ghcr.io` → 服务器一行 `docker compose pull && up -d` 完成部署。
>
> 服务器**全程不需要**：clone 仓库 / 装 Node / 装 Python / 装 uv / pnpm / 拉 Chromium 二进制 / 跑 build。
>
> **首次部署（含 Action 构建时间）**：约 30-50 分钟（一次性）
> **后续部署**：服务器侧 30 秒 - 2 分钟

---

## 一、为什么选 GHCR

| 维度 | GHCR | 其它 |
|---|---|---|
| 公开镜像 | **免费、无限存储、无限拉取** | Docker Hub 匿名 pull 6h/200 次 |
| 私有镜像 | 个人账号 500 MB / Org 2 GB 免费 | 阿里云 ACR 个人版无限免费但需要国内账号 |
| 与代码同源 | 同一个 GitHub 仓库 → 同一套权限 / 审计 | 需要单独平台账号 |
| CI 集成 | GitHub Actions 一等公民 | 都需要 secret 配置 |
| 服务器拉取速度 | 境外服务器很快；国内有 CDN 节点 | 阿里云 ACR 国内更快 |

> **推荐场景**：开源 / GitHub 优先 / 境外服务器 / 想发布到 OCI 标准 registry → GHCR
> 国内 Aliyun / Tencent 服务器纯国内部署、不开源 → 阿里云 ACR 个人版会更快

---

## 二、整体流程图

```
本地开发                  GitHub                              你的服务器
─────────                ──────                              ──────────
git push  ──────────►   ┌─────────────┐                     ┌─────────────────┐
                        │ Actions     │                     │ docker compose  │
git tag v1 ─────────►   │ build push  │ ──► ghcr.io ──────► │   pull / up -d  │
                        │ to ghcr.io  │     (镜像仓库)       └─────────────────┘
                        └─────────────┘
                              │                                      │
                        ~25-40 min 首次                       ~3-5 min 拉镜像
                        ~2-5 min 增量                         ~30s 增量
```

---

## 三、首次发布（5 步，约 5 分钟人工 + 30 分钟等 Actions 跑）

### 3.1 创建 GitHub 仓库

```bash
# 在 GitHub 网页创建一个仓库（公开 / 私有都行）
# 例如 https://github.com/<your-username>/AITestPlatform
```

### 3.2 把本地项目推上去

```bash
cd /Users/wxh/Downloads/长轻Job/AITestPlatform

# 检查 .gitignore（应该已经包含 .env / uploads/）
cat .gitignore | grep -E "^\.env$|^uploads/"
# .env
# uploads/
# backend/uploads/

# 检查工作区干净
git status

# 加 GitHub 远端
git remote add origin git@github.com:<your-username>/AITestPlatform.git
# 或 HTTPS：git remote add origin https://github.com/<your-username>/AITestPlatform.git

# 首次推送
git branch -M main
git push -u origin main
```

> ⚠️ 推之前**最后确认一遍**：`git diff --cached --name-only` 应该没有 `.env` / `uploads/` / `backend/uploads/` 等敏感文件。

### 3.3 等 GitHub Actions 自动跑

推送后到 `https://github.com/<your-username>/AITestPlatform/actions` 查看：

```
Workflow: Build and Push to GHCR
├─ build (backend)   running...   预计 25-40 分钟（首次）
└─ build (frontend)  running...   预计 3-5 分钟
```

**首次跑慢的原因**：

- backend 镜像 4 GB，layer cache 全 miss
- Chromium 二进制 ~970 MB 从境外 CDN 下
- `fonts-noto-cjk-extra` ~500 MB

之后每次 `git push main` 都只重跑变更层，2-5 分钟。

### 3.4 镜像可见性设置（重要！开源场景必做）

GHCR 推送的镜像默认是 **private**——服务器要 `docker login ghcr.io` 才能 pull。

**如果你想开源（推荐）**：把镜像设为 public，服务器拉时不需要登录。

1. 打开 `https://github.com/<your-username>?tab=packages`
2. 找到 `aitestplatform-backend` → Package settings → Danger zone → Change visibility → Public
3. 同样把 `aitestplatform-frontend` 也设为 Public

**保持 private**：跳过此步；服务器拉镜像前需要 `docker login`（见 §4.3）。

### 3.5 验证镜像已推送

```bash
# 任何机器（不需要本仓库代码）
docker pull ghcr.io/<your-username>/aitestplatform-backend:latest
docker pull ghcr.io/<your-username>/aitestplatform-frontend:latest

# 或浏览器访问：
# https://github.com/<your-username>/AITestPlatform/pkgs/container/aitestplatform-backend
```

---

## 四、服务器部署（首次，约 5 分钟人工 + 5 分钟拉镜像）

### 4.1 服务器最小依赖

```bash
# Ubuntu / Debian / CentOS / RHEL 都行；这里以 Ubuntu 22.04+ 为例
# 必须：docker (≥ 24) + docker compose plugin

curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker  # 或重新登录

docker --version          # >= 24
docker compose version    # >= v2.20
```

### 4.2 服务器配 docker 镜像加速（国内服务器强烈建议）

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com",
    "https://mirror.baidubce.com"
  ]
}
EOF
sudo systemctl restart docker
```

> 注意：镜像加速器**只对 `docker.io` 生效**（pull `postgres:16-alpine` 等）；GHCR 镜像直连 `ghcr.io`，国内拉一般也够快（GHCR 有 CDN）。如果发现 ghcr.io 慢，可以走 daocloud / nju 的 ghcr 反代（见 §8 常见坑）。

### 4.3 准备部署目录

```bash
mkdir -p ~/aitestplatform && cd ~/aitestplatform

# 服务器上不需要 git clone 整个仓库，只下载 2 个文件：
curl -fsSL -o docker-compose.prod.yml \
  https://raw.githubusercontent.com/<your-username>/AITestPlatform/main/docker-compose.prod.yml

curl -fsSL -o .env.example \
  https://raw.githubusercontent.com/<your-username>/AITestPlatform/main/.env.example

cp .env.example .env
```

### 4.4 配置 `.env`

```bash
nano .env  # 或 vim

# 至少必须改这 3 项：
SECRET_KEY=<随机 64 位字符串>           # python -c "import secrets;print(secrets.token_urlsafe(64))"
ENCRYPT_KEY=<Fernet 密钥>              # python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
ADMIN_PASSWORD=<强密码>

# 端口冲突时改这一项（服务器 8000 被其它项目占用）：
# BACKEND_PORT=7008                    # 默认 8000，可改成任意空闲端口
                                       # 容器内 uvicorn 始终监听 8000，无需改代码

# 添加 GHCR 引用变量：
echo "" >> .env
echo "# ========== GHCR 镜像引用 ==========" >> .env
echo "GHCR_OWNER=<your-username-lowercase>" >> .env
echo "IMAGE_TAG=latest  # 生产建议改成 v1.0.0 等具体版本号" >> .env
```

> ⚠️ `GHCR_OWNER` 必须**小写**（GHCR 限制）。如果你的 GitHub 用户名含大写，全转小写填这里。

### 4.5 (仅 private 镜像) 登录 GHCR

如果在 §3.4 你**没有**把镜像设为 public：

```bash
# 1. 在 GitHub 网页生成一个 Personal Access Token (classic)
#    Settings → Developer settings → Personal access tokens → Tokens (classic)
#    勾选权限：read:packages（够了）
#    复制生成的 token（只显示一次）

# 2. 服务器上登录
echo "<your-pat>" | docker login ghcr.io -u <your-username> --password-stdin
```

public 镜像跳过此步。

### 4.6 拉镜像 + 启动

```bash
cd ~/aitestplatform
docker compose -f docker-compose.prod.yml --env-file .env pull
# 首次 ~3-7 分钟（4 GB backend）
# 增量 ~30 秒（只拉变更 layer）

docker compose -f docker-compose.prod.yml --env-file .env up -d
docker compose -f docker-compose.prod.yml ps
# 应该看到 db / backend / frontend 都 healthy / running
```

### 4.7 验证

```bash
# 后端健康检查
# 默认 8000；若 .env 里设置了 BACKEND_PORT（例如 7008），用对应端口
curl "http://localhost:${BACKEND_PORT:-8000}/api/health"
# {"status":"ok"}

# 前端
curl -I http://localhost
# HTTP/1.1 200 OK

# 浏览器打开服务器 IP（或域名）→ 看到登录页 → 用 .env 里的 ADMIN_USERNAME / ADMIN_PASSWORD 登录
```

---

## 五、增量更新（最常用，30 秒 - 2 分钟）

### 5.1 工作流：代码改动 → 自动发布到 GHCR

#### 方式 A：自动模式（推荐，无需思考）

```bash
# 本地开发完成后
git add .
git commit -m "feat: xxx"
git push origin main

# GitHub Actions 自动跑（2-5 分钟），推 :latest + :sha-<7位> + :main 三个 tag
# 服务器：
ssh user@server
cd ~/aitestplatform
docker compose -f docker-compose.prod.yml --env-file .env pull
docker compose -f docker-compose.prod.yml --env-file .env up -d
```

#### 方式 B：版本化发布（生产推荐，可回滚）

```bash
# 本地：
./scripts/release.sh v1.0.0
# 自动校验 + 打 tag + push origin
# GitHub Actions 自动构建并推到 ghcr.io，tag = v1.0.0 + v1.0 + latest

# 服务器：钉版本（不是 latest）
ssh user@server
cd ~/aitestplatform
sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=v1.0.0/' .env
docker compose -f docker-compose.prod.yml --env-file .env pull
docker compose -f docker-compose.prod.yml --env-file .env up -d
```

> 生产环境**强烈推荐**钉版本号（v1.0.0），不要用 latest——避免本地 push 后服务器意外升级。

### 5.2 一键发布脚本

`scripts/release.sh` 已封装好流程：

```bash
./scripts/release.sh v1.0.0                        # 标准发布
./scripts/release.sh v1.0.0-rc.1                   # 预发布
./scripts/release.sh v1.0.1 -m "fix: skill bug"    # 自定义 tag message
```

它会：

1. 校验在 main 分支 + 工作区干净 + tag 格式合法
2. 校验本地 main 与远端同步
3. 二次确认后打 tag 并 push
4. 输出 GitHub Actions 进度链接 + 服务器更新命令

### 5.3 回滚到旧版本

```bash
# 服务器
sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=v0.9.0/' .env
docker compose -f docker-compose.prod.yml --env-file .env pull
docker compose -f docker-compose.prod.yml --env-file .env up -d

# 数据库迁移如果向后不兼容则不能直接回滚（需要 alembic downgrade，先在 staging 验证）
```

---

## 六、镜像 tag 策略（实操推荐）

GitHub Actions workflow 自动给每次构建打多个 tag：

| 触发方式 | 自动产生的 tag | 用途 |
|---|---|---|
| `git push origin main` | `latest` + `main` + `sha-abc1234` | 开发 / staging |
| `git tag v1.2.3 + push` | `v1.2.3` + `v1.2` + `latest` + `sha-abc1234` | 正式发布 / 生产 |
| `git tag v1.2.3-rc.1 + push` | `v1.2.3-rc.1` + `sha-abc1234`（**不**打 latest） | RC 预发布 |
| `workflow_dispatch (手动)` | 自定义 tag + `sha-abc1234` | 临时测试 |
| `pull_request → main` | 仅构建不推送 | 验证 PR 没把 Dockerfile 整坏 |

**生产环境建议钉版本号**：`IMAGE_TAG=v1.0.0`
**staging 环境可以用 latest**：`IMAGE_TAG=latest`
**任何时候要看具体跑的什么 commit**：`docker compose images` 看 IMAGE ID + GHCR 网页查 sha tag

---

## 七、首次部署核对清单

```
本地侧：
  □ git remote 指向 GitHub
  □ .gitignore 包含 .env / uploads/
  □ git push origin main 成功
  □ GitHub Actions 全绿（约 30 分钟）
  □ ghcr.io 上看到 2 个 package
  □ 镜像 visibility 设置正确（public 开源 / private 私有）

服务器侧：
  □ docker --version >= 24
  □ docker compose version >= v2.20
  □ daemon.json 配了镜像加速（国内）
  □ 部署目录有 docker-compose.prod.yml + .env
  □ .env 改了 SECRET_KEY / ENCRYPT_KEY / ADMIN_PASSWORD
  □ .env 加了 GHCR_OWNER + IMAGE_TAG
  □ private 镜像已 docker login ghcr.io
  □ docker compose pull 成功（4 GB ~5 分钟）
  □ docker compose up -d 后 3 个容器全 running
  □ curl http://localhost:${BACKEND_PORT:-8000}/api/health 返回 ok
  □ 浏览器能登录平台
```

---

## 八、常见坑

### 8.1 GitHub Actions 第一次构建超时（> 6 小时）

GitHub Actions 公共仓 runner 单 job 上限 6 小时。backend 首次构建大概 25-40 分钟，远低于上限。如果真超时：

- 检查是不是 Chromium 拉取卡住（playwright CDN 偶尔抽风）
- 重试一次（Actions 页面 → Re-run failed jobs）
- 考虑给 Dockerfile 加 `ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright`（淘宝镜像）

### 8.2 GHCR 推送失败：`denied: installation not allowed to Create organization package`

PAT / GITHUB_TOKEN 权限不够。本 workflow 用 `permissions: packages: write` 应该够；如果你在 organization 仓库下：

1. Org 设置 → Packages → Allow members to create packages
2. 或者把 workflow `runs-on` 改成 self-hosted runner

### 8.3 服务器 pull `ghcr.io` 慢 / connection reset

**境外服务器**：通常很快，无问题。
**国内服务器**：GHCR 在中国境内 CDN 节点不全，有时候 200 KB/s。临时方案——走 ghcr 反代：

```bash
# 把 ghcr.io 替换为 ghcr.nju.edu.cn (南大反代，纯公益，不保证 SLA)
# 在 .env 里改：
sed -i 's|ghcr.io|ghcr.nju.edu.cn|g' docker-compose.prod.yml
docker compose -f docker-compose.prod.yml pull
```

或者长期方案——把 GHCR 镜像同步到阿里云 ACR（用 [skopeo](https://github.com/containers/skopeo) 或阿里云的镜像同步功能）。

### 8.4 服务器 pull 报 `unauthorized` 但镜像应该是 public

GHCR 私有镜像**首次推送时默认是 private**。即使在 GitHub 网页设了 public，如果你之前 pull 过一次报 401，docker 可能缓存了凭据。解决：

```bash
docker logout ghcr.io
docker compose -f docker-compose.prod.yml pull
```

### 8.5 本地是 ARM Mac，服务器是 x86_64

本 workflow 已经在 GitHub Actions 上构建了 amd64 镜像（runner 默认是 amd64），服务器直接拉就是 amd64，无问题。

如果你绕过 Actions 直接本地 push：

```bash
# 本地 ARM Mac 必须用 buildx 强制 amd64
docker buildx build --platform linux/amd64 \
  -t ghcr.io/<your-username>/aitestplatform-backend:latest \
  --push ./backend
```

### 8.6 想同时支持 amd64 + arm64 服务器

修改 `.github/workflows/build-and-push.yml`：

```yaml
platforms: linux/amd64,linux/arm64  # 改成多 arch
```

构建时间翻倍（backend 50-80 分钟首次），但镜像里同时含两种 arch，服务器自动选对的。

### 8.7 担心 GHCR 仓库被滥用拉爆带宽

GHCR 公开镜像目前**无限拉取**（不像 Docker Hub 限速）。但如果你后续想限制：把镜像设回 private，自己维护 PAT 分发。

### 8.8 私密信息（.env）该不该上传

**绝对不要**。`.gitignore` 已经把 `.env` 排除。`.env.example` 是模板（不含真实密钥），可以推。如果不小心 commit 了 `.env`：

```bash
# 立刻撤回
git rm --cached .env
git commit -m "chore: remove .env from tracking"
git push

# 然后所有暴露的密钥都必须立刻轮换
# - SECRET_KEY / ENCRYPT_KEY 重新生成（注意：换 ENCRYPT_KEY 会让已加密的 secret 物料解不开！需要先解密再换）
# - LLM API key 在对应平台撤销重发
# - 数据库密码改一遍
```

### 8.9 镜像太大想瘦身

单独一篇文档要写。短期可做：

- 改 Dockerfile 把 `fonts-noto-cjk-extra` 删掉（保留 `fonts-noto-cjk` 即可，省 ~500 MB）
- 拆 base + app 两层镜像（base 含 Chromium + Node + 字体，app 只含应用代码）；推 app 时不需要重推 base

详见三期之后的优化计划。

---

## 九、完整命令速查

```bash
# ── 本地（首次）─────────────────────────────────────────
git remote add origin git@github.com:<USER>/AITestPlatform.git
git push -u origin main
# GitHub Actions 自动构建（30 分钟）
# 在 https://github.com/<USER>?tab=packages 把镜像设为 public

# ── 本地（发布新版本）───────────────────────────────────
./scripts/release.sh v1.0.0
# 等 GitHub Actions 跑完（2-5 分钟）

# ── 服务器（首次）───────────────────────────────────────
mkdir -p ~/aitestplatform && cd ~/aitestplatform
curl -fsSL -o docker-compose.prod.yml https://raw.githubusercontent.com/<USER>/AITestPlatform/main/docker-compose.prod.yml
curl -fsSL -o .env.example          https://raw.githubusercontent.com/<USER>/AITestPlatform/main/.env.example
cp .env.example .env
nano .env   # 改 SECRET_KEY / ENCRYPT_KEY / ADMIN_PASSWORD / GHCR_OWNER / IMAGE_TAG
docker compose -f docker-compose.prod.yml --env-file .env pull
docker compose -f docker-compose.prod.yml --env-file .env up -d

# ── 服务器（增量更新）──────────────────────────────────
cd ~/aitestplatform
sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=v1.0.1/' .env  # 钉新版本
docker compose -f docker-compose.prod.yml --env-file .env pull
docker compose -f docker-compose.prod.yml --env-file .env up -d

# ── 服务器（查看 / 调试）────────────────────────────────
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml exec backend sh
docker compose -f docker-compose.prod.yml down              # 仅停容器
docker compose -f docker-compose.prod.yml down -v           # ⚠️ 连数据卷一起删（数据丢失）
```

---

## 十、与现有 docker-compose 文件的关系

| 文件 | 用途 | 适用场景 |
|---|---|---|
| `docker-compose.yml` | 本地开发 / 没有 GHCR 时直接源码构建 | 开发机、首次试用 |
| `docker-compose.dev.yml` | 开发模式 override（独立 pgdata_dev 等）| 开发机 |
| `docker-compose.vpn.yml` | VPN 内网场景 override（http_login / browser proxy）| 公司内网测试 |
| **`docker-compose.prod.yml`** | **生产部署：拉 GHCR 镜像，无源码** | **生产服务器** |

可以叠加 override：

```bash
# 生产 + VPN
docker compose -f docker-compose.prod.yml -f docker-compose.vpn.yml --env-file .env up -d
```

---

## 十一、推荐发布节奏

| 频率 | 场景 | 操作 |
|---|---|---|
| 每次代码 commit | 自动推 :latest + :sha-xxx | `git push main` 即可，无人工 |
| 每周 / 每个迭代 | 正式版本 | `./scripts/release.sh v1.x.0` |
| 紧急 hotfix | patch 版本 | `./scripts/release.sh v1.x.1` |
| 大版本 | major / minor | `./scripts/release.sh v2.0.0` |

服务器策略：

- **staging 环境**：`IMAGE_TAG=latest`，每天凌晨 cron `pull && up -d` 自动追主线
- **生产环境**：`IMAGE_TAG=v1.x.x` 钉版本，发布走人工审批

---










*文档版本：v1.0 — 首次创建*
*最后更新：2026-05-06*
