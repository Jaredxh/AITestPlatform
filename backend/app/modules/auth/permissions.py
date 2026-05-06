"""RBAC 权限常量与预置角色定义。"""


class Permissions:
    # 项目
    PROJECT_CREATE = "project:create"
    PROJECT_EDIT = "project:edit"
    PROJECT_DELETE = "project:delete"
    PROJECT_VIEW = "project:view"
    # 需求
    REQUIREMENT_UPLOAD = "requirement:upload"
    REQUIREMENT_DELETE = "requirement:delete"
    REQUIREMENT_REVIEW = "requirement:review"
    REQUIREMENT_VIEW = "requirement:view"
    # 用例
    TESTCASE_CREATE = "testcase:create"
    TESTCASE_EDIT = "testcase:edit"
    TESTCASE_DELETE = "testcase:delete"
    TESTCASE_VIEW = "testcase:view"
    TESTCASE_GENERATE = "testcase:generate"
    TESTCASE_APPROVE = "testcase:approve"
    # LLM
    LLM_CONFIG = "llm:config"
    LLM_CHAT = "llm:chat"
    # UI 自动化（二期）
    UI_ENV_VIEW = "ui_env:view"
    UI_ENV_CREATE = "ui_env:create"
    UI_ENV_EDIT = "ui_env:edit"
    UI_ENV_DELETE = "ui_env:delete"
    # UI 自动化执行（二期 Task 9.6）
    # 拆三个权限是为了让"看历史"和"按下立即执行"是不同的责任：
    # tester 默认能 RUN，viewer 只能 VIEW；STOP 单独拎出来留作未来"只让发起人能停"的策略升级位
    UI_EXEC_VIEW = "ui_exec:view"
    UI_EXEC_RUN = "ui_exec:run"
    UI_EXEC_STOP = "ui_exec:stop"
    # Task 9.7：调试模式（POST /continue 推进单步）。
    # 与 RUN 拆开是为了"灰度放给资深 QA"——调试会卡住浏览器实例 30 分钟，
    # 不希望随便谁都能开
    UI_EXEC_DEBUG = "ui_exec:debug"
    # 测试物料（二期 Task 8.5）
    TEST_DATA_VIEW = "test_data:view"
    TEST_DATA_EDIT = "test_data:edit"
    TEST_DATA_REVEAL = "test_data:reveal"
    TEST_DATA_IMPORT = "test_data:import"
    # 提示词管理（2026-05 拆分）
    # 历史上 ``prompts/router.py`` 复用 ``REQUIREMENT_*`` 权限——语义混乱：
    # 改一段 LLM prompt 模板和上传需求文档完全是两个责任。这里独立出来，
    # 同时让前端 ``RoleEditDialog`` 的菜单树能展示"提示词管理"这一项
    # （之前用户编辑角色时找不到这个菜单的开关）。``init_data._seed_roles``
    # 每次启动自动同步系统角色权限，所以已部署的 admin / project_manager
    # 重启后会自动补上新权限，**无需手写 alembic 迁移**。
    PROMPT_VIEW = "prompt:view"
    PROMPT_EDIT = "prompt:edit"
    PROMPT_DELETE = "prompt:delete"
    # 管理
    USER_MANAGE = "user:manage"
    ROLE_MANAGE = "role:manage"


ALL_PERMISSIONS = sorted([
    v for k, v in vars(Permissions).items() if not k.startswith("_")
])

SYSTEM_ROLES: dict[str, dict] = {
    "admin": {
        "display_name": "管理员",
        "description": "拥有所有权限",
        "permissions": ALL_PERMISSIONS,
    },
    "project_manager": {
        "display_name": "项目经理",
        "description": "管理项目、需求和用例，使用 AI 对话",
        "permissions": [
            Permissions.PROJECT_CREATE,
            Permissions.PROJECT_EDIT,
            Permissions.PROJECT_DELETE,
            Permissions.PROJECT_VIEW,
            Permissions.REQUIREMENT_UPLOAD,
            Permissions.REQUIREMENT_DELETE,
            Permissions.REQUIREMENT_REVIEW,
            Permissions.REQUIREMENT_VIEW,
            Permissions.TESTCASE_CREATE,
            Permissions.TESTCASE_EDIT,
            Permissions.TESTCASE_DELETE,
            Permissions.TESTCASE_VIEW,
            Permissions.TESTCASE_GENERATE,
            Permissions.TESTCASE_APPROVE,
            Permissions.LLM_CHAT,
            Permissions.UI_ENV_VIEW,
            Permissions.UI_ENV_CREATE,
            Permissions.UI_ENV_EDIT,
            Permissions.UI_ENV_DELETE,
            Permissions.UI_EXEC_VIEW,
            Permissions.UI_EXEC_RUN,
            Permissions.UI_EXEC_STOP,
            Permissions.UI_EXEC_DEBUG,
            Permissions.TEST_DATA_VIEW,
            Permissions.TEST_DATA_EDIT,
            Permissions.TEST_DATA_REVEAL,
            Permissions.TEST_DATA_IMPORT,
            # 项目经理需要为团队定制提示词模板（如评审风格、用例生成口吻）
            Permissions.PROMPT_VIEW,
            Permissions.PROMPT_EDIT,
            Permissions.PROMPT_DELETE,
        ],
    },
    "tester": {
        "display_name": "测试人员",
        "description": "查看项目和需求，管理用例，使用 AI 对话",
        "permissions": [
            Permissions.PROJECT_VIEW,
            Permissions.REQUIREMENT_VIEW,
            Permissions.TESTCASE_CREATE,
            Permissions.TESTCASE_EDIT,
            Permissions.TESTCASE_DELETE,
            Permissions.TESTCASE_VIEW,
            Permissions.TESTCASE_GENERATE,
            Permissions.TESTCASE_APPROVE,
            Permissions.LLM_CHAT,
            Permissions.UI_ENV_VIEW,
            Permissions.UI_EXEC_VIEW,
            Permissions.UI_EXEC_RUN,
            Permissions.UI_EXEC_STOP,
            Permissions.UI_EXEC_DEBUG,
            Permissions.TEST_DATA_VIEW,
            Permissions.TEST_DATA_EDIT,
            Permissions.TEST_DATA_IMPORT,
            # 测试人员只读提示词（生成用例 / 评审走的是项目内已有模板，不允许
            # 改模板免得影响他人）
            Permissions.PROMPT_VIEW,
        ],
    },
    "viewer": {
        "display_name": "只读用户",
        "description": "仅可查看项目、需求和用例",
        "permissions": [
            Permissions.PROJECT_VIEW,
            Permissions.REQUIREMENT_VIEW,
            Permissions.TESTCASE_VIEW,
            Permissions.UI_ENV_VIEW,
            Permissions.UI_EXEC_VIEW,
            Permissions.TEST_DATA_VIEW,
            Permissions.PROMPT_VIEW,
        ],
    },
}
