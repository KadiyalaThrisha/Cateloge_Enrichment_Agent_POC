import csv
import io
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

import streamlit as st

# Must be first for local imports when running Streamlit directly.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.orchestrator.supervisor import Supervisor
from app.utils.catalog_api_client import health_check, run_enrichment_remote
from app.utils.review_actions import apply_review_update
from app.utils.review_queue import collect_reviews
from app.utils.storage import save_products

st.set_page_config(page_title="Catalog Enrichment Dashboard", layout="wide")

# Default API origin for the Streamlit sidebar (override with CATALOG_API_BASE_URL).
DEFAULT_CATALOG_API_BASE_URL = "http://127.0.0.1:8001"


def load_sample_products() -> List[Dict[str, Any]]:
    with open("data/sample_input.json", encoding="utf-8") as f:
        data = json.load(f)
    results = Supervisor().run_batch_pipeline(data)
    return [r.model_dump() for r in results]


def parse_uploaded_file(uploaded_file) -> List[Dict[str, Any]]:
    file_name = uploaded_file.name.lower()
    content = uploaded_file.getvalue().decode("utf-8", errors="ignore")

    if file_name.endswith(".json"):
        data = json.loads(content)
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return data
        raise ValueError("JSON must be an object or array of objects")

    if file_name.endswith(".jsonl"):
        rows: List[Dict[str, Any]] = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        return rows

    if file_name.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(content))
        return [dict(row) for row in reader]

    raise ValueError("Unsupported file type. Use .json, .jsonl, or .csv")


def initialize_state() -> None:
    if "products" not in st.session_state:
        st.session_state["products"] = []
        st.session_state["run_source"] = "No dataset loaded"
        st.session_state["run_ts"] = "-"
        st.session_state["edit_mode"] = False
        st.session_state["search"] = ""
        st.session_state["active_nav"] = "Overview"
    if "use_fastapi" not in st.session_state:
        flag = os.getenv("CATALOG_USE_FASTAPI", "true").strip().lower()
        st.session_state["use_fastapi"] = flag not in ("0", "false", "no", "off")
    if "api_base_url" not in st.session_state:
        st.session_state["api_base_url"] = (
            os.getenv("CATALOG_API_BASE_URL", DEFAULT_CATALOG_API_BASE_URL).strip()
        )
    if "last_run_backend" not in st.session_state:
        st.session_state["last_run_backend"] = None
    if "last_validations" not in st.session_state:
        st.session_state["last_validations"] = []
    if st.session_state.get("active_nav") == "CLI View":
        st.session_state["active_nav"] = "CLI"


def taxonomy_path(product: Dict[str, Any]) -> str:
    return ((product.get("predicted_taxonomy") or {}).get("path")) or "Unknown"


def taxonomy_conf(product: Dict[str, Any]) -> float:
    conf = (product.get("predicted_taxonomy") or {}).get("confidence")
    return float(conf) if conf is not None else 0.0


def stage_for_product(product: Dict[str, Any]) -> str:
    conf = taxonomy_conf(product)
    review = (product.get("provenance") or {}).get("review") or {}
    if review:
        return "Needs Review"
    if conf >= 0.95:
        return "Ready"
    if conf >= 0.80:
        return "Review Pending"
    return "Blocked"


def status_emoji(stage: str) -> str:
    if stage == "Ready":
        return "🟢"
    if stage == "Needs Review":
        return "🟡"
    if stage == "Blocked":
        return "🔴"
    return "🟠"


def build_queue_rows(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for p in products:
        rows.append(
            {
                "id": p.get("source_product_id") or "-",
                "name": p.get("raw_title") or "-",
                "taxonomy": taxonomy_path(p),
                "confidence": round(taxonomy_conf(p), 2),
                "issues": len(((p.get("provenance") or {}).get("review") or {}).keys()),
                "stage": stage_for_product(p),
                "reviewer": "Unassigned",
            }
        )
    rows.sort(key=lambda r: (r["stage"] != "Needs Review", r["confidence"]))
    return rows


def build_cli_like_view(product: Dict[str, Any]) -> Dict[str, Any]:
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
        variant_preview.append({"_note": f"{len(variants_raw) - 12} more sibling row(s)"})

    view: Dict[str, Any] = {
        "source_product_id": product.get("source_product_id"),
        "title": product.get("raw_title") or product.get("normalized_title"),
        "taxonomy": {
            "mapped_category": tax.get("path"),
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
            "family_signature": product.get("family_signature"),
            "group_id": product.get("group_id"),
            "variant_axes": product.get("variant_axes") or [],
            "sibling_count": len(variants_raw),
            "variants": variant_preview,
        },
    }

    prov = product.get("provenance") or {}
    if prov.get("review"):
        view["provenance"] = {"review": prov.get("review")}

    quality = product.get("quality") or {}
    if quality:
        view["quality"] = quality

    content = product.get("content") or {}
    if content:
        view["content"] = content

    media = product.get("media") or {}
    if media:
        view["media"] = media

    return view


def inject_styles() -> None:
    st.markdown(
        """
<style>
.block-container {padding-top: 1rem; padding-bottom: 1rem; max-width: 1800px;}
.card {background:#ffffff; border:1px solid #e2e8f0; border-radius:14px; padding:14px; margin-bottom:10px;}
.small {color:#64748b; font-size:12px;}
.kpi-value {font-size:30px; font-weight:700; color:#0f172a; line-height:1.1;}
.section-title {font-size:22px; font-weight:700; color:#0f172a; margin-bottom:8px;}
.pill {display:inline-block; border:1px solid #cbd5e1; border-radius:999px; padding:4px 10px; font-size:12px; color:#334155; margin-right:6px; margin-bottom:6px;}
.warn {background:#fff7ed; color:#9a3412; border-radius:10px; padding:8px 10px; margin-bottom:6px; font-size:13px;}
.alert {background:#fef2f2; color:#b91c1c; border-radius:10px; padding:8px 10px; margin-bottom:6px; font-size:13px;}
</style>
        """,
        unsafe_allow_html=True,
    )


inject_styles()
initialize_state()
products = st.session_state["products"]
review_items = collect_reviews(products)
queue_rows = build_queue_rows(products)

total_products = len(products)
avg_conf = round(sum(taxonomy_conf(p) for p in products) / total_products * 100, 1) if total_products else 0
pending_review = sum(1 for p in products if stage_for_product(p) == "Needs Review")
blocked = sum(1 for p in products if stage_for_product(p) == "Blocked")
published = sum(1 for p in products if stage_for_product(p) == "Ready")

st.markdown("## Catalog Enrichment Agent Dashboard")

header_cols = st.columns([3.5, 2.5, 1.1, 1.2, 1.4])
with header_cols[0]:
    st.session_state["search"] = st.text_input(
        "Search products, families, runs...",
        value=st.session_state.get("search", ""),
        label_visibility="collapsed",
        placeholder="Search products, families, runs...",
    )
with header_cols[1]:
    backend_hint = st.session_state.get("last_run_backend") or (
        "FastAPI" if st.session_state.get("use_fastapi") else "local"
    )
    st.caption(
        f"Run: `{st.session_state.get('run_source', 'n/a')}`  |  {st.session_state.get('run_ts', '-')}  |  **{backend_hint}**"
    )
with header_cols[2]:
    uploaded = st.file_uploader(
        "Upload Feed",
        type=["json", "jsonl", "csv"],
        label_visibility="collapsed",
    )
with header_cols[3]:
    run_clicked = st.button("Run Enrichment", use_container_width=True)
with header_cols[4]:
    publish_clicked = st.button("Publish Approved", use_container_width=True, type="primary")

if run_clicked:
    try:
        if uploaded is None:
            st.warning("Please upload a dataset file before running enrichment.")
            st.stop()

        raw_data = parse_uploaded_file(uploaded)
        source = uploaded.name

        with st.spinner("Running enrichment pipeline..."):
            if st.session_state.get("use_fastapi"):
                base = (st.session_state.get("api_base_url") or "").strip().rstrip("/")
                if not base:
                    st.error("Set API base URL in the sidebar (FastAPI backend).")
                    st.stop()
                products_out, validations_out = run_enrichment_remote(
                    raw_data,
                    base,
                    verbose=False,
                )
                st.session_state["products"] = products_out
                st.session_state["last_validations"] = validations_out
                st.session_state["last_run_backend"] = "FastAPI"
            else:
                results = Supervisor(verbose=False).run_batch_pipeline(raw_data)
                st.session_state["products"] = [r.model_dump() for r in results]
                st.session_state["last_validations"] = []
                st.session_state["last_run_backend"] = "local"
            st.session_state["run_source"] = source
            st.session_state["run_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.success(f"Pipeline completed for {len(st.session_state['products'])} products.")
        st.rerun()
    except Exception as exc:
        st.error(f"Run failed: {exc}")
        if st.session_state.get("use_fastapi"):
            st.info(
                "Tip: start the API with `uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8001`, "
                "or turn off **Use FastAPI backend** in the sidebar to run locally."
            )

if publish_clicked:
    publishable = [p for p in st.session_state["products"] if stage_for_product(p) == "Ready"]
    for p in publishable:
        p.setdefault("quality", {})
        p["quality"]["publish_status"] = "published"
        p["quality"]["published_at"] = datetime.now().isoformat()
    save_products(st.session_state["products"], file_path="data/output_products.json")
    st.success(f"Published {len(publishable)} items. Saved to data/output_products.json")

products = st.session_state["products"]
review_items = collect_reviews(products)
queue_rows = build_queue_rows(products)

query = st.session_state.get("search", "").strip().lower()
if query:
    queue_rows = [
        r
        for r in queue_rows
        if query in r["name"].lower() or query in r["id"].lower() or query in r["taxonomy"].lower()
    ]

layout = st.columns([1.2, 4.3, 1.7], gap="small")
nav_items = [
    "Overview",
    "Ingestion Runs",
    "Review Queue",
    "CLI",
    "Taxonomy Review",
    "Variant Issues",
    "Product Families",
    "Media Enrichment",
    "Analytics",
    "Audit Logs",
    "Configuration",
]
active_nav = st.session_state.get("active_nav", "Overview")

with layout[0]:
    st.markdown('<div class="card"><div class="section-title" style="font-size:18px;">Navigation</div>', unsafe_allow_html=True)
    for item in nav_items:
        if st.button(
            item,
            use_container_width=True,
            key=f"nav_{item}",
            type="primary" if item == active_nav else "secondary",
        ):
            st.session_state["active_nav"] = item
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("FastAPI backend", expanded=False):
        st.session_state["use_fastapi"] = st.checkbox(
            "Use FastAPI backend for Run Enrichment",
            value=st.session_state.get("use_fastapi", True),
            help="When on, the dashboard calls POST /api/v1/catalog/enrichment/run. "
            "Start the server with: uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8001. "
            "Default is on; set env CATALOG_USE_FASTAPI=false to start with local pipeline.",
        )
        st.session_state["api_base_url"] = st.text_input(
            "API base URL",
            value=st.session_state.get("api_base_url", DEFAULT_CATALOG_API_BASE_URL),
            help="No trailing slash. Must match where uvicorn is listening (default port 8001).",
        )
        if st.button("Test API health", use_container_width=True):
            ok, msg = health_check(st.session_state["api_base_url"].strip())
            if ok:
                st.success(f"Reachable: {msg}")
            else:
                st.error(msg)

with layout[1]:
    kpi_cols = st.columns(6)
    kpis = [
        ("Products Received", f"{total_products:,}", "Current loaded sample"),
        ("Auto-Enriched", f"{max(total_products - pending_review, 0):,}", "Estimated"),
        ("Pending Review", f"{pending_review:,}", f"{len(review_items)} flagged by provenance"),
        ("Validation Errors", f"{blocked:,}", "Below threshold"),
        ("Published Today", f"{published:,}", "Ready records"),
        ("Avg Confidence", f"{avg_conf}%", "Taxonomy confidence"),
    ]
    for col, (label, value, sub) in zip(kpi_cols, kpis):
        with col:
            st.markdown(
                f'<div class="card"><div class="small">{label}</div><div class="kpi-value">{value}</div><div class="small">{sub}</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown(
        """
<div class="card">
  <span class="pill">Status: Pending</span>
  <span class="pill">Category: All</span>
  <span class="pill">Confidence: &lt; 0.85</span>
  <span class="pill">Source: Merchant</span>
  <span class="pill">Assignee: Unassigned</span>
</div>
        """,
        unsafe_allow_html=True,
    )

    if not products:
        st.markdown("### No data loaded")
        st.info("Upload a JSON/JSONL/CSV file, then click **Run Enrichment** to populate this dashboard.")
    elif active_nav in {"Overview", "Review Queue"}:
        st.markdown('<div class="card"><div class="section-title" style="font-size:20px;">Review Queue</div></div>', unsafe_allow_html=True)
        st.dataframe(queue_rows, use_container_width=True, hide_index=True)

        selected_options = [r["id"] for r in queue_rows] if queue_rows else []
        selected_id = st.selectbox(
            "Open product detail",
            options=selected_options,
            index=0 if selected_options else None,
        )
        selected = next((p for p in products if p.get("source_product_id") == selected_id), {})

        st.markdown('<div class="card"><div class="section-title" style="font-size:20px;">Product Review Detail</div></div>', unsafe_allow_html=True)
        st.markdown(
            f"**Selected:** {selected.get('raw_title','-')}  \n"
            f"<span class='small'>Confidence: {taxonomy_conf(selected):.2f} • "
            f"Status: {stage_for_product(selected)} • Source: {selected.get('source_system','-')}</span>",
            unsafe_allow_html=True,
        )

        detail_cols = st.columns(2)
        with detail_cols[0]:
            st.markdown("#### Predicted Taxonomy")
            current_taxonomy = taxonomy_path(selected)
            taxonomy_override = st.text_input("Override taxonomy path", value=current_taxonomy, key=f"tax_{selected_id}")
            if st.button("Save Taxonomy Override", use_container_width=True):
                selected.setdefault("predicted_taxonomy", {})
                selected["predicted_taxonomy"]["path"] = taxonomy_override.strip() or "Unknown"
                selected.setdefault("quality", {})
                selected["quality"]["review_action"] = "taxonomy_overridden"
                st.success("Taxonomy updated.")
                st.rerun()

            st.markdown("#### Variant Axes")
            axes = selected.get("variant_axes") or []
            st.write(", ".join(axes) if axes else "None")

        with detail_cols[1]:
            st.markdown("#### Extracted Attributes")
            attrs = selected.get("attributes") or {}
            if attrs:
                for k, v in attrs.items():
                    st.write(f"- **{k}**: {v}")
            else:
                st.write("No attributes extracted")

            st.markdown("#### Validation Warnings")
            review_block = ((selected.get("provenance") or {}).get("review") or {})
            if review_block:
                for attr, detail in review_block.items():
                    st.markdown(f"<div class='warn'>• {attr}: {detail.get('reason','needs review')}</div>", unsafe_allow_html=True)
            else:
                st.success("No active warnings")

        st.markdown("#### Reviewer Actions")
        action_cols = st.columns(6)
        action_names = ["Approve", "Edit Attributes", "Override Taxonomy", "Reassign", "Send Back", "Reject"]
        for idx, action in enumerate(action_names):
            if action_cols[idx].button(action, use_container_width=True):
                selected.setdefault("quality", {})
                selected["quality"]["review_action"] = action.lower().replace(" ", "_")
                if action == "Edit Attributes":
                    st.session_state["edit_mode"] = True
                if action in {"Approve", "Send Back", "Reject"}:
                    selected["quality"]["review_status"] = action.lower().replace(" ", "_")
                st.toast(f"{action} action recorded")

        if st.session_state.get("edit_mode"):
            st.markdown("##### Quick Edit (for flagged attributes)")
            for item in review_items:
                if item.get("product_id") != selected.get("source_product_id"):
                    continue
                user_value = st.text_input(
                    f"Enter {item['attribute']}",
                    key=f"edit_{item['product_id']}_{item['attribute']}",
                )
                if st.button(f"Submit {item['attribute']}", key=f"submit_{item['product_id']}_{item['attribute']}"):
                    apply_review_update(selected, item["attribute"], user_value)
                    selected.setdefault("quality", {})
                    selected["quality"]["review_action"] = f"attribute_updated:{item['attribute']}"
                    st.success(f"Updated {item['attribute']} = {user_value}")
                    st.rerun()
    elif active_nav == "Ingestion Runs":
        st.markdown("### Ingestion Runs")
        st.info("Current run metadata and ingestion stats from your latest uploaded file.")
        st.write(f"- Run source: `{st.session_state.get('run_source', '-')}`")
        st.write(f"- Run time: `{st.session_state.get('run_ts', '-')}`")
        st.write(f"- Records processed: `{len(products)}`")
        st.write(f"- Supported upload formats: JSON, JSONL, CSV")
    elif active_nav == "CLI":
        st.markdown("### CLI")
        st.caption("Compact terminal-style product output rendered in the dashboard.")
        cli_ids = [p.get("source_product_id") or "-" for p in products]
        selected_cli_id = st.selectbox(
            "Select product for CLI output",
            options=cli_ids,
            index=0 if cli_ids else None,
            key="cli_view_selector",
        )
        selected_cli_product = next(
            (p for p in products if p.get("source_product_id") == selected_cli_id),
            {},
        )
        if selected_cli_product:
            st.markdown("#### Compact CLI JSON")
            st.json(build_cli_like_view(selected_cli_product), expanded=True)
            if st.checkbox("Show full product JSON", key="cli_full_json"):
                st.markdown("#### Full Product JSON")
                st.json(selected_cli_product, expanded=False)
    elif active_nav == "Taxonomy Review":
        st.markdown("### Taxonomy Review")
        taxonomy_rows = [
            {
                "product_id": p.get("source_product_id"),
                "title": p.get("raw_title"),
                "taxonomy": taxonomy_path(p),
                "confidence": round(taxonomy_conf(p), 2),
                "stage": stage_for_product(p),
            }
            for p in products
        ]
        st.dataframe(taxonomy_rows, use_container_width=True, hide_index=True)
    elif active_nav == "Variant Issues":
        st.markdown("### Variant Issues")
        issues_rows = [
            {
                "product_id": p.get("source_product_id"),
                "title": p.get("raw_title"),
                "variant_axes": ", ".join(p.get("variant_axes") or []) or "—",
                "axes_source": (p.get("quality") or {}).get("variant_axes_source") or "—",
                "issue": "Missing variant axes" if not (p.get("variant_axes") or []) else "None",
            }
            for p in products
        ]
        st.dataframe(issues_rows, use_container_width=True, hide_index=True)
    elif active_nav == "Product Families":
        st.markdown("### Product Families")
        families: Dict[str, Dict[str, Any]] = {}
        for p in products:
            gid = p.get("group_id") or f"ungrouped::{p.get('source_product_id')}"
            family = families.setdefault(
                gid,
                {
                    "family_name": p.get("family_name"),
                    "group_id": gid,
                    "taxonomy_leaf": taxonomy_path(p).split(">")[-1].strip(),
                    "family_size": 0,
                    "variant_axes": set(),
                    "color_values": set(),
                    "size_values": set(),
                    "product_ids": [],
                },
            )
            family["family_size"] += 1
            family["product_ids"].append(p.get("source_product_id"))
            for axis in (p.get("variant_axes") or []):
                family["variant_axes"].add(axis)
            variant_attrs = p.get("variant_attributes") or {}
            color_val = variant_attrs.get("color")
            size_val = variant_attrs.get("size")

            if isinstance(color_val, dict):
                color_name = color_val.get("name")
                if color_name:
                    family["color_values"].add(str(color_name))
            elif color_val:
                family["color_values"].add(str(color_val))

            if size_val:
                family["size_values"].add(str(size_val))

        family_rows = []
        for item in families.values():
            family_rows.append(
                {
                    "family_name": item["family_name"],
                    "group_id": item["group_id"],
                    "taxonomy_leaf": item["taxonomy_leaf"],
                    "family_size": item["family_size"],
                    "variant_axes": ", ".join(sorted(item["variant_axes"])) or "-",
                    "colors_in_family": ", ".join(sorted(item["color_values"])) or "-",
                    "sizes_in_family": ", ".join(sorted(item["size_values"])) or "-",
                    "product_ids": ", ".join(item["product_ids"]),
                }
            )
        st.dataframe(family_rows, use_container_width=True, hide_index=True)
    elif active_nav == "Analytics":
        st.markdown("### Analytics")
        stage_counts: Dict[str, int] = {}
        for p in products:
            s = stage_for_product(p)
            stage_counts[s] = stage_counts.get(s, 0) + 1
        st.bar_chart(stage_counts)
        conf_rows = [{"product_id": p.get("source_product_id"), "confidence": taxonomy_conf(p)} for p in products]
        st.dataframe(conf_rows, use_container_width=True, hide_index=True)
    elif active_nav == "Audit Logs":
        st.markdown("### Audit Logs")
        logs = []
        for p in products:
            quality = p.get("quality") or {}
            if quality:
                logs.append(
                    {
                        "product_id": p.get("source_product_id"),
                        "review_action": quality.get("review_action"),
                        "review_status": quality.get("review_status"),
                        "publish_status": quality.get("publish_status"),
                        "published_at": quality.get("published_at"),
                    }
                )
        if logs:
            st.dataframe(logs, use_container_width=True, hide_index=True)
        else:
            st.info("No audit actions recorded yet.")
    elif active_nav == "Media Enrichment":
        st.markdown("### Media Enrichment")
        st.warning("Media enrichment service is not wired yet. This section is ready for integration.")
    elif active_nav == "Configuration":
        st.markdown("### Configuration")
        st.markdown(
            "**FastAPI microservice** — same enrichment logic, exposed over HTTP for other clients."
        )
        st.code(
            "uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8001",
            language="bash",
        )
        st.markdown(
            f"- Docs: [{DEFAULT_CATALOG_API_BASE_URL}/docs]({DEFAULT_CATALOG_API_BASE_URL}/docs)\n"
            "- Health: `GET /api/v1/health`\n"
            "- Run: `POST /api/v1/catalog/enrichment/run`"
        )
        st.markdown("Use the **FastAPI backend** expander in the left sidebar to point the dashboard at your API.")
        vals = st.session_state.get("last_validations") or []
        if vals:
            st.markdown("#### Last run validations (from API)")
            st.dataframe(vals, use_container_width=True, hide_index=True)
    else:
        st.markdown(f"### {active_nav}")
        st.info("This section is available for configuration/extensions.")

    st.markdown("#### Export")
    export_payload = json.dumps(st.session_state["products"], indent=2, ensure_ascii=False)
    st.download_button(
        "Download Current Run JSON",
        data=export_payload,
        file_name="enrichment_run_output.json",
        mime="application/json",
        use_container_width=True,
    )

with layout[2]:
    if not products:
        st.markdown('<div class="card"><div class="section-title" style="font-size:18px;">Section Help</div>', unsafe_allow_html=True)
        st.write("No run is available yet.")
        st.write("1) Upload a feed")
        st.write("2) Click Run Enrichment")
        st.write("3) Review and publish approved records")
        st.markdown("</div>", unsafe_allow_html=True)
    elif active_nav in {"Overview", "Review Queue", "CLI", "Analytics"}:
        st.markdown('<div class="card"><div class="section-title" style="font-size:18px;">Today\'s Run Summary</div>', unsafe_allow_html=True)
        st.markdown(f"- {total_products} products processed")
        st.markdown(f"- {published} ready for publish")
        st.markdown(f"- {pending_review} in review queue")
        st.markdown(f"- {blocked} blocked by confidence")
        st.markdown(f"- {len(review_items)} review flags from provenance")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="card"><div class="section-title" style="font-size:18px;">High Priority Alerts</div>', unsafe_allow_html=True)
        if blocked:
            st.markdown("<div class='alert'>Taxonomy confidence low for blocked records</div>", unsafe_allow_html=True)
        if pending_review:
            st.markdown("<div class='alert'>Manual attribute review pending</div>", unsafe_allow_html=True)
        st.markdown("<div class='alert'>Media enrichment status is not wired yet</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="card"><div class="section-title" style="font-size:18px;">Operational Analytics</div>', unsafe_allow_html=True)
        category_counts: Dict[str, int] = {}
        for p in products:
            leaf = taxonomy_path(p).split(">")[-1].strip()
            category_counts[leaf] = category_counts.get(leaf, 0) + 1
        if category_counts:
            for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
                st.progress(min(count / max(category_counts.values()), 1.0), text=f"{cat}: {count}")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="card"><div class="section-title" style="font-size:18px;">Issue Summary</div>', unsafe_allow_html=True)
        st.write(f"Taxonomy mismatches: {blocked}")
        st.write(f"Missing attributes: {len(review_items)}")
        st.write(f"Variant conflicts: {sum(1 for p in products if not (p.get('variant_axes') or []))}")
        st.write("Media gaps: N/A")
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.markdown('<div class="card"><div class="section-title" style="font-size:18px;">Section Help</div>', unsafe_allow_html=True)
        st.write(f"You are in **{active_nav}**.")
        st.write("Use Run Enrichment after upload, then work section-by-section.")
        st.markdown("</div>", unsafe_allow_html=True)