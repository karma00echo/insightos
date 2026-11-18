# InsightOS 2.0 Preview — Personal Decision & Action Agent

InsightOS helps people turn research into decisions they can revisit. Start in AI Chat, consult your knowledge base or run Deep Research, then explicitly confirm a decision. Approve actions and track their progress. When a cited source changes, inspect the version difference and request an evidence-bound impact analysis. You decide whether to keep, review, or revise the decision.

## What you can do

| Module | Preview capability |
| --- | --- |
| AI Chat and Hybrid RAG | Ask questions of permitted documents and inspect citations. Retrieval combines Qdrant dense search, SQL-backed BM25 and RRF, with optional reranking. |
| Deep Research | Run bounded research across selected knowledge bases, inspect findings and gaps, and save or export cited Markdown reports. |
| Personal Memory | Record explicitly confirmed facts, preferences and plans through the API. Decisions can refer to a specific revision. |
| Decision Tracker | Review AI-assisted drafts, confirm a decision, and trace later revisions and their sources. |
| Action Center | Create or approve actions, edit deadlines, and track status and history independently of the decision. |
| Source Change Detection | Request a comparison of two supported source versions and inspect their differences and explicit decision references. |
| Decision Impact Analysis and Human Review | Analyze a selected change against the current decision, inspect supporting excerpts and unknowns, then record a review choice. A revision takes effect only after explicit confirmation. |

The interface is primarily Chinese. The chat-first workspace uses Vue 3, TypeScript and Vite. FastAPI, SQLAlchemy and Alembic manage the API, persisted business state and schema. Research uses a bounded Python/SQL workflow. See [architecture](docs/architecture.md) for the data and authorization boundaries.

## Run the Preview locally

Use Python 3.12 and Node.js 22 as in CI. Run these commands from the repository root on a fresh checkout.

    python -m venv .venv
    # Windows PowerShell
    .\.venv\Scripts\Activate.ps1
    # On macOS/Linux use: source .venv/bin/activate
    python -m pip install -e './backend[dev]'
    cd frontend
    npm ci

From the repository root, copy `.env.example` to `.env` and set a unique `RAG_SECRET_KEY`. Start the API from `backend/` with `python -m uvicorn app.main:app --host 127.0.0.1 --port 8010`. In a second terminal, run `npm run dev` from `frontend/` and open the [local workspace](http://127.0.0.1:5173). FastAPI documents the Memory and other API endpoints in the [local API reference](http://127.0.0.1:8010/docs).

The default local profile uses SQLite, embedded Qdrant, hash embeddings, and extractive chat. Use a fresh local data directory. Startup applies pending Alembic migrations to the configured local database. The default profile supports application plumbing and manual workflows. Model-assisted discussion, AI report writing, and impact analysis need a configured provider. For local semantic embeddings, install the backend ml extra and configure sentence-transformers with BAAI/bge-small-zh-v1.5 and a matching embedding dimension of 512. Keep a separate data directory for a different embedding configuration. For AI features, configure RAG_LLM_PROVIDER, RAG_LLM_MODEL and the URL and credentials required by your provider. A remote provider can receive the selected evidence and may charge per request. See [deployment configuration](docs/deployment.md) for standalone services and model options.

## Verification and scope

The local T12 acceptance record is PARTIAL. Desktop and 390×844 isolated browser continuity passed with Mock model responses. Full production recovery remains PARTIAL. Real-provider quality and production readiness remain PENDING. Production deployment and database migration are pending. T13 has not started. See [Preview validation](docs/preview-validation.md) for historical results, candidate checks and reproducibility limits.

Licensed under [MIT](LICENSE).
