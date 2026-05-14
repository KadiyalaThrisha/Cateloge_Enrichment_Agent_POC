from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EnrichmentRunRequest(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    verbose: bool = False


class ValidationResult(BaseModel):
    source_product_id: Optional[str] = None
    taxonomy_confidence: float = 0.0
    stage: str
    issues: List[str] = Field(default_factory=list)
    ready_for_publish: bool = False


class EnrichmentRunResponse(BaseModel):
    run_size: int = 0
    products: List[Dict[str, Any]] = Field(default_factory=list)
    validations: List[ValidationResult] = Field(default_factory=list)


class ProductsBatchResponse(BaseModel):
    """Intermediate pipeline stages return products only (no validation summary)."""

    run_size: int = 0
    products: List[Dict[str, Any]] = Field(default_factory=list)

