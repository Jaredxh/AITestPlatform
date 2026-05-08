from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "AITestPlatform"
    DEBUG: bool = True

    # Database
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "aitest"
    POSTGRES_PASSWORD: str = "aitest123"
    POSTGRES_DB: str = "aitest_platform"

    # JWT
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Fernet 对称加密 key：DB 里加密 LLM api_key / UI 登录态 / 物料 secret 等。
    # 这里的默认值与 ``.env.example`` 保持一致（``cp .env.example .env`` 即可
    # 开箱即用）。一旦改动会让旧库里所有已加密字段永久解不开 —— 一个部署点
    # 定下来后**别再动**。生产建议生成独立 key，详见 ``.env.example`` 注释。
    ENCRYPT_KEY: str = "sTOsMs0VqznVBvb3aBWQzqs3UctMQllS9Rf5Ii-JARc="

    # File storage
    UPLOAD_DIR: str = "uploads"
    REQUIREMENT_UPLOAD_DIR: str = "uploads/requirements"
    # 二期 UI 自动化：BrowserContext storage_state 持久化目录（按 environment_id 文件名）
    UI_STATE_DIR: str = "uploads/ui_state"
    # 二期 Task 8.5 测试物料：file 类型物料的文件根目录（按 project_id/set_id 分层）
    TEST_DATA_UPLOAD_DIR: str = "uploads/test-data"
    # 单个 file 物料上限，默认 50 MB（可通过 .env 覆盖，单位 bytes）
    TEST_DATA_MAX_FILE_SIZE: int = 50 * 1024 * 1024
    # 二期 UI 自动化产物根目录：按 execution_id 分层放 video/steps/trace
    UI_ARTIFACTS_DIR: str = "uploads/ui_artifacts"
    # 单步截图类型（png 清晰但大，jpeg 小但失真）
    UI_STEP_SCREENSHOT_TYPE: str = "png"

    # ── Task 11.2 清理 cron：定期回收磁盘 ──
    # 视频 / 截图 / trace / step screenshot：超过 N 天的删文件 + 清 DB 路径列
    UI_MEDIA_RETENTION_DAYS: int = 30
    # storage_state 文件：超过 N 天且 DB 中无对应 environment 的孤立 state
    UI_STATE_RETENTION_DAYS: int = 7
    # snapshot_before/after 与 tool_calls：超过 N 天的 step 把这些大字段清空
    # （metadata 还在，只是不能再"重放"详细内容）
    UI_SNAPSHOT_RETENTION_DAYS: int = 7
    # 测试物料 file 类型的孤立物理文件（DB 删除条目后磁盘还在 N 天）
    TEST_DATA_FILE_RETENTION_DAYS: int = 90
    # 物料审计日志保留天数（预留：审计表上线后启用）
    TEST_DATA_AUDIT_RETENTION_DAYS: int = 180

    # 周期 cron 触发间隔；0 = 不启用周期清理（仅保留 admin 手动触发 API）
    CLEANUP_INTERVAL_HOURS: int = 24
    # 启动时是否在第一次循环前立刻跑一次（让首次部署也有清理效果，
    # 同时压力测试 / 排错时可以关掉）
    CLEANUP_RUN_ON_STARTUP: bool = False

    # Server
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000

    # ── UI 自动化：浏览器出口代理（VPN 场景必备）─────────────────────
    # 用例：被测系统部署在公司内网，需要走 macOS / Linux 上的 VPN 才能访问。
    # Docker Desktop on macOS 的容器流量经 LinuxKit VM 出去，**对私网 IP 段
    # 不会命中宿主机 VPN 路由表**，导致容器永远连不通公司内网。
    # 解决：在宿主机起一个 HTTP/SOCKS5 代理（mitmproxy / tinyproxy / pproxy 等），
    # 容器把这个代理填到 ``UI_BROWSER_PROXY`` —— ``BrowserBundle`` 启动 chromium
    # 时把它作为 ``--proxy-server`` 传给 chromium，所有浏览器流量经宿主机代理出去，
    # 自然能命中 VPN 隧道。例：``http://host.docker.internal:8118``。
    # None / 空串 = 不走代理（默认）。
    UI_BROWSER_PROXY: str | None = None
    # 是否同时让 backend 自己的出口流量走该代理（影响 LLM 调用、需求文档下载等）。
    # 注：这条只是给 backend 配 ``HTTP_PROXY`` / ``HTTPS_PROXY`` env 提示，实际生效
    # 还需在 docker-compose 同步设置 env 变量。
    UI_BROWSER_PROXY_BYPASS: str | None = None  # 例：``localhost,127.0.0.1,db,host.docker.internal``

    # ── 有头浏览器远程观察（Xvfb + noVNC）────────────────────────────
    # 容器部署时让 ``environment.headless=False`` 真正可用：chromium 跑在 Xvfb
    # 虚拟显示器，画面经 x11vnc → websockify → 浏览器 ``<iframe>`` 实时看。
    # 链路详情见 ``backend/Dockerfile §6`` 与 ``backend/entrypoint.sh``。
    #
    # ``UI_NOVNC_ENABLED=false`` → 关闭 VNC 桥接（Xvfb 仍会起，仅"看不到画面"）。
    # 镜像里没装 xvfb / x11vnc / websockify 时这些字段也无害——entrypoint 会
    # best-effort 跳过启动，前端探测端口失败后隐藏"实时画面"按钮。
    UI_NOVNC_ENABLED: bool = True
    UI_NOVNC_PORT: int = 6080
    """websockify HTTP/WS 监听端口；frontend nginx 反代 ``/novnc/`` → ``backend:这个端口``。"""

    UI_VNC_DISPLAY: str = ":99"
    """Xvfb 显示器编号；改这个会同时影响 Xvfb / x11vnc / chromium 的 DISPLAY env。"""

    # ── http_login 专用代理（精确旁路，不污染 LLM / 其它 backend 出口）────
    # 用例：被测系统的 ``auth_base_url`` 在公司内网（容器路由打不通），但你又
    # 不想让全部 backend 流量都经过 ``HTTP_PROXY`` —— 因为某些代理只 split
    # 了内网，外网（如 OpenAI）反而被挡住。设置这一项后 **仅** ``http_login``
    # 类型的前置走该代理，``ai_login`` / ``state_inject`` / LLM 调用都不受影响。
    # 例：``http://host.docker.internal:8118``。空串 / None = 不启用旁路。
    UI_HTTP_LOGIN_PROXY: str | None = None

    # 技能包 ``http_get_json`` / ``http_post_json`` 出口代理（可选）。
    # Docker Desktop + macOS VPN 下容器直连打不通公司网段时，应与本机代理一致。
    # 空：依次回退 ``UI_HTTP_LOGIN_PROXY`` → 环境变量 ``HTTP_PROXY`` / ``http_proxy``。
    SKILL_HTTP_PROXY: str | None = None

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def DATABASE_URL_SYNC(self) -> str:
        """Alembic 迁移用的同步 URL"""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
