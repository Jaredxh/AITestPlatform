#!/bin/bash
set -e

# AITestPlatform 统一命令入口
# 用法: ./run.sh <command>

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

export PATH="$HOME/.local/bin:$PATH"

case "${1:-help}" in

  # ==================== 开发环境 ====================
  dev)
    echo "🚀 启动开发环境..."
    docker compose -f docker-compose.dev.yml up -d db
    echo "✓ PostgreSQL 已启动 (localhost:5432)"
    echo "启动后端和前端（Ctrl+C 停止全部）..."
    trap 'kill 0' EXIT
    (cd "$BACKEND_DIR" && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000) &
    (cd "$FRONTEND_DIR" && pnpm dev) &
    wait
    ;;

  dev-backend)
    cd "$BACKEND_DIR" && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    ;;

  dev-frontend)
    cd "$FRONTEND_DIR" && pnpm dev
    ;;

  # ==================== Docker 部署 ====================
  init)
    bash "$PROJECT_ROOT/scripts/init.sh"
    ;;

  up)
    docker compose up -d --build
    echo "✓ 全部服务已启动"
    echo "  前端: http://localhost:80"
    echo "  后端: http://localhost:8000/docs"
    ;;

  redeploy)
    # 业务代码热更新 —— 跳过镜像重建。
    #
    # 适用场景：只改了 Python / Vue / 模板等"源码"文件，没动 Dockerfile、
    # pyproject.toml、uv.lock、package.json、pnpm-lock.yaml。耗时约 5-25s，
    # 比 ./run.sh up（≥60s 起步、最多 6 分钟）快一个数量级。
    #
    # 实现：
    #   - backend：`docker cp ./backend/app + alembic` 进容器 → restart
    #     （entrypoint.sh 会自动跑 alembic upgrade head）
    #   - frontend：本地 `pnpm build`（vite 增量，~10s）→ docker cp 进 nginx
    #     的 /usr/share/nginx/html → `nginx -s reload` 平滑重载
    #
    # 不适用场景（必须用 ./run.sh up 走完整重建）：
    #   - backend 加/删依赖（pyproject.toml / uv.lock 变了）
    #   - frontend 加/删依赖（package.json / pnpm-lock.yaml 变了）
    #   - 改 Dockerfile / docker-compose.yml / nginx.conf / entrypoint.sh
    shift
    target="${1:-all}"
    BACKEND_CTN="$(docker compose ps -q backend 2>/dev/null)"
    FRONTEND_CTN="$(docker compose ps -q frontend 2>/dev/null)"
    if [ -z "$BACKEND_CTN" ] && [ -z "$FRONTEND_CTN" ]; then
      echo "✗ 没有运行中的容器，请先执行 ./run.sh up"
      exit 1
    fi

    redeploy_backend() {
      if [ -z "$BACKEND_CTN" ]; then
        echo "⚠️  backend 容器未运行，跳过"
        return
      fi
      echo "[backend] 同步源码到容器..."
      # `docker cp <src>/. <ctn>:<dst>` 用末尾 ``/.`` 表示"把目录 *内容* 拷过去"，
      # 不带 ``/.`` 时会把整个目录作为子目录拷进去。这里要的是覆盖更新。
      docker cp "$BACKEND_DIR/app/." "$BACKEND_CTN:/app/app/"
      docker cp "$BACKEND_DIR/alembic/." "$BACKEND_CTN:/app/alembic/"
      docker cp "$BACKEND_DIR/alembic.ini" "$BACKEND_CTN:/app/alembic.ini"
      echo "[backend] restart 容器（entrypoint 会自动跑 alembic upgrade head）..."
      docker compose restart backend
      echo "[backend] 等待健康检查..."
      for i in $(seq 1 20); do
        if curl -sf http://localhost:"${BACKEND_PORT:-8000}"/api/health >/dev/null 2>&1; then
          echo "✓ backend 已就绪 (http://localhost:${BACKEND_PORT:-8000}/api/health)"
          return
        fi
        sleep 1
      done
      echo "⚠️  backend 健康检查 20s 仍未通过，请用 ./run.sh logs 看日志"
    }

    redeploy_frontend() {
      if [ -z "$FRONTEND_CTN" ]; then
        echo "⚠️  frontend 容器未运行，跳过"
        return
      fi
      if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        echo "[frontend] 检测到 node_modules 不存在，先 pnpm install..."
        (cd "$FRONTEND_DIR" && pnpm install --frozen-lockfile)
      fi
      echo "[frontend] 本地 pnpm build（vite 增量构建）..."
      (cd "$FRONTEND_DIR" && pnpm build)
      echo "[frontend] 同步 dist 到 nginx..."
      # 先把容器里旧 dist 清掉，避免删除文件没生效（cp 是 union 不是 sync）。
      # nginx 的 / 静态目录就是 /usr/share/nginx/html（见 frontend/Dockerfile）。
      docker exec "$FRONTEND_CTN" sh -c 'rm -rf /usr/share/nginx/html/*'
      docker cp "$FRONTEND_DIR/dist/." "$FRONTEND_CTN:/usr/share/nginx/html/"
      echo "[frontend] nginx -s reload（无停机）..."
      docker exec "$FRONTEND_CTN" nginx -s reload
      echo "✓ frontend 已就绪 (http://localhost:${FRONTEND_PORT:-80}/)"
    }

    case "$target" in
      backend|be)
        redeploy_backend
        ;;
      frontend|fe)
        redeploy_frontend
        ;;
      all|"")
        redeploy_backend
        redeploy_frontend
        ;;
      *)
        echo "用法：./run.sh redeploy [backend|frontend|all]"
        exit 2
        ;;
    esac
    echo ""
    echo "✓ 热更新完成（提示：改 deps / Dockerfile 仍需 ./run.sh up）"
    ;;

  down)
    docker compose down
    docker compose -f docker-compose.dev.yml down
    echo "✓ 已停止全部服务"
    ;;

  logs)
    docker compose logs -f
    ;;

  # ==================== 依赖管理 ====================
  install)
    echo "📦 安装后端依赖..."
    (cd "$BACKEND_DIR" && uv sync)
    echo "📦 安装前端依赖..."
    (cd "$FRONTEND_DIR" && pnpm install)
    echo "✓ 所有依赖安装完毕"
    ;;

  add-backend)
    shift
    cd "$BACKEND_DIR" && uv add "$@"
    ;;

  add-frontend)
    shift
    cd "$FRONTEND_DIR" && pnpm add "$@"
    ;;

  # ==================== 数据库 ====================
  db-migrate)
    shift
    cd "$BACKEND_DIR" && uv run alembic revision --autogenerate -m "${1:-auto}"
    ;;

  db-upgrade)
    cd "$BACKEND_DIR" && uv run alembic upgrade head
    ;;

  db-reset)
    echo "⚠️  重置数据库..."
    docker compose -f docker-compose.dev.yml down -v
    docker compose -f docker-compose.dev.yml up -d db
    echo "等待数据库就绪..."
    sleep 3
    cd "$BACKEND_DIR" && uv run alembic upgrade head
    echo "✓ 数据库已重置"
    ;;

  # ==================== 质量检查 ====================
  lint)
    echo "检查后端..."
    (cd "$BACKEND_DIR" && uv run ruff check .)
    echo "检查前端..."
    (cd "$FRONTEND_DIR" && pnpm lint)
    ;;

  format)
    (cd "$BACKEND_DIR" && uv run ruff format .)
    (cd "$FRONTEND_DIR" && pnpm format)
    ;;

  typecheck)
    cd "$FRONTEND_DIR" && pnpm typecheck
    ;;

  test)
    shift
    cd "$BACKEND_DIR" && uv run pytest "$@"
    ;;

  test-backend)
    shift
    cd "$BACKEND_DIR" && uv run pytest "$@"
    ;;

  validate-skill)
    shift
    cd "$BACKEND_DIR" && uv run python scripts/validate_skill.py "$@"
    ;;

  # ==================== 构建 ====================
  build)
    docker compose build
    ;;

  build-frontend)
    cd "$FRONTEND_DIR" && pnpm build
    ;;

  # ==================== 清理 ====================
  clean)
    rm -rf "$FRONTEND_DIR/dist" "$FRONTEND_DIR/node_modules/.vite"
    find "$BACKEND_DIR" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    echo "✓ 已清理"
    ;;

  # ==================== 帮助 ====================
  help|*)
    echo ""
    echo "AITestPlatform - 统一命令入口"
    echo ""
    echo "用法: ./run.sh <command>"
    echo ""
    echo "开发:"
    echo "  dev             一键启动开发环境（数据库+后端+前端）"
    echo "  dev-backend     仅启动后端"
    echo "  dev-frontend    仅启动前端"
    echo ""
    echo "部署:"
    echo "  init            一键初始化（首次部署推荐）"
    echo "  up              Docker 一键部署（全量重建，~60-360s）"
    echo "  redeploy        业务代码热更新（不改 deps 时用，~10-25s）"
    echo "                    例: ./run.sh redeploy / redeploy backend / redeploy frontend"
    echo "  down            停止所有服务"
    echo "  logs            查看容器日志"
    echo ""
    echo "依赖:"
    echo "  install         安装全部依赖"
    echo "  add-backend     添加后端依赖 (例: ./run.sh add-backend openai)"
    echo "  add-frontend    添加前端依赖 (例: ./run.sh add-frontend dayjs)"
    echo ""
    echo "数据库:"
    echo "  db-migrate      生成迁移 (例: ./run.sh db-migrate 'add user table')"
    echo "  db-upgrade      执行迁移"
    echo "  db-reset        重置数据库（仅开发用）"
    echo ""
    echo "质量:"
    echo "  lint            代码检查"
    echo "  format          格式化代码"
    echo "  typecheck       前端类型检查"
    echo "  test            运行后端 pytest（例: ./run.sh test tests/ui_automation/ -v）"
    echo "  validate-skill  校验 SKILL ZIP（例: ./run.sh validate-skill /tmp/pkg.zip）"
    echo ""
    echo "构建:"
    echo "  build           构建 Docker 镜像"
    echo "  build-frontend  构建前端产物"
    echo ""
    echo "其他:"
    echo "  clean           清理缓存和产物"
    echo "  help            显示此帮助"
    echo ""
    ;;
esac
