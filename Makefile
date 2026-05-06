.PHONY: dev dev-backend dev-frontend up down logs install add-backend add-frontend db-migrate db-upgrade db-reset lint format typecheck build clean

# ==================== 开发环境 ====================

dev: ## 一键启动开发环境（数据库容器 + 本地前后端热重载）
	@echo "🚀 启动开发环境..."
	@docker compose -f docker-compose.dev.yml up -d db
	@echo "✓ PostgreSQL 已启动 (localhost:5432)"
	@echo "启动后端和前端..."
	@trap 'kill 0' EXIT; \
		cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 & \
		cd frontend && pnpm dev & \
		wait

dev-backend: ## 仅启动后端（需先启动数据库）
	cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## 仅启动前端
	cd frontend && pnpm dev

# ==================== Docker 部署 ====================

up: ## 一键启动全部容器（生产模式）
	docker compose up -d --build

down: ## 停止全部容器
	docker compose down
	docker compose -f docker-compose.dev.yml down

logs: ## 查看日志
	docker compose logs -f

# ==================== 依赖管理 ====================

install: ## 安装全部依赖（首次 clone 后执行）
	@echo "📦 安装后端依赖..."
	cd backend && uv sync
	@echo "📦 安装前端依赖..."
	cd frontend && pnpm install
	@echo "✓ 所有依赖安装完毕"

add-backend: ## 添加后端依赖 (usage: make add-backend pkg=fastapi)
	cd backend && uv add $(pkg)

add-frontend: ## 添加前端依赖 (usage: make add-frontend pkg=naive-ui)
	cd frontend && pnpm add $(pkg)

# ==================== 数据库 ====================

db-migrate: ## 生成迁移文件 (usage: make db-migrate msg="add user table")
	cd backend && uv run alembic revision --autogenerate -m "$(msg)"

db-upgrade: ## 执行迁移
	cd backend && uv run alembic upgrade head

db-reset: ## 重置数据库（危险！仅开发用）
	docker compose -f docker-compose.dev.yml down -v
	docker compose -f docker-compose.dev.yml up -d db
	@echo "等待数据库就绪..."
	@sleep 3
	cd backend && uv run alembic upgrade head
	@echo "✓ 数据库已重置"

# ==================== 质量检查 ====================

lint: ## 代码检查
	cd backend && uv run ruff check .
	cd frontend && pnpm lint

format: ## 代码格式化
	cd backend && uv run ruff format .
	cd frontend && pnpm format

typecheck: ## 类型检查
	cd frontend && pnpm typecheck

# ==================== 构建 ====================

build: ## 构建生产镜像
	docker compose build

build-frontend: ## 仅构建前端产物
	cd frontend && pnpm build

# ==================== 清理 ====================

clean: ## 清理构建产物和缓存
	rm -rf frontend/dist frontend/node_modules/.vite
	find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ 已清理"

# ==================== 帮助 ====================

help: ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
