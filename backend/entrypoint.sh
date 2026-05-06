#!/bin/bash
set -e

VENV_PYTHON="/app/.venv/bin/python"

echo "================================================"
echo "  AITestPlatform Backend - Startup"
echo "================================================"

# 1. Wait for database
echo "[1/3] Waiting for database..."
for i in $(seq 1 30); do
    if $VENV_PYTHON -c "
import socket, os
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.connect((os.environ.get('POSTGRES_HOST','db'), int(os.environ.get('POSTGRES_PORT','5432'))))
    s.close()
except:
    exit(1)
" 2>/dev/null; then
        echo "  Database is ready."
        break
    fi
    echo "  Waiting... ($i/30)"
    sleep 2
done

# 2. Run database migrations
echo "[2/3] Running database migrations..."
$VENV_PYTHON -c "from alembic.config import main; main(argv=['upgrade', 'head'])"
echo "  Migrations complete."

# 3. Initialize admin account
echo "[3/3] Initializing admin account..."
$VENV_PYTHON -c "
import asyncio
import os
from app.database import async_session_factory
from sqlalchemy import select, or_, insert
from app.modules.auth.models import User, Role, user_roles
from app.core.security import hash_password
from app.modules.auth.init_data import init_roles

async def init():
    await init_roles()

    username = os.environ.get('ADMIN_USERNAME', 'admin')
    password = os.environ.get('ADMIN_PASSWORD', 'admin123')
    email = os.environ.get('ADMIN_EMAIL', 'admin@aitest.local')

    async with async_session_factory() as db:
        result = await db.execute(
            select(User).where(or_(User.username == username, User.email == email))
        )
        existing = result.scalar_one_or_none()

        if existing:
            print(f'  Admin user \"{username}\" already exists, skipping.')
        else:
            user = User(
                username=username,
                email=email,
                hashed_password=hash_password(password),
                display_name='系统管理员',
                is_superuser=True,
                is_active=True,
            )
            db.add(user)
            await db.flush()

            role_result = await db.execute(
                select(Role).where(Role.name == 'admin')
            )
            admin_role = role_result.scalar_one_or_none()
            if admin_role:
                await db.execute(
                    insert(user_roles).values(user_id=user.id, role_id=admin_role.id)
                )

            await db.commit()
            print(f'  Admin user created: {username} / {password}')

asyncio.run(init())
"
echo "  Initialization complete."

echo ""
# ── 有头浏览器栈（Xvfb + x11vnc + websockify + noVNC）───────────────
# 设计参见 backend/Dockerfile §6 注释。链路：chromium→Xvfb→x11vnc→websockify→6080。
#
# 启动失败处理：每一步都 best-effort——后端业务功能与 noVNC 解耦，VNC 起不来时
# 仅相当于「无法远程看实时画面」，但 environment.headless=False 仍能在 Xvfb 里跑，
# 截图 / 视频 / trace 均不受影响。
NOVNC_ENABLED="${UI_NOVNC_ENABLED:-true}"
NOVNC_PORT="${UI_NOVNC_PORT:-6080}"
VNC_DISPLAY="${UI_VNC_DISPLAY:-:99}"

if command -v Xvfb >/dev/null 2>&1; then
  echo "[Xvfb] Starting display $VNC_DISPLAY for headed browser support..."
  Xvfb "$VNC_DISPLAY" -screen 0 1920x1080x24 -nolisten tcp -ac >/tmp/xvfb.log 2>&1 &
  export DISPLAY="$VNC_DISPLAY"
  # Xvfb 起来需要 socket 文件就绪；600ms 经验值已稳定
  sleep 0.6
  echo "  DISPLAY=$DISPLAY (headed mode in container)"
else
  echo "[Xvfb] Not installed, headed browser may fail in container."
fi

if [ "$NOVNC_ENABLED" = "true" ] || [ "$NOVNC_ENABLED" = "1" ]; then
  if [ -n "$DISPLAY" ] && command -v x11vnc >/dev/null 2>&1 && command -v websockify >/dev/null 2>&1; then
    echo "[x11vnc] Exposing $DISPLAY → 127.0.0.1:5900 (loopback only)..."
    # -nopw：访问鉴权由 nginx /novnc/ 反代层兜底（前端登录态校验过的用户才看得到）
    # -localhost：仅 127.0.0.1 监听，避免 5900 被外部扫到
    # -shared：允许多个 viewer 同时观看（团队协作 / 旁观调试场景）
    # -forever：x11vnc 默认在第一个 viewer 断开后退出，加这条让它常驻
    # -quiet：少打日志，避免日志炸；详细排错时可改 -verbose
    x11vnc -display "$DISPLAY" -nopw -forever -shared -localhost \
      -rfbport 5900 -quiet >/tmp/x11vnc.log 2>&1 &

    echo "[websockify] VNC TCP → WS on port $NOVNC_PORT, serving noVNC static..."
    # --web 直接 serve /usr/share/novnc 里的纯前端，省掉额外 nginx 配静态
    # 目标端口 5900 走 127.0.0.1，websockify 进程内部转发就好
    NOVNC_WEB_DIR="/usr/share/novnc"
    if [ -d "$NOVNC_WEB_DIR" ]; then
      websockify --web="$NOVNC_WEB_DIR" "$NOVNC_PORT" 127.0.0.1:5900 \
        >/tmp/websockify.log 2>&1 &
      sleep 0.4
      echo "  noVNC ready: http://localhost:$NOVNC_PORT/vnc_lite.html (frontend nginx 反代到 /novnc/)"
    else
      echo "[websockify] $NOVNC_WEB_DIR not found, falling back to TCP-only WS bridge."
      websockify "$NOVNC_PORT" 127.0.0.1:5900 >/tmp/websockify.log 2>&1 &
    fi
  else
    echo "[noVNC] disabled (missing DISPLAY / x11vnc / websockify); 实时画面投屏不可用"
  fi
else
  echo "[noVNC] UI_NOVNC_ENABLED=false, 跳过 VNC 桥接（仅启 Xvfb 让 headed 浏览器能跑）"
fi

echo ""
echo "================================================"
echo "  Starting uvicorn server..."
echo "================================================"
# Single worker on purpose: the AI generate-batch stream hub and the chat
# agent's task state live in process memory. Running multiple workers would
# cause SSE subscribers to land on a different worker than the producer
# (empty hub → immediate "done"). If this service ever needs to scale out,
# migrate the stream hub to Redis pub/sub or Postgres LISTEN/NOTIFY first.
exec $VENV_PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
