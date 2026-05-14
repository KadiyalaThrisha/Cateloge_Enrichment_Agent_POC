from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.catalog import router as catalog_router
from app.api.routes.health import router as health_router
from app.config.settings import get_settings

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Browser dev server (Vite) calls API on a different origin/port.
_default_origins = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
]
_cors_raw = os.getenv("CORS_ORIGINS", "").strip()
_cors_origins = (
    [o.strip() for o in _cors_raw.split(",") if o.strip()] if _cors_raw else _default_origins
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix=settings.api_prefix)
app.include_router(catalog_router, prefix=settings.api_prefix)


def _catalog_post_route_count() -> int:
    prefix = f"{settings.api_prefix}/catalog/"
    return sum(
        1
        for route in app.routes
        if getattr(route, "methods", None)
        and "POST" in route.methods
        and str(getattr(route, "path", "")).startswith(prefix)
    )


@app.get("/")
def root() -> dict:
    return {
        "service": settings.app_name,
        "environment": settings.environment,
        "docs": "/docs",
        "api_prefix": settings.api_prefix,
        "catalog_post_route_count": _catalog_post_route_count(),
    }

