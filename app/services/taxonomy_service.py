"""Taxonomy prediction via webhook + fallback."""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List

import requests
import urllib3

from app.core.canonical_model import CanonicalProduct

# Public clone default: invalid host — set TAXONOMY_WEBHOOK_URL in .env for your n8n (or other) endpoint.
DEFAULT_TAXONOMY_WEBHOOK = "https://example.invalid/catalog/taxonomy-webhook"


class TaxonomyService:
    def __init__(
        self,
        webhook_url: str = DEFAULT_TAXONOMY_WEBHOOK,
        *,
        verbose: bool = False,
    ) -> None:
        self.webhook_url = webhook_url
        self.verbose = verbose
        self.verify_ssl = self._read_verify_ssl_flag()
        if not self.verify_ssl and self.verbose:
            # Dev-friendly mode for self-signed n8n certs.
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            print("[taxonomy] SSL verification disabled (self-signed cert mode).")

    @staticmethod
    def _read_verify_ssl_flag() -> bool:
        """
        TAXONOMY_VERIFY_SSL:
          - true/1/yes/on  -> verify certs
          - false/0/no/off -> skip verification
        Default: false (to support internal self-signed n8n endpoint).
        """
        raw = os.getenv("TAXONOMY_VERIFY_SSL", "false").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _read_webhook_batch_size() -> int:
        """
        Items per POST to the taxonomy webhook (n8n). When done with one chunk, the next chunk is sent.

        TAXONOMY_WEBHOOK_BATCH_SIZE:
          - default 5
          - 0 or negative = send the whole batch in a single request (old behavior).
        """
        raw = os.getenv("TAXONOMY_WEBHOOK_BATCH_SIZE", "5").strip()
        try:
            return int(raw)
        except ValueError:
            return 5

    @staticmethod
    def _read_webhook_timeout_seconds() -> float:
        raw = os.getenv("TAXONOMY_WEBHOOK_TIMEOUT", "20").strip()
        try:
            return max(1.0, float(raw))
        except ValueError:
            return 20.0

    def run_batch(self, canonicals: List[CanonicalProduct]) -> List[CanonicalProduct]:
        if not canonicals:
            return canonicals

        chunk_size = self._read_webhook_batch_size()
        if chunk_size <= 0:
            chunk_size = len(canonicals)

        timeout = self._read_webhook_timeout_seconds()

        if self.verbose:
            print(
                f"  [taxonomy] start batch size={len(canonicals)} "
                f"webhook_chunk_size={chunk_size} timeout={timeout}s "
                f"endpoint={self.webhook_url}"
            )

        for offset in range(0, len(canonicals), chunk_size):
            chunk = canonicals[offset : offset + chunk_size]
            try:
                items_out = self._call_service(chunk, timeout=timeout)
                for canonical, item in zip(chunk, items_out):
                    self.apply_response(canonical, item)
                    if self.verbose:
                        tax = canonical.predicted_taxonomy or {}
                        print(
                            "  [taxonomy] mapped "
                            f"id={canonical.source_product_id} "
                            f"path={tax.get('path')!r} conf={float(tax.get('confidence') or 0):.3f}"
                        )
            except Exception as e:
                print(
                    f"[taxonomy] webhook chunk failed offset={offset} size={len(chunk)}: {e}"
                )
                for c in chunk:
                    if not c.predicted_taxonomy or not c.predicted_taxonomy.get("path"):
                        self.fallback(c)
                        if self.verbose:
                            print(
                                f"  [taxonomy] fallback applied id={c.source_product_id} "
                                f"path={(c.predicted_taxonomy or {}).get('path')!r}"
                            )

        if self.verbose:
            unknown = sum(
                1
                for c in canonicals
                if ((c.predicted_taxonomy or {}).get("path") in (None, "", "Unknown"))
            )
            print(
                f"  [taxonomy] done batch size={len(canonicals)} unknown={unknown}"
            )

        return canonicals

    def _call_service(
        self,
        canonicals: List[CanonicalProduct],
        *,
        timeout: float,
    ) -> List[Dict[str, Any]]:
        payload = {
            "items": [
                {
                    "id": str(uuid.uuid4()),
                    "title": c.raw_title,
                    "categories": list(c.raw_categories or []),
                }
                for c in canonicals
            ]
        }
        response = requests.post(
            self.webhook_url,
            json=payload,
            timeout=timeout,
            verify=self.verify_ssl,
        )
        response.raise_for_status()
        result = response.json()

        if isinstance(result, dict):
            items_list = [result]
        elif isinstance(result, list):
            items_list = result
        else:
            raise ValueError("Unexpected taxonomy response format")

        if len(items_list) != len(canonicals):
            raise ValueError(
                f"Taxonomy returned {len(items_list)} items for {len(canonicals)} products"
            )
        return items_list

    @staticmethod
    def apply_response(canonical: CanonicalProduct, item: Dict[str, Any]) -> None:
        # Support n8n shape (mapped_category, categoryId, categoryIds) and common alternates.
        path = item.get("mapped_category") or item.get("path")
        category_id = item.get("categoryId") or item.get("category_id")
        category_ids = item.get("categoryIds")
        if category_ids is None:
            category_ids = item.get("category_ids") or []
        if not isinstance(category_ids, list):
            category_ids = list(category_ids) if category_ids else []

        conf = item.get("confidence", 0)
        if conf is None:
            conf = 0

        canonical.predicted_taxonomy = {
            "path": path,
            "confidence": conf,
            "category_id": category_id,
            "category_ids": category_ids,
        }

    def fallback(self, canonical: CanonicalProduct) -> CanonicalProduct:
        title = (canonical.raw_title or "").lower()

        if "shoe" in title or "running" in title:
            canonical.predicted_taxonomy = {
                "path": "Footwear > Sports Shoes",
                "confidence": 0.7,
                "category_id": None,
                "category_ids": [],
            }
        else:
            canonical.predicted_taxonomy = {
                "path": "Unknown",
                "confidence": 0.4,
                "category_id": None,
                "category_ids": [],
            }

        if self.verbose:
            print(f"  [taxonomy] fallback: {canonical.predicted_taxonomy}")
        return canonical
