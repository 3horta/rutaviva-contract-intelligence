"""Punto de entrada de la API.

Se arranca con la fábrica, sin una `app` global, para que importar este módulo no lea
la configuración: `uvicorn --factory app.main:create_app`.
"""

from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    # Fail fast: una configuración inválida impide crear la app (y arrancar uvicorn).
    settings = settings or get_settings()

    app = FastAPI(title="Rutaviva Contract Intelligence", version="0.1.0")
    app.dependency_overrides[get_settings] = lambda: settings
    app.include_router(health_router)
    return app
