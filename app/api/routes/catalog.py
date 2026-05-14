from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from app.api.schemas import (
    EnrichmentRunRequest,
    EnrichmentRunResponse,
    ProductsBatchResponse,
    ValidationResult,
)
from app.config.settings import get_settings
from app.orchestrator.supervisor import Supervisor

router = APIRouter(
    prefix="/catalog",
    tags=["catalog"],
)


def _supervisor(payload: EnrichmentRunRequest) -> Supervisor:
    return Supervisor(
        taxonomy_webhook_url=get_settings().taxonomy_webhook_url,
        verbose=payload.verbose,
    )


def _require_items(payload: EnrichmentRunRequest) -> None:
    if not payload.items:
        raise HTTPException(status_code=400, detail="Payload items cannot be empty")


def _stage_for_product(product: Dict[str, Any]) -> str:
    conf = float(((product.get("predicted_taxonomy") or {}).get("confidence")) or 0.0)
    review = (product.get("provenance") or {}).get("review") or {}
    if review:
        return "needs_review"
    if conf >= 0.95:
        return "ready"
    if conf >= 0.80:
        return "staging"
    return "blocked"


def _validate_products(products: List[Dict[str, Any]]) -> List[ValidationResult]:
    validations: List[ValidationResult] = []
    for p in products:
        conf = float(((p.get("predicted_taxonomy") or {}).get("confidence")) or 0.0)
        stage = _stage_for_product(p)
        issues: List[str] = []
        if (p.get("predicted_taxonomy") or {}).get("path") in (None, "", "Unknown"):
            issues.append("taxonomy_unknown")
        if conf < 0.80:
            issues.append("taxonomy_confidence_low")
        if (p.get("provenance") or {}).get("review"):
            issues.append("manual_review_required")

        validations.append(
            ValidationResult(
                source_product_id=p.get("source_product_id"),
                taxonomy_confidence=conf,
                stage=stage,
                issues=issues,
                ready_for_publish=(stage == "ready"),
            )
        )
    return validations


@router.post(
    "/enrichment/run",
    response_model=EnrichmentRunResponse,
    summary="Run full pipeline",
    description="Primary orchestration entry: taxonomy, attributes, grouping, schema mapping, and validation for one item or a batch.",
)
def run_enrichment(payload: EnrichmentRunRequest) -> EnrichmentRunResponse:
    _require_items(payload)
    supervisor = _supervisor(payload)
    results = supervisor.run_batch_pipeline(payload.items)
    products = [r.model_dump() for r in results]
    validations = _validate_products(products)
    return EnrichmentRunResponse(
        run_size=len(products),
        products=products,
        validations=validations,
    )


@router.post(
    "/taxonomy/predict",
    response_model=ProductsBatchResponse,
    summary="Predict taxonomy path",
    description="Taxonomy prediction only. Can be used independently for diagnostics (same item shape as enrichment run).",
)
def predict_taxonomy(payload: EnrichmentRunRequest) -> ProductsBatchResponse:
    _require_items(payload)
    supervisor = _supervisor(payload)
    canonicals = [supervisor.create_canonical(item) for item in payload.items]
    supervisor.run_taxonomy_batch(canonicals)
    products = [c.model_dump() for c in canonicals]
    return ProductsBatchResponse(run_size=len(products), products=products)


@router.post(
    "/attributes/extract",
    response_model=ProductsBatchResponse,
    summary="Extract normalized attributes",
    description="Category-aware extraction: normalizes input, predicts taxonomy, then runs attribute extraction.",
)
def extract_attributes(payload: EnrichmentRunRequest) -> ProductsBatchResponse:
    _require_items(payload)
    supervisor = _supervisor(payload)
    canonicals = [supervisor.create_canonical(item) for item in payload.items]
    supervisor.run_taxonomy_batch(canonicals)
    for c in canonicals:
        supervisor.run_attributes(c)
    products = [c.model_dump() for c in canonicals]
    return ProductsBatchResponse(run_size=len(products), products=products)


@router.post(
    "/family/group",
    response_model=ProductsBatchResponse,
    summary="Create family and variants",
    description="Taxonomy-constrained grouping: runs taxonomy, attributes, then family / variant grouping for the batch.",
)
def group_families(payload: EnrichmentRunRequest) -> ProductsBatchResponse:
    _require_items(payload)
    supervisor = _supervisor(payload)
    canonicals = [supervisor.create_canonical(item) for item in payload.items]
    supervisor.run_taxonomy_batch(canonicals)
    for c in canonicals:
        supervisor.run_attributes(c)
    supervisor.run_grouping_batch(canonicals)
    products = [c.model_dump() for c in canonicals]
    return ProductsBatchResponse(run_size=len(products), products=products)


@router.post(
    "/content/enrich",
    response_model=None,
    summary="Generate content and metadata",
    description="Grounded generation only. Not implemented as a standalone HTTP stage yet.",
)
def enrich_content(_payload: EnrichmentRunRequest) -> None:
    raise HTTPException(
        status_code=501,
        detail=(
            "Standalone content enrichment is not implemented yet. "
            "Use POST /catalog/enrichment/run for the full pipeline, or extend the orchestrator."
        ),
    )


@router.post(
    "/media/enrich",
    response_model=None,
    summary="Validate/register media outputs",
    description="Optional media branch. Not implemented as a standalone HTTP stage yet.",
)
def enrich_media(_payload: EnrichmentRunRequest) -> None:
    raise HTTPException(
        status_code=501,
        detail=(
            "Standalone media enrichment is not implemented yet. "
            "Use POST /catalog/enrichment/run for the full pipeline, or extend the orchestrator."
        ),
    )


@router.post(
    "/quality/validate",
    response_model=List[ValidationResult],
    summary="Run validation and anomaly checks",
    description="Deterministic gating over items already present in the request payload (no upstream calls).",
)
def validate_products(payload: EnrichmentRunRequest) -> List[ValidationResult]:
    _require_items(payload)
    return _validate_products(payload.items)


@router.post(
    "/publish",
    response_model=None,
    summary="Publish validated output",
    description="Final publishing step: push to staging/live target. Not implemented yet.",
)
def publish_catalog(_payload: EnrichmentRunRequest) -> None:
    raise HTTPException(
        status_code=501,
        detail="Publishing to staging or live targets is not implemented yet.",
    )

