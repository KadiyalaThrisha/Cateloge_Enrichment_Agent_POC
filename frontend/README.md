# Catalog enrichment — browser UI (Vite + React)

Separate from Streamlit. Calls the FastAPI service only. **Layout matches the Streamlit dashboard** (header, KPIs, pills, 3-column nav + main + right rail, review queue, product detail, actions, export).

## Prereqs

1. API running (from repo root):

```bash
uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8001
```

2. Install deps (once):

```bash
cd frontend
npm install
```

## Dev

```bash
cd frontend
npm run dev
```

Open **http://127.0.0.1:5173** (Vite default).  
Set **API base** to `http://127.0.0.1:8001` if it differs, then **Check health**.

Upload a product JSON array under **Ingestion Runs** (same shape as `data/sample_input.json`).

## Config

Copy `.env.example` → `.env.local` and adjust `VITE_API_BASE` if the API host/port changes.

## Build (static files)

```bash
npm run build
```

Output in `frontend/dist/`. Serve behind any static host; ensure CORS on the API includes that origin.
