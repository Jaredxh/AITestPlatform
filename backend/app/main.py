import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.exceptions import (
    AppException,
    app_exception_handler,
    generic_exception_handler,
    http_exception_handler,
)

# Wire our own module loggers into stderr so `logger.info/.warning/.error`
# from `app.*` actually surface in Docker logs. Uvicorn only configures
# `uvicorn.*` loggers by default.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("app").setLevel(logging.INFO)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:80"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    from app.modules.admin.router import router as admin_router
    from app.modules.auth.router import router as auth_router
    from app.modules.dashboard.router import router as dashboard_router
    from app.modules.llm.chat_router import router as chat_router
    from app.modules.llm.router import legacy_router as llm_legacy_router
    from app.modules.llm.router import router as llm_router
    from app.modules.projects.router import router as projects_router
    from app.modules.prompts.router import router as prompts_router
    from app.modules.requirements.router import router as requirements_router
    from app.modules.test_data.router import router as test_data_router
    from app.modules.testcases.router import router as testcases_router
    from app.modules.ui_automation.router import router as ui_automation_router
    from app.modules.users.router import router as users_router

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(projects_router)
    app.include_router(llm_router)
    app.include_router(llm_legacy_router)
    app.include_router(chat_router)
    app.include_router(requirements_router)
    app.include_router(prompts_router)
    app.include_router(testcases_router)
    app.include_router(dashboard_router)
    app.include_router(ui_automation_router)
    app.include_router(test_data_router)
    app.include_router(admin_router)

    @app.on_event("startup")
    async def on_startup():
        from app.modules.auth.init_data import init_roles, sync_built_in_prompts
        from app.modules.ui_automation.cleanup_scheduler import (
            start_cleanup_scheduler,
        )

        await init_roles()
        await sync_built_in_prompts()
        # Task 11.2 周期清理（asyncio task）；CLEANUP_INTERVAL_HOURS=0 时 no-op
        start_cleanup_scheduler()

    @app.on_event("shutdown")
    async def on_shutdown():
        from app.modules.ui_automation.cleanup_scheduler import (
            stop_cleanup_scheduler,
        )

        await stop_cleanup_scheduler()

    @app.get("/api/health")
    async def health_check():
        return {"status": "ok", "service": settings.PROJECT_NAME}

    return app


app = create_app()
