"""
Supervisor: thin orchestrator — delegates to taxonomy, attribute, and grouping services.
"""

from __future__ import annotations

from typing import List, Optional

from app.core.canonical_model import CanonicalProduct
from app.core.normalizer import normalize_raw_data
from app.services.attribute_service import AttributeService
from app.services.grouping_service import GroupingService
from app.services.taxonomy_service import DEFAULT_TAXONOMY_WEBHOOK, TaxonomyService


class Supervisor:
    def __init__(
        self,
        taxonomy_webhook_url: str = DEFAULT_TAXONOMY_WEBHOOK,
        *,
        verbose: bool = False,
    ) -> None:
        self.verbose = verbose
        self._taxonomy = TaxonomyService(taxonomy_webhook_url, verbose=verbose)
        self._attributes = AttributeService(verbose=verbose)
        self._grouping = GroupingService(verbose=verbose)

    # ------------------------------------------------------------------
    # Public entrypoints
    # ------------------------------------------------------------------

    def run_pipeline(self, raw_data: dict) -> CanonicalProduct:
        """Full pipeline for a single raw product dict."""
        if self.verbose:
            print("\n[supervisor] run_pipeline start (single item)")
        canonical = self.create_canonical(raw_data)
        self.run_taxonomy(canonical)
        self._run_post_taxonomy_stages(canonical)
        if self.verbose:
            print(
                f"[supervisor] run_pipeline done id={canonical.source_product_id} "
                f"taxonomy={((canonical.predicted_taxonomy or {}).get('path'))!r} "
                f"group={canonical.group_id!r}"
            )
        return canonical

    def run_batch_pipeline(self, raw_data_list: List[dict]) -> List[CanonicalProduct]:
        """Batch: normalize all → taxonomy → attributes → grouping → schema/validation."""
        if self.verbose:
            print(f"\n[supervisor] run_batch_pipeline start size={len(raw_data_list)}")

        canonicals = [self.create_canonical(item) for item in raw_data_list]
        self.run_taxonomy_batch(canonicals)

        for c in canonicals:
            self.run_attributes(c)

        self.run_grouping_batch(canonicals)

        for c in canonicals:
            self.run_schema_mapping(c)
            self.run_validation(c)

        if self.verbose:
            ready = 0
            review = 0
            blocked = 0
            for c in canonicals:
                conf = float(((c.predicted_taxonomy or {}).get("confidence")) or 0.0)
                has_review = bool((c.provenance or {}).get("review"))
                if has_review:
                    review += 1
                elif conf >= 0.95:
                    ready += 1
                elif conf < 0.80:
                    blocked += 1
            print(
                f"[supervisor] run_batch_pipeline done size={len(canonicals)} "
                f"ready={ready} review={review} blocked={blocked}"
            )
        return canonicals

    def create_canonical(self, raw_data: dict) -> CanonicalProduct:
        normalized = normalize_raw_data(raw_data)
        canonical = CanonicalProduct(**normalized)
        if self.verbose:
            print(
                f"  [stage] normalize id={canonical.source_product_id} "
                f"title={(canonical.raw_title or '')[:70]!r}"
            )
        return canonical

    # ------------------------------------------------------------------
    # Taxonomy
    # ------------------------------------------------------------------

    def run_taxonomy(self, canonical: CanonicalProduct) -> CanonicalProduct:
        self.run_taxonomy_batch([canonical])
        return canonical

    def run_taxonomy_batch(self, canonicals: List[CanonicalProduct]) -> List[CanonicalProduct]:
        if self.verbose:
            print(f"  [stage] taxonomy batch size={len(canonicals)}")
        return self._taxonomy.run_batch(canonicals)

    def fallback_taxonomy(self, canonical: CanonicalProduct) -> CanonicalProduct:
        return self._taxonomy.fallback(canonical)

    # ------------------------------------------------------------------
    # Post-taxonomy chain (single item)
    # ------------------------------------------------------------------

    def _run_post_taxonomy_stages(self, canonical: CanonicalProduct) -> None:
        self.run_attributes(canonical)
        self.run_grouping(canonical)
        self.run_schema_mapping(canonical)
        self.run_validation(canonical)

    def run_attributes(self, canonical: CanonicalProduct) -> CanonicalProduct:
        if self.verbose:
            print("  [stage] attributes")
        return self.run_attribute_extraction(canonical)

    def run_grouping(self, canonical: CanonicalProduct) -> CanonicalProduct:
        self.run_grouping_batch([canonical])
        return canonical

    def run_grouping_batch(self, canonicals: List[CanonicalProduct]) -> List[CanonicalProduct]:
        if self.verbose:
            print(f"  [stage] grouping batch size={len(canonicals)}")
        return self._grouping.run_batch(canonicals)

    def run_schema_mapping(self, canonical: CanonicalProduct) -> CanonicalProduct:
        if self.verbose:
            print(
                "  [stage] schema mapping (placeholder) "
                f"id={canonical.source_product_id} group={canonical.group_id}"
            )
        return canonical

    def run_validation(self, canonical: CanonicalProduct) -> CanonicalProduct:
        if self.verbose:
            conf = float(((canonical.predicted_taxonomy or {}).get("confidence")) or 0.0)
            needs_review = bool((canonical.provenance or {}).get("review"))
            print(
                "  [stage] validation (placeholder) "
                f"id={canonical.source_product_id} tax_conf={conf:.2f} "
                f"needs_review={needs_review}"
            )
        return canonical

    # ------------------------------------------------------------------
    # Category / attributes (delegates)
    # ------------------------------------------------------------------

    def get_category_key(self, taxonomy_path: Optional[str]) -> str:
        return self._attributes.get_category_key(taxonomy_path)

    def run_attribute_extraction(self, canonical: CanonicalProduct) -> CanonicalProduct:
        return self._attributes.extract(canonical)


# Backward compatibility for imports like: from app.orchestrator.supervisor import DEFAULT_TAXONOMY_WEBHOOK
__all__ = ["Supervisor", "DEFAULT_TAXONOMY_WEBHOOK"]
