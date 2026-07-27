import asyncio
import logging
from contextlib import asynccontextmanager
from contextlib import nullcontext
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import Settings, settings, validate_deployment_security
from app.core.db import init_db
from app.core.instance_lock import InstanceLock
from app.core.logging import configure_logging
from app.services.backups import run_startup_backup
from app.services.briefing_worker import briefing_scheduler_loop, run_startup_briefing_processing
from app.services.reminder_worker import reminder_polling_loop, run_startup_reminder_processing
from app.services.telegram_poller import run_startup_telegram_processing, telegram_polling_loop
from app.services.trello_worker import run_startup_trello_sync, trello_sync_loop


logger = logging.getLogger(__name__)
STARTUP_STEP_TIMEOUT_SECONDS = 10.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    config: Settings = app.state.settings
    validate_deployment_security(config)
    lock = nullcontext() if config.instance_lock_test_bypass else InstanceLock(config.resolved_instance_lock_path)
    with lock:
        configure_logging()
        init_db()
        run_startup_backup()
        runtime_task = asyncio.create_task(_run_runtime_workers())
        try:
            yield
        finally:
            runtime_task.cancel()
            try:
                await runtime_task
            except asyncio.CancelledError:
                pass


async def _run_runtime_workers() -> None:
    await _run_startup_steps()
    worker_tasks = [
        asyncio.create_task(worker())
        for enabled, worker in (
            (settings.telegram_enabled, telegram_polling_loop),
            (settings.telegram_enabled, reminder_polling_loop),
            (settings.trello_enabled, trello_sync_loop),
            (settings.telegram_enabled, briefing_scheduler_loop),
        )
        if enabled
    ]
    try:
        if worker_tasks:
            await asyncio.gather(*worker_tasks)
        else:
            await asyncio.Event().wait()
    finally:
        for task in worker_tasks:
            task.cancel()
        for task in worker_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


async def _run_startup_steps() -> None:
    for label, step in (
        ("telegram", run_startup_telegram_processing),
        ("reminders", run_startup_reminder_processing),
        ("briefing", run_startup_briefing_processing),
        ("trello", run_startup_trello_sync),
    ):
        try:
            await asyncio.wait_for(step(), timeout=STARTUP_STEP_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("%s startup step timed out after %.0fs; continuing runtime startup", label, STARTUP_STEP_TIMEOUT_SECONDS)
        except Exception:
            logger.exception("%s startup step failed; continuing", label)


def create_app(config: Settings = settings) -> FastAPI:
    application = FastAPI(
        title="alphawave-taskd",
        version="0.1.0",
        docs_url=config.docs_url,
        redoc_url=config.redoc_url,
        openapi_url=config.openapi_url,
        lifespan=lifespan,
    )
    application.state.settings = config

    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(router)

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.exists():
        application.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return application


app = create_app()


def main() -> None:
    import uvicorn

    validate_deployment_security(settings)
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
