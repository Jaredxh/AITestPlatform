#!/bin/bash
set -e

# AITestPlatform 一键初始化脚本
# 用法: bash scripts/init.sh

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo ""
echo "==========================================="
echo "  AITestPlatform - 一键初始化"
echo "==========================================="
echo ""

# ── Step 1: Check prerequisites ──
echo "[1/5] 检查环境依赖..."

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        echo "  ❌ 未找到 $1，请先安装: $2"
        exit 1
    fi
    echo "  ✓ $1"
}

check_cmd "docker" "https://docs.docker.com/get-docker/"
check_cmd "docker" "docker compose 需要 Docker 20.10+"

if ! docker compose version &>/dev/null; then
    echo "  ❌ docker compose 不可用，请升级 Docker"
    exit 1
fi
echo "  ✓ docker compose"

# ── Step 2: Create .env if missing ──
echo ""
echo "[2/5] 配置环境变量..."

if [ ! -f .env ]; then
    cp .env.example .env
    echo "  已从 .env.example 创建 .env"

    # Generate a random SECRET_KEY
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null || openssl rand -base64 48)
    if [ -n "$SECRET_KEY" ]; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|SECRET_KEY=.*|SECRET_KEY=$SECRET_KEY|" .env
        else
            sed -i "s|SECRET_KEY=.*|SECRET_KEY=$SECRET_KEY|" .env
        fi
        echo "  已生成随机 SECRET_KEY"
    fi
else
    echo "  .env 已存在，跳过"
fi

# ── Step 3: Build images ──
echo ""
echo "[3/5] 构建 Docker 镜像..."
docker compose build

# ── Step 4: Start services ──
echo ""
echo "[4/5] 启动服务..."
docker compose up -d

# ── Step 5: Wait and verify ──
echo ""
echo "[5/5] 等待服务就绪..."

# 读取 .env 中的 BACKEND_PORT（宿主机映射端口），默认 8000
BACKEND_PORT="$(grep -E '^BACKEND_PORT=' .env 2>/dev/null | tail -n1 | cut -d= -f2)"
BACKEND_PORT="${BACKEND_PORT:-8000}"

for i in $(seq 1 60); do
    if curl -sf "http://localhost:${BACKEND_PORT}/api/health" >/dev/null 2>&1; then
        echo "  ✓ 后端服务就绪"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "  ⚠️ 后端启动超时，请检查日志: docker compose logs backend"
        exit 1
    fi
    sleep 2
done

if curl -sf http://localhost:80 >/dev/null 2>&1; then
    echo "  ✓ 前端服务就绪"
else
    echo "  ⚠️ 前端可能尚未就绪，请稍后刷新页面"
fi

# ── Done ──
echo ""
echo "==========================================="
echo "  ✅ AITestPlatform 初始化完成！"
echo "==========================================="
echo ""
echo "  🌐 前端地址:  http://localhost"
echo "  📡 后端 API:  http://localhost:${BACKEND_PORT}/docs"
echo ""
echo "  默认管理员账号:"
echo "    用户名: admin"
echo "    密码:   admin123"
echo ""
echo "  ⚠️ 请在首次登录后修改管理员密码！"
echo ""
echo "  常用命令:"
echo "    docker compose logs -f     查看日志"
echo "    docker compose down        停止服务"
echo "    docker compose restart     重启服务"
echo ""
