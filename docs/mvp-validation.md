# MVP validation provenance

This is a sanitized summary of private workstation records, not a new model evaluation. Raw prompts, responses, accounts, databases, vector indexes, reports and browser captures are intentionally not bundled.

| Private record | Observed result | Interpretation |
| --- | --- | --- |
| `MVP_V1_FINAL_ACCEPTANCE.md`, 2026-10-30 | Evidence-gap failure before new report writing | Earlier failure retained; no completed E2E claim for that run |
| `MVP_V1_FINAL_E2E_VERIFICATION.md`, 2026-11-01 | Fresh browser task, planning/retrieval/synthesis/real report writing, automatic save, citation display, history navigation and Markdown download passed | One real sample; download matched saved Markdown byte for byte; three normal provider calls |
| `RAG_CHAT_REAL_FAILURE_FIX_VERIFICATION.md` | Original-workspace backend 286 passed, frontend 8 passed | Subsequent chat regression record; includes private archive-dependent tests |

The browser record leaves semantic quality at `QUALITY_PENDING_HUMAN_REVIEW` with `semantic_checked=false`. Citation binding counts describe traceability, not factual accuracy. No new DeepSeek request is made for publication preparation. Historical estimated spend and elapsed time are not presented as general pricing or performance promises.

## Public offline verification

Run `python scripts/test-offline.py -q -rs`, `python -m ruff check backend scripts`, then `npm test` and `npm run build` in `frontend/`. The runner uses temporary runtime storage, empties provider settings, disables Hugging Face downloads/tracing, and rejects non-loopback socket connections. Loopback support is retained for the local test framework. It is a test harness, not an OS-wide network sandbox.

Some historical tests deliberately reference archives outside the Git repository:

- Report-binding replay requires a private replay helper, archived responses, and a copied historical store; that module skips if its replay helper is absent.
- Research evaluation tests require private benchmark packets/freeze records; their helper or archive-specific test skips when those records are absent.
- Historical failure-sequence and source-crowding tests require archived task JSON or the private runtime SQLite; those tests skip when their input is absent.

Synthetic API/retrieval/research/report tests remain available. Skips do not certify the unavailable historical scenarios. The original workspace retains private inputs and historical records. Public chat fixtures are explicitly synthetic replacements; they test the same mechanisms without claiming an exact historical replay. See [fixture provenance](../backend/tests/fixtures/README.md).

Publication verification results are recorded in [publication preparation](publication-preparation.md). CI infrastructure jobs also exercise PostgreSQL/Redis/Qdrant using fixed disposable test credentials; they are separate from the isolated offline test profile.

## Demo media

The README reserves positions for actual MVP screenshots without copying private browser captures. Before adding a screenshot, review displayed account information, document titles, source passages, research text, history, IDs and browser chrome. Legacy images are omitted. No screenshot is bundled or presented as new MVP evidence.
