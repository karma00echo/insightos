# InsightOS 2.0 Preview validation

T12 recorded **657 backend tests passed** on the final implementation, followed by a separate **3 passing source-race tests**. Frontend **65 passed**, with TypeScript, Vite and Ruff passing. The isolated desktop and 390×844 browser workflows passed with real Cookie/API/SQLite and Mock model responses. These are historical engineering results from the development workspace.

T12 overall remains **PARTIAL**. Full production recovery remains **PARTIAL**. Real-provider quality and production readiness remain **PENDING**. Production databases have not been migrated and the Preview has not been deployed. T13 has not started.

Public test data consists of reviewed demo examples and synthetic regression fixtures. Private archive-dependent tests may skip. Run the offline backend tests from the repository root with python scripts/test-offline.py -q -rs. Run npm test and npm run build from frontend. Use python -m ruff check backend scripts for lint.

The release candidate passed 241 targeted backend tests using an isolated SQLite test profile and Mock providers. Coverage includes the continuity workflow, source invalidation, authorization, citations, Deep Research and migration rollback. Ruff passed. The backend wheel built and installed offline using the workstation's existing Python 3.12 dependency environment. Its installed dependencies passed pip check.

The frontend installed from the lockfile and passed 65 tests, vue-tsc and the Vite production build on Node.js 24. The historical browser pass was recorded on the development implementation. The candidate was checked with automated tests and a build. A full backend rerun and a second browser pass were outside this candidate check.
