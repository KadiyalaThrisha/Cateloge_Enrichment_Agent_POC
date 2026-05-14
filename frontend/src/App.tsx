import { useCallback, useEffect, useMemo, useState } from "react";
import "./App.css";
import { getApiBase } from "./api";
import {
  type Nav,
  NAV,
  type Product,
  type QueueRow,
  attrKeysPreview,
  buildCliView,
  buildFamilyRows,
  buildQueueRows,
  collectReviews,
  applyReviewUpdate,
  parseFeedFile,
  recordKeyCount,
  stageForProduct,
  taxonomyConf,
  taxonomyPath,
} from "./productUtils";

const API_PREFIX = "/api/v1";
const THEME_STORAGE_KEY = "catalog-dashboard-theme";

type ThemeMode = "light" | "dark";

function readInitialTheme(): ThemeMode {
  if (typeof document === "undefined") return "light";
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "dark" || attr === "light") return attr;
  return "light";
}

function stageBadgeClass(stage: string): string {
  if (stage === "Ready") return "badge badge-ready";
  if (stage === "Blocked") return "badge badge-blocked";
  return "badge badge-review";
}

function confClass(conf: number): string {
  if (conf >= 0.85) return "conf-pill conf-high";
  if (conf >= 0.7) return "conf-pill conf-mid";
  return "conf-pill conf-low";
}

function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function AttributeDetailBlock({
  title,
  data,
  variant = "rest",
}: {
  title: string;
  data: Record<string, unknown>;
  variant?: "first" | "rest";
}) {
  const entries = Object.entries(data || {});
  const hClass = variant === "first" ? "panel-heading" : "panel-heading panel-heading-spaced";
  return (
    <>
      <h4 className={hClass}>{title}</h4>
      <div className="attr-panel">
        {entries.length ? (
          entries.map(([k, v]) => (
            <div key={k} className="small attr-row">
              <strong>{k}:</strong> {typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}
            </div>
          ))
        ) : (
          <span className="small">None</span>
        )}
      </div>
    </>
  );
}

export default function App() {
  const [nav, setNav] = useState<Nav>("Overview");
  const [search, setSearch] = useState("");
  const [apiBase, setApiBase] = useState(() => getApiBase());
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [runLoading, setRunLoading] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runOk, setRunOk] = useState<string | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [runSource, setRunSource] = useState("No dataset loaded");
  const [runTs, setRunTs] = useState("-");
  const [selectedId, setSelectedId] = useState("");
  const [taxDraft, setTaxDraft] = useState("");
  const [editMode, setEditMode] = useState(false);
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [lastValidations, setLastValidations] = useState<unknown[]>([]);
  const [showFullJson, setShowFullJson] = useState(false);
  const [healthMsg, setHealthMsg] = useState<string | null>(null);
  const [theme, setTheme] = useState<ThemeMode>(readInitialTheme);

  const reviewItems = useMemo(() => collectReviews(products), [products]);

  const queueRows = useMemo(() => {
    const rows = buildQueueRows(products);
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        r.id.toLowerCase().includes(q) ||
        r.taxonomy.toLowerCase().includes(q),
    );
  }, [products, search]);

  const totalProducts = products.length;
  const pendingReview = useMemo(
    () => products.filter((p) => stageForProduct(p) === "Needs Review").length,
    [products],
  );
  const blocked = useMemo(() => products.filter((p) => stageForProduct(p) === "Blocked").length, [products]);
  const published = useMemo(() => products.filter((p) => stageForProduct(p) === "Ready").length, [products]);
  const avgConf = useMemo(() => {
    if (!totalProducts) return 0;
    const sum = products.reduce((s, p) => s + taxonomyConf(p), 0);
    return Math.round((sum / totalProducts) * 1000) / 10;
  }, [products, totalProducts]);

  const selected = useMemo(
    () => products.find((p) => String(p.source_product_id) === selectedId),
    [products, selectedId],
  );

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      /* ignore quota / private mode */
    }
    const meta = document.getElementById("theme-color-meta") as HTMLMetaElement | null;
    if (meta) meta.setAttribute("content", theme === "dark" ? "#0f172a" : "#f1f5f9");
  }, [theme]);

  useEffect(() => {
    const queueIds = queueRows.map((r) => r.id);
    const allIds = products.map((p) => String(p.source_product_id));
    if (!allIds.length && !queueIds.length) {
      setSelectedId("");
      setTaxDraft("");
      return;
    }
    if (nav === "Extracted Attributes") {
      if (selectedId && allIds.includes(selectedId)) return;
      if (allIds.length) {
        setSelectedId(allIds[0]);
      }
      return;
    }
    if (!queueIds.length) {
      setSelectedId("");
      setTaxDraft("");
      return;
    }
    if (!queueIds.includes(selectedId)) {
      setSelectedId(queueIds[0]);
    }
  }, [queueRows, selectedId, nav, products]);

  useEffect(() => {
    if (selected) {
      setTaxDraft(taxonomyPath(selected));
    }
  }, [selected, selectedId]);

  const checkHealth = useCallback(async () => {
    const base = apiBase.replace(/\/$/, "");
    setHealthMsg(null);
    try {
      const res = await fetch(`${base}${API_PREFIX}/health`);
      const txt = await res.text();
      setHealthMsg(res.ok ? `OK — ${txt}` : `HTTP ${res.status}: ${txt}`);
    } catch (e) {
      setHealthMsg(`Error: ${String(e)}`);
    }
  }, [apiBase]);

  const runEnrichment = useCallback(async () => {
    setRunError(null);
    setRunOk(null);
    if (!pendingFile) {
      setRunError("Please choose a feed file (JSON, JSONL, or CSV) before running enrichment.");
      return;
    }
    setRunLoading(true);
    try {
      const text = await pendingFile.text();
      const items = parseFeedFile(pendingFile.name, text);
      const base = apiBase.replace(/\/$/, "");
      const res = await fetch(`${base}${API_PREFIX}/catalog/enrichment/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items, verbose: false }),
      });
      if (!res.ok) {
        const errBody = await res.text();
        throw new Error(`${res.status}: ${errBody.slice(0, 500)}`);
      }
      const data = (await res.json()) as { products?: Product[]; validations?: unknown[] };
      setProducts(data.products ?? []);
      setLastValidations(data.validations ?? []);
      setRunSource(pendingFile.name);
      setRunTs(new Date().toLocaleString());
      setRunOk(`Pipeline completed for ${(data.products ?? []).length} products.`);
      setNav("Overview");
      setEditMode(false);
    } catch (e) {
      setRunError(String(e));
    } finally {
      setRunLoading(false);
    }
  }, [apiBase, pendingFile]);

  const updateProduct = useCallback((id: string, updater: (p: Product) => Product) => {
    setProducts((prev) => prev.map((p) => (String(p.source_product_id) === id ? updater(structuredClone(p)) : p)));
  }, []);

  const saveTaxonomy = () => {
    if (!selected) return;
    const id = String(selected.source_product_id);
    updateProduct(id, (p) => {
      const tax = (p.predicted_taxonomy as Record<string, unknown>) || {};
      p.predicted_taxonomy = { ...tax, path: taxDraft.trim() || "Unknown" };
      const q = (p.quality as Record<string, unknown>) || {};
      p.quality = { ...q, review_action: "taxonomy_overridden" };
      return p;
    });
  };

  const recordAction = (action: string) => {
    if (!selected) return;
    const id = String(selected.source_product_id);
    updateProduct(id, (p) => {
      const q = (p.quality as Record<string, unknown>) || {};
      const next = { ...q, review_action: action.toLowerCase().replace(/ /g, "_") };
      if (["approve", "send_back", "reject"].includes(action.toLowerCase().replace(/ /g, "_"))) {
        next.review_status = action.toLowerCase().replace(/ /g, "_");
      }
      p.quality = next;
      return p;
    });
    if (action === "Edit Attributes") setEditMode(true);
  };

  const submitAttrEdit = (attr: string) => {
    if (!selected) return;
    const id = String(selected.source_product_id);
    const val = editValues[`${id}_${attr}`] ?? "";
    if (!val.trim()) return;
    setProducts((prev) =>
      prev.map((p) => {
        if (String(p.source_product_id) !== id) return p;
        const u = applyReviewUpdate(p, attr, val.trim());
        const q = (u.quality as Record<string, unknown>) || {};
        u.quality = { ...q, review_action: `attribute_updated:${attr}` };
        return u;
      }),
    );
  };

  const publishApproved = () => {
    const next = products.map((p) => {
      if (stageForProduct(p) !== "Ready") return p;
      const c = structuredClone(p) as Product;
      const q = (c.quality as Record<string, unknown>) || {};
      c.quality = { ...q, publish_status: "published", published_at: new Date().toISOString() };
      return c;
    });
    setProducts(next);
    const readyCount = products.filter((p) => stageForProduct(p) === "Ready").length;
    downloadJson("output_products.json", next);
    setRunOk(`Marked ${readyCount} ready item(s) as published (client-side) and downloaded JSON.`);
  };

  const familyRows = useMemo(() => buildFamilyRows(products), [products]);

  const categoryBars = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const p of products) {
      const leaf = taxonomyPath(p).split(">").pop()?.trim() || taxonomyPath(p);
      counts[leaf] = (counts[leaf] || 0) + 1;
    }
    const max = Math.max(1, ...Object.values(counts));
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([label, count]) => ({ label, count, pct: (count / max) * 100 }));
  }, [products]);

  const analyticsStages = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of products) {
      const s = stageForProduct(p);
      m[s] = (m[s] || 0) + 1;
    }
    return m;
  }, [products]);

  const showReviewWorkspace = nav === "Overview" || nav === "Review Queue";
  const showRightRail =
    !totalProducts ||
    nav === "Overview" ||
    nav === "Review Queue" ||
    nav === "CLI" ||
    nav === "Analytics";

  return (
    <div className="shell">
      <header className="header">
        <div className="header-inner">
          <h1 className="title">
            <span className="title-line title-line--primary">Catalog Enrichment</span>
            <span className="title-line title-line--secondary">Agent Dashboard</span>
          </h1>
          <input
            className="search"
            placeholder="Search products, families, runs..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="run-caption" title={`${runSource} | ${runTs} | FastAPI`}>
            Run: <code>{runSource}</code> | {runTs} | <strong>FastAPI</strong>
          </div>
          <div className="theme-switch" role="group" aria-label="Color theme">
            <button
              type="button"
              className={theme === "light" ? "is-active" : ""}
              onClick={() => setTheme("light")}
              aria-pressed={theme === "light"}
            >
              Light
            </button>
            <button
              type="button"
              className={theme === "dark" ? "is-active" : ""}
              onClick={() => setTheme("dark")}
              aria-pressed={theme === "dark"}
            >
              Dark
            </button>
          </div>
          <div className="file-wrap">
            <input
              type="file"
              accept=".json,.jsonl,.csv,application/json,text/csv"
              onChange={(e) => setPendingFile(e.target.files?.[0] ?? null)}
            />
          </div>
          <button
            type="button"
            className={`btn btn-dark${runLoading ? " btn-loading" : ""}`}
            disabled={runLoading}
            onClick={() => void runEnrichment()}
          >
            {!runLoading ? "Run Enrichment" : "Running"}
          </button>
          <button type="button" className="btn btn-emerald" disabled={!totalProducts} onClick={publishApproved}>
            Publish Approved
          </button>
        </div>
      </header>

      <div className="body-grid">
        <aside className="nav-card">
          <div className="card">
            <div className="section-title-sm">Navigation</div>
            {NAV.map((item) => (
              <button
                key={item}
                type="button"
                className={`nav-btn${nav === item ? " active" : ""}`}
                onClick={() => setNav(item)}
              >
                {item}
              </button>
            ))}
            <details className="expander">
              <summary>FastAPI backend</summary>
              <div className="expander-body">
                <label className="small">API base URL</label>
                <input className="input-full" value={apiBase} onChange={(e) => setApiBase(e.target.value.trim())} />
                <button type="button" className="btn btn-block" onClick={() => void checkHealth()}>
                  Test API health
                </button>
                {healthMsg && <p className="small" style={{ marginTop: "0.35rem" }}>{healthMsg}</p>}
              </div>
            </details>
          </div>
        </aside>

        <main>
          {runError && (
            <div className="flash flash--err" role="alert">
              {runError}
            </div>
          )}
          {runOk && (
            <div className="flash flash--ok" role="status">
              {runOk}
            </div>
          )}

          <div className="kpi-grid">
            {[
              ["Products Received", String(totalProducts), "Current run"],
              ["Auto-Enriched", String(Math.max(totalProducts - pendingReview, 0)), "Estimated"],
              ["Pending Review", String(pendingReview), `${reviewItems.length} flagged by provenance`],
              ["Validation Errors", String(blocked), "Below threshold"],
              ["Published Today", String(published), "Ready records"],
              ["Avg Confidence", `${avgConf}%`, "Taxonomy confidence"],
            ].map(([label, value, sub]) => (
              <div key={label} className="card kpi-card">
                <div className="small">{label}</div>
                <div className="kpi-value">{value}</div>
                <div className="small">{sub}</div>
              </div>
            ))}
          </div>

          <div className="card">
            <span className="pill">Status: Pending</span>
            <span className="pill">Category: All</span>
            <span className="pill">Confidence: &lt; 0.85</span>
            <span className="pill">Source: Merchant</span>
            <span className="pill">Assignee: Unassigned</span>
          </div>

          {!totalProducts && (
            <div className="info-banner">
              <strong>No data loaded.</strong> Choose a JSON / JSONL / CSV feed in the header, then click{" "}
              <strong>Run Enrichment</strong>.
            </div>
          )}

          {showReviewWorkspace && totalProducts > 0 && (
            <>
              <div className="card">
                <div className="section-title" style={{ fontSize: "1.05rem" }}>
                  Review Queue
                </div>
                <table className="data">
                  <thead>
                    <tr>
                      <th>Product / Family</th>
                      <th>Taxonomy</th>
                      <th>Confidence</th>
                      <th>Issues</th>
                      <th>Status</th>
                      <th>Reviewer</th>
                    </tr>
                  </thead>
                  <tbody>
                    {queueRows.map((r: QueueRow) => (
                      <tr
                        key={r.id}
                        className={r.id === selectedId ? "row-selected" : ""}
                        style={{ cursor: "pointer" }}
                        onClick={() => setSelectedId(r.id)}
                      >
                        <td>
                          <strong>{r.name}</strong>
                          <div className="small">Open detailed reviewer workspace</div>
                        </td>
                        <td>{r.taxonomy}</td>
                        <td>
                          <span className={confClass(r.confidence)}>{r.confidence}</span>
                        </td>
                        <td>{r.issues}</td>
                        <td>
                          <span className={stageBadgeClass(r.stage)}>{r.stage}</span>
                        </td>
                        <td>{r.reviewer}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="card">
                <label className="small">Open product detail</label>
                <select className="input-full" value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
                  {queueRows.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.id} — {r.name.slice(0, 48)}
                    </option>
                  ))}
                </select>
              </div>

              {selected && (
                <div className="card">
                  <div className="section-title" style={{ fontSize: "1.05rem" }}>
                    Product Review Detail
                  </div>
                  <p style={{ margin: "0.25rem 0 1rem" }}>
                    <strong>Selected:</strong> {String(selected.raw_title ?? "-")}
                    <br />
                    <span className="small">
                      Confidence: {taxonomyConf(selected).toFixed(2)} • Status: {stageForProduct(selected)} • Source:{" "}
                      {String(selected.source_system ?? "-")}
                    </span>
                  </p>

                  <div className="two-col">
                    <div>
                      <h4 className="panel-heading">Predicted Taxonomy</h4>
                      <div className="taxonomy-highlight">{taxonomyPath(selected)}</div>
                      <label className="small">Override taxonomy path</label>
                      <input className="input-full" value={taxDraft} onChange={(e) => setTaxDraft(e.target.value)} />
                      <button type="button" className="btn btn-block" onClick={saveTaxonomy}>
                        Save Taxonomy Override
                      </button>
                      <h4 className="panel-heading panel-heading-spaced">Variant Axes</h4>
                      <p className="small" style={{ margin: 0 }}>
                        {((selected.variant_axes as string[]) || []).join(", ") || "None"}
                      </p>
                    </div>
                    <div>
                      <h4 className="panel-heading">Extracted Attributes</h4>
                      <div className="attr-panel">
                        {Object.entries((selected.attributes as Record<string, unknown>) || {}).length ? (
                          Object.entries((selected.attributes as Record<string, unknown>) || {}).map(([k, v]) => (
                            <div key={k} className="small attr-row">
                              <strong>{k}:</strong> {typeof v === "object" ? JSON.stringify(v) : String(v)}
                            </div>
                          ))
                        ) : (
                          <span className="small">No attributes extracted</span>
                        )}
                      </div>
                      <h4 className="panel-heading panel-heading-spaced">Validation Warnings</h4>
                      {(() => {
                        const rev = ((selected.provenance as Record<string, unknown>)?.review || {}) as Record<
                          string,
                          { reason?: string }
                        >;
                        const keys = Object.keys(rev);
                        if (!keys.length) {
                          return <div className="success-line">No active warnings</div>;
                        }
                        return keys.map((attr) => (
                          <div key={attr} className="warn">
                            • {attr}: {rev[attr]?.reason ?? "needs review"}
                          </div>
                        ));
                      })()}
                    </div>
                  </div>

                  <h4 style={{ margin: "1rem 0 0.5rem" }}>Reviewer Actions</h4>
                  <div className="actions-row">
                    {["Approve", "Edit Attributes", "Override Taxonomy", "Reassign", "Send Back", "Reject"].map((a) => (
                      <button
                        key={a}
                        type="button"
                        className={`action-btn${a === "Approve" ? " approve" : ""}${a === "Reject" ? " reject" : ""}`}
                        onClick={() => recordAction(a)}
                      >
                        {a}
                      </button>
                    ))}
                  </div>

                  {editMode &&
                    reviewItems
                      .filter((it) => String(it.product_id ?? "") === String(selected.source_product_id))
                      .map((it) => (
                        <div key={it.attribute} className="card" style={{ marginTop: "0.75rem" }}>
                          <div className="small">Flagged: {it.attribute}</div>
                          <input
                            className="input-full"
                            placeholder={`Enter ${it.attribute}`}
                            value={editValues[`${selectedId}_${it.attribute}`] ?? ""}
                            onChange={(e) =>
                              setEditValues((prev) => ({ ...prev, [`${selectedId}_${it.attribute}`]: e.target.value }))
                            }
                          />
                          <button
                            type="button"
                            className="btn btn-dark"
                            style={{ marginTop: "0.35rem" }}
                            onClick={() => submitAttrEdit(it.attribute)}
                          >
                            Submit {it.attribute}
                          </button>
                        </div>
                      ))}
                </div>
              )}
            </>
          )}

          {nav === "Ingestion Runs" && (
            <div className="card">
              <h3 className="section-title">Ingestion Runs</h3>
              <p className="small">Current run metadata (same as Streamlit).</p>
              <ul className="compact">
                <li>
                  Run source: <code>{runSource}</code>
                </li>
                <li>Run time: {runTs}</li>
                <li>Records processed: {totalProducts}</li>
                <li>Supported: JSON, JSONL, CSV</li>
              </ul>
            </div>
          )}

          {nav === "CLI" && totalProducts > 0 && (
            <div className="card">
              <h3 className="section-title">CLI</h3>
              <p className="small">Compact terminal-style product output.</p>
              <select className="input-full" value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
                {products.map((p) => (
                  <option key={String(p.source_product_id)} value={String(p.source_product_id)}>
                    {String(p.source_product_id)}
                  </option>
                ))}
              </select>
              {selected && (
                <>
                  <h4 className="small" style={{ marginTop: "0.75rem" }}>
                    Compact CLI JSON
                  </h4>
                  <pre className="json-pre mono">{JSON.stringify(buildCliView(selected), null, 2)}</pre>
                  <label className="checkbox-row" style={{ marginTop: "0.5rem" }}>
                    <input type="checkbox" checked={showFullJson} onChange={(e) => setShowFullJson(e.target.checked)} />
                    Show full product JSON
                  </label>
                  {showFullJson && (
                    <pre className="json-pre mono" style={{ marginTop: "0.5rem" }}>
                      {JSON.stringify(selected, null, 2)}
                    </pre>
                  )}
                </>
              )}
            </div>
          )}

          {nav === "Taxonomy Review" && totalProducts > 0 && (
            <div className="card">
              <h3 className="section-title">Taxonomy Review</h3>
              <table className="data">
                <thead>
                  <tr>
                    <th>product_id</th>
                    <th>title</th>
                    <th>taxonomy</th>
                    <th>confidence</th>
                    <th>stage</th>
                  </tr>
                </thead>
                <tbody>
                  {products.map((p) => (
                    <tr key={String(p.source_product_id)}>
                      <td className="mono">{String(p.source_product_id)}</td>
                      <td>{String(p.raw_title ?? "")}</td>
                      <td>{taxonomyPath(p)}</td>
                      <td>{taxonomyConf(p).toFixed(2)}</td>
                      <td>
                        <span className={stageBadgeClass(stageForProduct(p))}>{stageForProduct(p)}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {nav === "Extracted Attributes" && totalProducts > 0 && (
            <>
              <div className="card">
                <h3 className="section-title">Extracted Attributes</h3>
                <p className="small" style={{ marginBottom: "0.75rem" }}>
                  Normalized fields from the attribute stage: general <code>attributes</code>,{" "}
                  <code>identity_attributes</code>, and <code>variant_attributes</code>. Click a row or use the
                  picker for full detail and JSON.
                </p>
                <table className="data">
                  <thead>
                    <tr>
                      <th>product_id</th>
                      <th>title</th>
                      <th>general</th>
                      <th>identity</th>
                      <th>variant</th>
                      <th>key preview</th>
                    </tr>
                  </thead>
                  <tbody>
                    {products.map((p) => {
                      const id = String(p.source_product_id);
                      return (
                        <tr
                          key={id}
                          className={id === selectedId ? "row-selected" : ""}
                          style={{ cursor: "pointer" }}
                          onClick={() => setSelectedId(id)}
                        >
                          <td className="mono">{id}</td>
                          <td>{String(p.raw_title ?? "")}</td>
                          <td>{recordKeyCount(p.attributes)}</td>
                          <td>{recordKeyCount(p.identity_attributes)}</td>
                          <td>{recordKeyCount(p.variant_attributes)}</td>
                          <td className="small">{attrKeysPreview(p)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div className="card">
                <label className="small">Product for detail</label>
                <select className="input-full" value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
                  {products.map((p) => {
                    const id = String(p.source_product_id);
                    return (
                      <option key={id} value={id}>
                        {id} — {String(p.raw_title ?? "").slice(0, 52)}
                      </option>
                    );
                  })}
                </select>
              </div>

              {selected && (
                <div className="card">
                  <h3 className="section-title">Attribute details</h3>
                  <p className="small" style={{ margin: "0.15rem 0 1rem" }}>
                    <strong>{String(selected.raw_title ?? "—")}</strong>
                    <span className="small" style={{ display: "block", marginTop: "0.25rem" }}>
                      Taxonomy: {taxonomyPath(selected)} · Stage: {stageForProduct(selected)}
                    </span>
                  </p>

                  <AttributeDetailBlock
                    title="General attributes"
                    data={(selected.attributes as Record<string, unknown>) || {}}
                    variant="first"
                  />
                  <AttributeDetailBlock
                    title="Identity attributes"
                    data={(selected.identity_attributes as Record<string, unknown>) || {}}
                  />
                  <AttributeDetailBlock
                    title="Variant attributes"
                    data={(selected.variant_attributes as Record<string, unknown>) || {}}
                  />

                  <h4 className="panel-heading panel-heading-spaced">Review flags (provenance)</h4>
                  {(() => {
                    const rev = ((selected.provenance as Record<string, unknown>)?.review || {}) as Record<
                      string,
                      { reason?: string }
                    >;
                    const keys = Object.keys(rev);
                    if (!keys.length) {
                      return <div className="success-line">No attribute-level review flags</div>;
                    }
                    return keys.map((attr) => (
                      <div key={attr} className="warn">
                        • {attr}: {rev[attr]?.reason ?? "needs review"}
                      </div>
                    ));
                  })()}

                  <h4 className="panel-heading panel-heading-spaced">Raw attribute payload (JSON)</h4>
                  <pre className="json-pre mono">
                    {JSON.stringify(
                      {
                        attributes: selected.attributes ?? {},
                        identity_attributes: selected.identity_attributes ?? {},
                        variant_attributes: selected.variant_attributes ?? {},
                      },
                      null,
                      2,
                    )}
                  </pre>
                </div>
              )}
            </>
          )}

          {nav === "Variant Issues" && totalProducts > 0 && (
            <div className="card">
              <h3 className="section-title">Variant Issues</h3>
              <p className="small" style={{ marginBottom: "0.75rem" }}>
                Axes are derived in grouping: first from multi-SKU variant-attribute differences; if none,
                Groq may infer axis names for the family when <code>VARIANT_AXES_LLM_ENABLED</code> is on
                (default). <code>quality.variant_axes_source</code> is{" "}
                <code>rule_discriminating_values</code>, <code>llm_inference</code>, or <code>none</code>.
              </p>
              <table className="data">
                <thead>
                  <tr>
                    <th>product_id</th>
                    <th>title</th>
                    <th>variant_axes</th>
                    <th>source</th>
                    <th>issue</th>
                  </tr>
                </thead>
                <tbody>
                  {products.map((p) => {
                    const axes = (p.variant_axes as string[]) || [];
                    const q = (p.quality as Record<string, unknown>) || {};
                    const src = String(q.variant_axes_source ?? "—");
                    const issue = axes.length ? "None" : "Missing variant axes";
                    return (
                      <tr key={String(p.source_product_id)}>
                        <td className="mono">{String(p.source_product_id)}</td>
                        <td>{String(p.raw_title ?? "")}</td>
                        <td>{axes.length ? axes.join(", ") : "—"}</td>
                        <td className="small">{src}</td>
                        <td>{issue}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {nav === "Product Families" && totalProducts > 0 && (
            <div className="card">
              <h3 className="section-title">Product Families</h3>
              <table className="data">
                <thead>
                  <tr>
                    <th>family_name</th>
                    <th>group_id</th>
                    <th>taxonomy_leaf</th>
                    <th>family_size</th>
                    <th>variant_axes</th>
                    <th>colors</th>
                    <th>sizes</th>
                    <th>product_ids</th>
                  </tr>
                </thead>
                <tbody>
                  {familyRows.map((r) => (
                    <tr key={r.group_id}>
                      <td>{r.family_name}</td>
                      <td className="mono">{r.group_id}</td>
                      <td>{r.taxonomy_leaf}</td>
                      <td>{r.family_size}</td>
                      <td>{r.variant_axes}</td>
                      <td>{r.colors_in_family}</td>
                      <td>{r.sizes_in_family}</td>
                      <td className="mono">{r.product_ids}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {nav === "Media Enrichment" && (
            <div className="card">
              <h3 className="section-title">Media Enrichment</h3>
              <div className="alert">Media enrichment service is not wired yet. This section is ready for integration.</div>
            </div>
          )}

          {nav === "Analytics" && totalProducts > 0 && (
            <div className="card">
              <h3 className="section-title">Analytics</h3>
              <div className="small" style={{ marginBottom: "0.75rem" }}>
                Stage distribution
              </div>
              {Object.entries(analyticsStages).map(([stage, count]) => (
                <div key={stage} className="progress-row">
                  <div className="progress-label">
                    <span>{stage}</span>
                    <span>{count}</span>
                  </div>
                  <div className="progress-track">
                    <div
                      className="progress-fill"
                      style={{ width: `${(count / Math.max(...Object.values(analyticsStages), 1)) * 100}%` }}
                    />
                  </div>
                </div>
              ))}
              <table className="data" style={{ marginTop: "1rem" }}>
                <thead>
                  <tr>
                    <th>product_id</th>
                    <th>confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {products.map((p) => (
                    <tr key={String(p.source_product_id)}>
                      <td className="mono">{String(p.source_product_id)}</td>
                      <td>{taxonomyConf(p).toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {nav === "Audit Logs" && (
            <div className="card">
              <h3 className="section-title">Audit Logs</h3>
              {products.some((p) => {
                const q = p.quality as Record<string, unknown> | undefined;
                return q && Object.keys(q).length > 0;
              }) ? (
                <table className="data">
                  <thead>
                    <tr>
                      <th>product_id</th>
                      <th>review_action</th>
                      <th>review_status</th>
                      <th>publish_status</th>
                      <th>published_at</th>
                    </tr>
                  </thead>
                  <tbody>
                    {products
                      .filter((p) => {
                        const q = p.quality as Record<string, unknown> | undefined;
                        return q && Object.keys(q).length > 0;
                      })
                      .map((p) => {
                        const q = (p.quality as Record<string, unknown>) || {};
                        return (
                          <tr key={String(p.source_product_id)}>
                            <td className="mono">{String(p.source_product_id)}</td>
                            <td>{String(q.review_action ?? "")}</td>
                            <td>{String(q.review_status ?? "")}</td>
                            <td>{String(q.publish_status ?? "")}</td>
                            <td className="mono">{String(q.published_at ?? "")}</td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              ) : (
                <p className="small">No audit actions recorded yet.</p>
              )}
            </div>
          )}

          {nav === "Configuration" && (
            <div className="card">
              <h3 className="section-title">Configuration</h3>
              <p className="small">
                <strong>FastAPI microservice</strong> — same enrichment logic as Streamlit, over HTTP.
              </p>
              <pre className="mono json-pre" style={{ maxHeight: 120 }}>
                uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8001
              </pre>
              <ul className="compact">
                <li>
                  Docs:{" "}
                  <a className="link-doc" href={`${apiBase.replace(/\/$/, "")}/docs`} target="_blank" rel="noreferrer">
                    {apiBase}/docs
                  </a>
                </li>
                <li>
                  Health: <code>GET {API_PREFIX}/health</code>
                </li>
                <li>
                  Run: <code>POST {API_PREFIX}/catalog/enrichment/run</code>
                </li>
              </ul>
              {lastValidations.length > 0 && (
                <>
                  <h4 className="small">Last run validations (from API)</h4>
                  <pre className="json-pre mono">{JSON.stringify(lastValidations, null, 2)}</pre>
                </>
              )}
            </div>
          )}

          {totalProducts > 0 && (
            <div className="card">
              <h4 className="section-title-sm">Export</h4>
              <button type="button" className="btn btn-dark" onClick={() => downloadJson("enrichment_run_output.json", products)}>
                Download Current Run JSON
              </button>
            </div>
          )}
        </main>

        <aside className="rail">
          {!totalProducts && (
            <div className="card">
              <div className="section-title-sm">Section Help</div>
              <ul className="compact">
                <li>No run is available yet.</li>
                <li>1) Upload a feed</li>
                <li>2) Click Run Enrichment</li>
                <li>3) Review and publish approved records</li>
              </ul>
            </div>
          )}

          {showRightRail && totalProducts > 0 && (
            <>
              <div className="card">
                <div className="section-title-sm">Today&apos;s Run Summary</div>
                <ul className="compact">
                  <li>{totalProducts} products processed</li>
                  <li>{published} ready for publish</li>
                  <li>{pendingReview} in review queue</li>
                  <li>{blocked} blocked by confidence</li>
                  <li>{reviewItems.length} review flags from provenance</li>
                </ul>
              </div>
              <div className="card">
                <div className="section-title-sm">High Priority Alerts</div>
                {blocked > 0 && <div className="alert">Taxonomy confidence low for blocked records</div>}
                {pendingReview > 0 && <div className="alert">Manual attribute review pending</div>}
                <div className="alert">Media enrichment status is not wired yet</div>
              </div>
              <div className="card">
                <div className="section-title-sm">Operational Analytics</div>
                {categoryBars.map(({ label, count, pct }) => (
                  <div key={label} className="progress-row">
                    <div className="progress-label">
                      <span>{label}</span>
                      <span>{count}</span>
                    </div>
                    <div className="progress-track">
                      <div className="progress-fill" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                ))}
              </div>
              <div className="card">
                <div className="section-title-sm">Issue Summary</div>
                <ul className="compact">
                  <li>Taxonomy mismatches: {blocked}</li>
                  <li>Missing attributes: {reviewItems.length}</li>
                  <li>Variant conflicts: {products.filter((p) => !((p.variant_axes as unknown[]) || []).length).length}</li>
                  <li>Media gaps: N/A</li>
                </ul>
              </div>
            </>
          )}

          {totalProducts > 0 && !showRightRail && (
            <div className="card">
              <div className="section-title-sm">Section Help</div>
              <p className="small">
                You are in <strong>{nav}</strong>. Use Run Enrichment after upload, then work section-by-section.
              </p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
