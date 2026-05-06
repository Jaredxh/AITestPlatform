from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.config import settings
from app.models.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 导入所有模型，确保 Alembic 能检测到
from app.modules.auth.models import Role, User, user_roles  # noqa: F401
from app.modules.projects.models import Project, ProjectMember  # noqa: F401
from app.modules.llm.models import LLMConfig, ChatSession, ChatMessage  # noqa: F401
from app.modules.requirements.models import RequirementDocument, AIReview  # noqa: F401
from app.modules.prompts.models import PromptTemplate, PromptVersion  # noqa: F401
from app.modules.testcases.models import TestcaseModule, Testcase, TestcaseStep, AIGenerationBatch  # noqa: F401
from app.modules.ui_automation.models import TestEnvironment, PreconditionTemplate  # noqa: F401
from app.modules.test_data.models import TestDataSet, TestDataItem  # noqa: F401

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL_SYNC,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(settings.DATABASE_URL_SYNC)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
