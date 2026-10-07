from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.admin import router as admin_router
from .api.auth import router as auth_router
from .api.jobs import router as jobs_router
from .api.management import router as management_router
from .api.templates import router as templates_router
from .api.verification import api_router as verification_api_router
from .api.verification import page_router as verification_page_router
from .config import Settings, get_settings
from .database import configure, create_all, get_session_factory
from .services.email import build_email_backend
from .services.runner import JobRunner


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure(settings.database_url)
        create_all()
        email_backend = build_email_backend(settings)
        runner = JobRunner(get_session_factory(), settings, email_backend=email_backend)
        app.state.runner = runner
        app.state.settings = settings
        yield
        runner.shutdown(wait=True)

    app = FastAPI(
        title="Bulk Certificate Generator",
        description=(
            "Bulk certificate generation with user accounts (register/login/bearer tokens), "
            "dynamic recipient fields (JSON or CSV), built-in or uploaded PDF templates, "
            "background rendering, e-mail delivery, QR verification + revocation, "
            "exports (ZIP/PDF/PNG/CSV), participant/job/certificate management and dashboards."
        ),
        version="3.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.include_router(auth_router, prefix=settings.api_prefix, tags=["auth"])
    app.include_router(admin_router, prefix=settings.api_prefix, tags=["admin"])
    app.include_router(jobs_router, prefix=settings.api_prefix, tags=["jobs"])
    app.include_router(management_router, prefix=settings.api_prefix, tags=["management"])
    app.include_router(templates_router, prefix=settings.api_prefix, tags=["templates"])
    app.include_router(verification_api_router, prefix=settings.api_prefix, tags=["verification"])
    app.include_router(verification_page_router, tags=["verification"])

    @app.get("/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "email_backend": settings.email_backend}

    return app


app = create_app()
