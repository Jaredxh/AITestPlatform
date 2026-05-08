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
# 从 ":99" 提取数字 99，给 lock / unix socket 路径使用
VNC_DISPLAY_NUM="${VNC_DISPLAY#:}"

# ── 清理上一轮残留的 X server 资源 ──
# 历史 bug：``docker compose restart`` 容器进程重启但 ``/tmp`` 不会清空（在容器
# 写层、不是 tmpfs）。旧 Xvfb 进程被 kill 后留下：
#   - /tmp/.X${N}-lock          —— 老 Xvfb 进程的 PID 锁文件
#   - /tmp/.X11-unix/X${N}      —— Unix domain socket
# 新 Xvfb 启动时看到 ``.X99-lock`` 直接报 ``Server is already active`` 拒启，
# 于是 DISPLAY=:99 被 export 出去但实际后面没有真正的 X server，所有 chromium
# 启动都会"Missing X server"。每次 restart 都会触发，redeploy 流程下尤其常见。
#
# 清理策略：lock 文件的 PID 必须**确认那个进程已经不在了**才能删，否则会破坏
# 仍在跑的 Xvfb（虽然在容器场景几乎不会发生，但还是稳一点）。
if [ -e "/tmp/.X${VNC_DISPLAY_NUM}-lock" ]; then
  OLD_XVFB_PID=$(cat "/tmp/.X${VNC_DISPLAY_NUM}-lock" 2>/dev/null | tr -d ' ')
  if [ -z "$OLD_XVFB_PID" ] || ! kill -0 "$OLD_XVFB_PID" 2>/dev/null; then
    echo "[Xvfb] 清理上一轮残留 lock /tmp/.X${VNC_DISPLAY_NUM}-lock (pid=$OLD_XVFB_PID 已不存在)"
    rm -f "/tmp/.X${VNC_DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${VNC_DISPLAY_NUM}"
  else
    echo "[Xvfb] WARN: /tmp/.X${VNC_DISPLAY_NUM}-lock 关联的 pid=$OLD_XVFB_PID 仍在运行，跳过清理"
  fi
fi

if command -v Xvfb >/dev/null 2>&1; then
  echo "[Xvfb] Starting display $VNC_DISPLAY for headed browser support..."
  Xvfb "$VNC_DISPLAY" -screen 0 1920x1080x24 -nolisten tcp -ac >/tmp/xvfb.log 2>&1 &
  XVFB_PID=$!
  export DISPLAY="$VNC_DISPLAY"
  # Xvfb 起来需要 socket 文件就绪；600ms 经验值已稳定
  sleep 0.6
  # 验证 Xvfb 真的活着 + socket 就绪。失败时 unset DISPLAY 让下游 chromium 走
  # headless（而不是带着死的 DISPLAY 跑导致"Missing X server"）。
  if ! kill -0 "$XVFB_PID" 2>/dev/null \
      || [ ! -S "/tmp/.X11-unix/X${VNC_DISPLAY_NUM}" ]; then
    echo "[Xvfb] WARN: Xvfb 启动失败（pid=$XVFB_PID 不在 / socket 不就绪），unset DISPLAY"
    echo "[Xvfb] 详情见 /tmp/xvfb.log:"
    tail -10 /tmp/xvfb.log 2>/dev/null | sed 's/^/  /'
    unset DISPLAY
  else
    echo "  DISPLAY=$DISPLAY (headed mode in container)"
  fi
else
  echo "[Xvfb] Not installed, headed browser may fail in container."
fi

# x11vnc / websockify 不需要预清理——``docker compose restart`` 会先 SIGTERM
# 容器的 PID 1（entrypoint），signal 会传播给所有子进程（Xvfb / x11vnc /
# websockify），新容器启动时这些进程都已彻底死掉。只有 Xvfb 在被杀时**不会**
# 清理 ``/tmp/.X99-lock`` 锁文件（这是 Xvfb 已知行为），所以上面那段 lock
# 清理才有必要。

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
