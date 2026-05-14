import argparse
import json
from typing import Any, Dict, List

from app.orchestrator.supervisor import Supervisor
from app.utils.review_actions import apply_review_update
from app.utils.storage import save_products

LINE_WIDTH = 72


def create_batches(data, batch_size=5):
    for i in range(0, len(data), batch_size):
        yield data[i : i + batch_size]


def _rule(char: str = "=", width: int = LINE_WIDTH) -> None:
    print(char * width)


def _taxonomy_leaf(product: Dict[str, Any]) -> str:
    path = (product.get("predicted_taxonomy") or {}).get("path") or ""
    if not path or path == "Unknown":
        return "—"
    parts = [p.strip() for p in path.split(">") if p.strip()]
    return parts[-1] if parts else path


def _review_status(product: Dict[str, Any]) -> str:
    review = product.get("provenance", {}).get("review") or {}
    c = review.get("color") or {}
    if c.get("needs_review"):
        reason = c.get("reason") or "pending"
        return f"review:color ({reason})"
    return "ok"


def _products_needing_color_review(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in products:
        review = p.get("provenance", {}).get("review") or {}
        if "color" not in review:
            continue
        detail = review.get("color") or {}
        if detail.get("needs_review"):
            out.append(p)
    return out


def _print_batch_header(batch_index: int, batch_total: int, n_items: int) -> None:
    print()
    _rule("=")
    print(f"  Batch {batch_index}/{batch_total}  ·  {n_items} product(s)")
    _rule("=")


def _truncate(s: Any, max_len: int = 100) -> Any:
    if s is None or not isinstance(s, str):
        return s
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def build_cli_product_view(product: Dict[str, Any]) -> Dict[str, Any]:
    """
    Subset of pipeline output for readable terminal: id, title, taxonomy,
    attributes, grouping, and active provenance/quality when present.
    """
    tax = product.get("predicted_taxonomy") or {}
    variants_raw = product.get("variants") or []
    variant_preview: List[Dict[str, Any]] = []
    for v in variants_raw[:12]:
        variant_preview.append(
            {
                "source_product_id": v.get("source_product_id"),
                "variant_attributes": v.get("variant_attributes") or {},
            }
        )
    if len(variants_raw) > 12:
        variant_preview.append(
            {"_note": f"{len(variants_raw) - 12} more sibling row(s) in this group"}
        )

    # Mirror n8n taxonomy payload naming for easy comparison with webhook output.
    path_value = tax.get("path")
    view: Dict[str, Any] = {
        "source_product_id": product.get("source_product_id"),
        "title": product.get("raw_title") or product.get("normalized_title"),
        "taxonomy": {
            "mapped_category": path_value,
            "categoryId": tax.get("category_id"),
            "categoryIds": tax.get("category_ids") or [],
            "confidence": tax.get("confidence"),
        },
        "attributes": {
            "extracted": product.get("attributes") or {},
            "identity": product.get("identity_attributes") or {},
            "variant": product.get("variant_attributes") or {},
        },
        "grouping": {
            "family_name": product.get("family_name"),
            "family_signature": _truncate(product.get("family_signature"), 120),
            "group_id": product.get("group_id"),
            "variant_axes": product.get("variant_axes") or [],
            "sibling_count": len(variants_raw),
            "variants": variant_preview,
        },
    }

    prov = product.get("provenance") or {}
    review = prov.get("review")
    if review:
        view["provenance"] = {"review": review}

    q = product.get("quality") or {}
    if q:
        view["quality"] = q

    content = product.get("content") or {}
    if content:
        view["content"] = content

    media = product.get("media") or {}
    if media:
        view["media"] = media

    return view


def _print_product_block(
    product: Dict[str, Any],
    index: int,
    batch_len: int,
    *,
    full_json: bool,
) -> None:
    _rule("-")
    pid = product.get("source_product_id") or "—"
    leaf = _taxonomy_leaf(product)
    status = _review_status(product)
    conf = (product.get("predicted_taxonomy") or {}).get("confidence")
    conf_s = f"{float(conf):.2f}" if conf is not None else "—"
    title = (product.get("raw_title") or "")[:56]
    if len(product.get("raw_title") or "") > 56:
        title += "…"
    print(f"  [{index}/{batch_len}]  {pid}")
    print(f"      title   : {title or '—'}")
    print(f"      leaf    : {leaf}")
    print(f"      tax conf: {conf_s}  ·  {status}")
    _rule("-")
    if full_json:
        print(json.dumps(product, indent=2, ensure_ascii=False))
    else:
        compact = build_cli_product_view(product)
        print(json.dumps(compact, indent=2, ensure_ascii=False))
    print()


def _print_run_summary(products: List[Dict[str, Any]]) -> None:
    need_review = len(_products_needing_color_review(products))
    _rule("=")
    print(f"  Summary  ·  {len(products)} product(s)  ·  {need_review} need color review")
    _rule("=")


def _print_review_section_header() -> None:
    print()
    _rule("=")
    print("  Manual review (color)")
    _rule("=")


def run_pipeline(
    *,
    input_path: str = "data/sample_input.json",
    batch_size: int = 5,
    verbose: bool = False,
    full_json: bool = False,
) -> None:
    supervisor = Supervisor(verbose=verbose)

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array of products, got {type(data).__name__}")

    print(f"\n  Input file : {input_path}")
    print(f"  Batch size : {batch_size} (grouping runs within each batch)\n")

    batches = list(create_batches(data, batch_size=batch_size))
    batch_total = len(batches)
    all_results: List[Dict[str, Any]] = []

    for batch_num, batch in enumerate(batches, start=1):
        _print_batch_header(batch_num, batch_total, len(batch))

        results = supervisor.run_batch_pipeline(batch)

        for i, result in enumerate(results, start=1):
            product_dict = result.model_dump()
            all_results.append(product_dict)
            _print_product_block(
                product_dict, i, len(results), full_json=full_json
            )

    _print_run_summary(all_results)

    review_queue = _products_needing_color_review(all_results)
    if not review_queue:
        print()
        _rule("-")
        print("  No manual color review — skipping prompts.")
        _rule("-")
    else:
        _print_review_section_header()

    for product in review_queue:
        review = product.get("provenance", {}).get("review", {})
        rc = review["color"]
        print(f"\n  Product : {product.get('source_product_id')}")
        print(f"  Image   : {rc.get('image_url')}")
        print(f"  Reason  : {rc.get('reason')}")

        try:
            user_color = input("  Enter color (or Enter to skip): ").strip()
        except EOFError:
            print("\n  (stdin closed — skipping remaining prompts)")
            break

        if not user_color:
            print("  Skipped.")
            continue

        updated = apply_review_update(product, "color", user_color)

        for idx, p in enumerate(all_results):
            if p.get("source_product_id") == product.get("source_product_id"):
                all_results[idx] = updated

        print("  Updated record (compact):")
        print(
            json.dumps(
                build_cli_product_view(updated),
                indent=2,
                ensure_ascii=False,
            )
        )

    print()
    _rule("-")
    print(
        "  Saving data/output_products.json (full records; use --full for full JSON on screen) …"
    )
    save_products(all_results)
    _rule("-")
    print("  Done.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Catalog enrichment pipeline (CLI)")
    parser.add_argument(
        "-i",
        "--input",
        default="data/sample_input.json",
        help="JSON array of raw products (default: data/sample_input.json)",
    )
    parser.add_argument(
        "-n",
        "--batch-size",
        type=int,
        default=5,
        metavar="N",
        help="Products per batch; grouping only merges within a batch (default: 5)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print per-stage and color-vision debug lines",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Print full CanonicalProduct JSON (default: compact pipeline view)",
    )
    args = parser.parse_args()
    run_pipeline(
        input_path=args.input,
        batch_size=args.batch_size,
        verbose=args.verbose,
        full_json=args.full,
    )


if __name__ == "__main__":
    main()
