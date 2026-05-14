"""HTTP client for catalog enrichment FastAPI service."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

import requests


def _api_prefix() -> str:
    return os.getenv("API_PREFIX", "/api/v1").rstrip("/")


def _enrichment_chunk_size() -> int:
    """
    When > 0, split POST /catalog/enrichment/run into multiple requests of at most
    this many items (avoids taxonomy timeouts and huge single payloads).

    Set CATALOG_ENRICHMENT_CHUNK_SIZE=50 (example) before Streamlit. Default 0 = one request.

    Trade-off: family/variant grouping runs per chunk only; SKUs split across chunks are
    not merged into one group in that run.
    """
    raw = os.getenv("CATALOG_ENRICHMENT_CHUNK_SIZE", "0").strip()
    if not raw:
        return 0
    try:
        n = int(raw)
    except ValueError:
        return 0
    return max(0, n)


def enrichment_run_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}{_api_prefix()}/catalog/enrichment/run"


def health_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}{_api_prefix()}/health"


def _post_enrichment_run(
    items: List[Dict[str, Any]],
    base_url: str,
    *,
    verbose: bool,
    timeout: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    url = enrichment_run_url(base_url)
    response = requests.post(
        url,
        json={"items": items, "verbose": verbose},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    products = data.get("products") or []
    validations = data.get("validations") or []
    return products, validations


def run_enrichment_remote(
    items: List[Dict[str, Any]],
    base_url: str,
    *,
    verbose: bool = False,
    timeout: int = 300,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    chunk = _enrichment_chunk_size()
    if chunk <= 0 or len(items) <= chunk:
        return _post_enrichment_run(items, base_url, verbose=verbose, timeout=timeout)

    all_products: List[Dict[str, Any]] = []
    all_validations: List[Dict[str, Any]] = []
    for start in range(0, len(items), chunk):
        part = items[start : start + chunk]
        prods, vals = _post_enrichment_run(part, base_url, verbose=verbose, timeout=timeout)
        all_products.extend(prods)
        all_validations.extend(vals)
    return all_products, all_validations


def health_check(base_url: str, *, timeout: int = 5) -> Tuple[bool, str]:
    try:
        response = requests.get(health_url(base_url), timeout=timeout)
        if response.ok:
            return True, response.text or "ok"
        return False, f"HTTP {response.status_code}: {response.text[:200]}"
    except requests.RequestException as exc:
        return False, str(exc)
