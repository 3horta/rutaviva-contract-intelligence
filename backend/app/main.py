"""API entry point.

Started through the factory, with no global `app`, so importing this module does not
read the settings: `uvicorn --factory app.main:create_app`.
"""

from fastapi import FastAPI

from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.problem import install_problem_handlers
from app.api.query import router as query_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    # Fail fast: invalid settings prevent creating the app (and starting uvicorn).
    settings = settings or get_settings()

    app = FastAPI(title="Rutaviva Contract Intelligence", version="0.1.0")
    app.dependency_overrides[get_settings] = lambda: settings
    install_problem_handlers(app)
    app.include_router(health_router)
    app.include_router(documents_router)
    app.include_router(query_router)
    return app
