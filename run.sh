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
    echo "  up              Docker 一键部署"
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
