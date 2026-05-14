from __future__ import annotations

import os
from dataclasses import dataclass

from app.services.taxonomy_service import DEFAULT_TAXONOMY_WEBHOOK


@dataclass(frozen=True)
class Settings:
    app_name: str = "Catalog Enrichment Microservice"
    api_prefix: str = "/api/v1"
    taxonomy_webhook_url: str = DEFAULT_TAXONOMY_WEBHOOK
    environment: str = "dev"


def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "Catalog Enrichment Microservice"),
        api_prefix=os.getenv("API_PREFIX", "/api/v1"),
        taxonomy_webhook_url=os.getenv("TAXONOMY_WEBHOOK_URL", DEFAULT_TAXONOMY_WEBHOOK),
        environment=os.getenv("APP_ENV", "dev"),
    )

