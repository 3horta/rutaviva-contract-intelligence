"""API entry point.

Started through the factory, with no global `app`, so importing this module does not
read the settings: `uvicorn --factory app.main:create_app`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from app.api.conversations import router as conversations_router
from app.api.demo_auth import demo_login_enabled
from app.api.demo_auth import router as demo_auth_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.problem import install_problem_handlers
from app.api.query import router as query_router
from app.core.auth import load_signing_key
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.telemetry import configure_telemetry, instrument_fastapi
from app.providers import warm_up


def create_app(settings: Settings | None = None) -> FastAPI:
    # Fail fast: invalid settings prevent creating the app (and starting uvicorn).
    settings = settings or get_settings()
    # Same for the JWT signing key: without it no request could be authenticated, so the
    # API refuses to start (raises AuthConfigError) instead of answering 503 to everything.
    # In Compose the `migrate` service creates the key before the API starts.
    load_signing_key(settings)

    # Runs after uvicorn has set up its loggers, so they are switched to JSON too.
    configure_logging(settings.log_level)
    configure_telemetry(settings.otel_service_name, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Load the embedding model before serving (in a thread: it is CPU-bound).
        await run_in_threadpool(warm_up, settings)
        yield

    app = FastAPI(title="Rutaviva Contract Intelligence", version="0.1.0", lifespan=lifespan)
    instrument_fastapi(app)
    app.dependency_overrides[get_settings] = lambda: settings
    install_problem_handlers(app)
    app.include_router(health_router)
    app.include_router(documents_router)
    app.include_router(query_router)
    app.include_router(conversations_router)
    # Demo sign-in (ADR-016): on by default; with DEMO_LOGIN=false the routes do not exist.
    if demo_login_enabled():
        app.include_router(demo_auth_router)
    return app
