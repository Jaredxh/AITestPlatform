"""Backend pytest 全局配置。

asyncio 自动 mark 已通过 pyproject.toml 的 ``asyncio_mode = "auto"`` 配置。

import all ORM models eagerly so that relationship() string references
（`back_populates`、跨模块外键）能在测试构造 ORM 实例时找到对方类，避免
``sqlalchemy.exc.InvalidRequestError: ... failed to locate a name 'Project'``
之类的 mapper-init 错误。这个 import 仅当模块被 import 时执行一次，对生
产代码无副作用。
"""

# noqa: F401 — eager imports for SQLAlchemy mapper resolution
from app.modules.auth import models as _auth_models  # noqa: F401
from app.modules.llm import models as _llm_models  # noqa: F401
from app.modules.projects import models as _projects_models  # noqa: F401
from app.modules.requirements import models as _requirements_models  # noqa: F401
from app.modules.skills import models as _skills_models  # noqa: F401
from app.modules.test_data import models as _test_data_models  # noqa: F401
from app.modules.testcases import models as _testcases_models  # noqa: F401
from app.modules.ui_automation import models as _ui_automation_models  # noqa: F401

from app.modules.skills.platform_chat_tools import ensure_platform_chat_tools_registered

ensure_platform_chat_tools_registered()
