export type Product = Record<string, unknown>;

export const NAV = [
  "Overview",
  "Ingestion Runs",
  "Review Queue",
  "CLI",
  "Taxonomy Review",
  "Extracted Attributes",
  "Variant Issues",
  "Product Families",
  "Media Enrichment",
  "Analytics",
  "Audit Logs",
  "Configuration",
] as const;

export type Nav = (typeof NAV)[number];

export function taxonomyPath(p: Product): string {
  const t = p.predicted_taxonomy as Record<string, unknown> | undefined;
  const path = t?.path;
  return typeof path === "string" && path ? path : "Unknown";
}

/** Count top-level keys on a plain object record (not arrays). */
export function recordKeyCount(obj: unknown): number {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return 0;
  return Object.keys(obj as Record<string, unknown>).length;
}

/** Comma-separated attribute keys for table preview. */
export function attrKeysPreview(p: Product, maxKeys = 5): string {
  const attrs = (p.attributes as Record<string, unknown>) || {};
  const keys = Object.keys(attrs);
  if (!keys.length) return "—";
  const slice = keys.slice(0, maxKeys).join(", ");
  return keys.length > maxKeys ? `${slice} (+${keys.length - maxKeys})` : slice;
}

export function taxonomyConf(p: Product): number {
  const t = p.predicted_taxonomy as Record<string, unknown> | undefined;
  const c = t?.confidence;
  if (typeof c === "number") return c;
  if (typeof c === "string") return parseFloat(c) || 0;
  return 0;
}

export function stageForProduct(p: Product): string {
  const conf = taxonomyConf(p);
  const prov = p.provenance as Record<string, unknown> | undefined;
  const review = prov?.review as Record<string, unknown> | undefined;
  if (review && Object.keys(review).length > 0) return "Needs Review";
  if (conf >= 0.95) return "Ready";
  if (conf >= 0.8) return "Review Pending";
  return "Blocked";
}

export type QueueRow = {
  id: string;
  name: string;
  taxonomy: string;
  confidence: number;
  issues: number;
  stage: string;
  reviewer: string;
};

export function buildQueueRows(products: Product[]): QueueRow[] {
  const rows = products.map((p) => {
    const prov = p.provenance as Record<string, unknown> | undefined;
    const review = prov?.review as Record<string, unknown> | undefined;
    const issueCount = review ? Object.keys(review).length : 0;
    return {
      id: String(p.source_product_id ?? "-"),
      name: String(p.raw_title ?? "-"),
      taxonomy: taxonomyPath(p),
      confidence: Math.round(taxonomyConf(p) * 100) / 100,
      issues: issueCount,
      stage: stageForProduct(p),
      reviewer: "Unassigned",
    };
  });
  rows.sort((a, b) => {
    const aNeeds = a.stage !== "Needs Review" ? 1 : 0;
    const bNeeds = b.stage !== "Needs Review" ? 1 : 0;
    if (aNeeds !== bNeeds) return aNeeds - bNeeds;
    return a.confidence - b.confidence;
  });
  return rows;
}

export type ReviewItem = {
  product_id: string | undefined;
  attribute: string;
  reason: unknown;
  image_url: unknown;
  category: unknown;
};

export function collectReviews(products: Product[]): ReviewItem[] {
  const out: ReviewItem[] = [];
  for (const product of products) {
    const provenance = (product.provenance as Record<string, unknown>) || {};
    const reviewBlock = (provenance.review as Record<string, unknown>) || {};
    for (const [attr, details] of Object.entries(reviewBlock)) {
      const d = details as Record<string, unknown>;
      if (d?.needs_review) {
        out.push({
          product_id: d.product_id as string | undefined,
          attribute: attr,
          reason: d.reason,
          image_url: d.image_url,
          category: d.category,
        });
      }
    }
  }
  return out;
}

export function applyReviewUpdate(product: Product, attribute: string, value: string): Product {
  const next = structuredClone(product) as Product;
  const attrs = (next.attributes as Record<string, unknown>) || {};
  next.attributes = { ...attrs, [attribute]: value.charAt(0).toUpperCase() + value.slice(1) };
  const prov = (next.provenance as Record<string, unknown>) || {};
  const rev = { ...((prov.review as Record<string, unknown>) || {}) };
  delete rev[attribute];
  const { review: _drop, ...restProv } = prov;
  if (Object.keys(rev).length === 0) {
    next.provenance = restProv;
  } else {
    next.provenance = { ...restProv, review: rev };
  }
  return next;
}

export function buildCliView(p: Product): Record<string, unknown> {
  const tax = (p.predicted_taxonomy as Record<string, unknown>) || {};
  const variantsRaw = (p.variants as unknown[]) || [];
  const variantPreview: unknown[] = [];
  for (const v of variantsRaw.slice(0, 12)) {
    variantPreview.push(v);
  }
  if (variantsRaw.length > 12) {
    variantPreview.push({ _note: `${variantsRaw.length - 12} more sibling row(s)` });
  }
  const view: Record<string, unknown> = {
    source_product_id: p.source_product_id,
    title: p.raw_title ?? p.normalized_title,
    taxonomy: {
      mapped_category: tax.path,
      categoryId: tax.category_id,
      categoryIds: tax.category_ids ?? [],
      confidence: tax.confidence,
    },
    attributes: {
      extracted: p.attributes ?? {},
      identity: p.identity_attributes ?? {},
      variant: p.variant_attributes ?? {},
    },
    grouping: {
      family_name: p.family_name,
      family_signature: p.family_signature,
      group_id: p.group_id,
      variant_axes: p.variant_axes ?? [],
      sibling_count: variantsRaw.length,
      variants: variantPreview,
    },
  };
  const prov = p.provenance as Record<string, unknown> | undefined;
  if (prov?.review) view.provenance = { review: prov.review };
  if (p.quality) view.quality = p.quality;
  if (p.content) view.content = p.content;
  if (p.media) view.media = p.media;
  return view;
}

export type FamilyRow = {
  family_name: string;
  group_id: string;
  taxonomy_leaf: string;
  family_size: number;
  variant_axes: string;
  colors_in_family: string;
  sizes_in_family: string;
  product_ids: string;
};

export function buildFamilyRows(products: Product[]): FamilyRow[] {
  const families = new Map<
    string,
    {
      family_name: unknown;
      group_id: string;
      taxonomy_leaf: string;
      family_size: number;
      variant_axes: Set<string>;
      color_values: Set<string>;
      size_values: Set<string>;
      product_ids: string[];
    }
  >();
  for (const p of products) {
    const gid = String(p.group_id ?? `ungrouped::${p.source_product_id ?? ""}`);
    const leaf = taxonomyPath(p).split(">").pop()?.trim() ?? taxonomyPath(p);
    if (!families.has(gid)) {
      families.set(gid, {
        family_name: p.family_name,
        group_id: gid,
        taxonomy_leaf: leaf,
        family_size: 0,
        variant_axes: new Set(),
        color_values: new Set(),
        size_values: new Set(),
        product_ids: [],
      });
    }
    const f = families.get(gid)!;
    f.family_size += 1;
    f.product_ids.push(String(p.source_product_id ?? ""));
    const axes = (p.variant_axes as string[]) || [];
    axes.forEach((a) => f.variant_axes.add(a));
    const va = (p.variant_attributes as Record<string, unknown>) || {};
    const colorVal = va.color;
    if (colorVal && typeof colorVal === "object" && colorVal !== null && "name" in colorVal) {
      f.color_values.add(String((colorVal as { name: unknown }).name));
    } else if (typeof colorVal === "string") f.color_values.add(colorVal);
    const sizeVal = va.size;
    if (typeof sizeVal === "string") f.size_values.add(sizeVal);
  }
  return [...families.values()].map((item) => ({
    family_name: String(item.family_name ?? ""),
    group_id: item.group_id,
    taxonomy_leaf: item.taxonomy_leaf,
    family_size: item.family_size,
    variant_axes: [...item.variant_axes].sort().join(", ") || "-",
    colors_in_family: [...item.color_values].sort().join(", ") || "-",
    sizes_in_family: [...item.size_values].sort().join(", ") || "-",
    product_ids: item.product_ids.join(", "),
  }));
}

export function parseFeedFile(name: string, text: string): Product[] {
  const lower = name.toLowerCase();
  if (lower.endsWith(".json")) {
    const data = JSON.parse(text) as unknown;
    if (Array.isArray(data)) return data as Product[];
    if (data && typeof data === "object") return [data as Product];
    throw new Error("JSON must be an array of products or a single object");
  }
  if (lower.endsWith(".jsonl")) {
    const rows: Product[] = [];
    for (const line of text.split(/\r?\n/)) {
      const t = line.trim();
      if (!t) continue;
      rows.push(JSON.parse(t) as Product);
    }
    return rows;
  }
  if (lower.endsWith(".csv")) {
    const lines = text.split(/\r?\n/).filter((l) => l.trim());
    if (lines.length < 2) throw new Error("CSV needs a header row and at least one data row");
    const header = lines[0].split(",").map((h) => h.trim().replace(/^"|"$/g, ""));
    const out: Product[] = [];
    for (let i = 1; i < lines.length; i++) {
      const cells = lines[i].split(",").map((c) => c.trim().replace(/^"|"$/g, ""));
      const row: Record<string, string> = {};
      header.forEach((h, j) => {
        row[h] = cells[j] ?? "";
      });
      out.push(row as Product);
    }
    return out;
  }
  throw new Error("Unsupported file type. Use .json, .jsonl, or .csv");
}
