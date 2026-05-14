# Catalog enrichment dashboard

Python **FastAPI** microservice plus **Streamlit** and **React (Vite)** UIs for catalog onboarding: normalize → taxonomy (webhook) → attributes → family/variant grouping.

## Quick start

1. **Python:** create a venv, then from repo root:

   ```bash
   pip install -r req.txt
   ```

2. **Secrets & config:** copy `.env.example` to **`.env`** (this file is gitignored). Set at least:

   - `GROQ_API_KEY` — required for LLM grouping / variant axes / color LLM paths  
   - `TAXONOMY_WEBHOOK_URL` — your taxonomy n8n (or other) endpoint (the repo default in code is a non-routable placeholder)

3. **API:**

   ```bash
   uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8001
   ```

4. **Browser UI:** see [`frontend/README.md`](frontend/README.md).

5. **Streamlit:** `streamlit run app/ui/review_dashboard.py`

Full architecture and env vars: **[`HANDOFF.md`](HANDOFF.md)**.

## Security before GitHub

- **Never commit** `.env` — it is listed in `.gitignore`.  
- Do not paste API keys into tracked files; use `.env` or your host’s secret store.  
- If a key was ever committed, **rotate** it in the provider console and rewrite git history if needed.

### Before `git add` / `git commit`

Run `git status` — **`.env` must not** appear under “Changes to be committed”. After staging, `git diff --cached --name-only` must not list `.env`.

If your local `.env` ever contained real keys that were shared or copied, **rotate those keys** in the Groq (and any other) dashboards even if `.env` stayed untracked.

## Repo layout (high level)

| Path | Purpose |
|------|---------|
| `app/` | FastAPI, orchestrator, services, core models |
| `frontend/` | React dashboard (API client) |
| `data/sample_input.json` | Example feed |
| `req.txt` | Python dependencies |

## License

Add a `LICENSE` file when your organization decides which license applies.
