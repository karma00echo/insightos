# Clean release preparation

This MVP v1.0 snapshot is prepared in an independent Git repository. It contains the current application source, migrations, frontend, tests, examples, documentation, environment templates, MIT License, CI and deployment files. It does not inherit any commit history or remote configuration from the development repository.

## What is intentionally absent

- Real `.env` files and credentials, user accounts, SQLite databases and sidecars.
- Qdrant indexes, uploaded knowledge, model weights, virtual environments and dependency caches.
- Historical reports, model request/response logs, raw benchmark/acceptance JSON and browser captures.
- Nine legacy UI images and the workstation-specific semantic-index recovery launcher.

The original workstation material is retained outside this snapshot. Historical verification is summarized in [MVP validation provenance](mvp-validation.md); the raw evidence is not a public fixture set.

## Fixture review

Three chat regression JSON files originally contained exported source passages and runtime UUIDs. The release uses small, author-written synthetic replacements, deterministic sample IDs and explicit provenance. See [fixture notes](../backend/tests/fixtures/README.md). Existing tests continue to check citation formatting, numeric rejection, streaming verification, cross-language selection and failure-diagnostic isolation. These synthetic tests do not claim an exact replay of the private historical responses or validate semantic research quality.

The benchmark and evaluation JSON under `examples/` are fictional, human-written demo cases. Frontend package/lock/TypeScript JSON are build configuration. No raw acceptance JSON or image asset is included.

## Security and source integrity

All release candidates are scanned as text for common provider keys, private keys, JWTs, credential-bearing URLs, phone-like identifiers, workstation home paths and exact private configuration values. Possible literal credentials are reviewed in context. The CI database URL uses the documented disposable `fixture-password-only` value; test-account passwords are synthetic fixtures, not deployment credentials. Environment templates keep real API keys blank and deployment credentials explicit placeholders.

Backend application source, frontend application source, migrations and LICENSE are retained byte for byte. Changes are limited to public fixture data/provenance and documentation/ignore rules. The original development worktree, index, HEAD, refs, Git configuration and `.env` are checked against a read-only baseline.

No DeepSeek call, remote creation, remote configuration change or push is performed during this preparation. A new `main` branch and staged initial snapshot can be reviewed before any local commit or publication. The existing MIT License is preserved, including its attribution.

## Verification and remaining limits

| Check | Result |
| --- | --- |
| Backend tests in this independent copy | **347 passed, 33 skipped**; one existing jieba/pkg_resources deprecation warning |
| Frontend tests on an identical external validation copy | **8 passed** |
| Frontend vue-tsc and production build | Passed |
| Ruff, backend and scripts | Passed |
| Environment templates / startup files / Markdown links | Validated |
| Full public-candidate scan | No real credential or private configuration match detected; CI fixture URL reviewed |
| Large/binary/runtime payloads | None in candidates; largest source file below 70 KB |

Private archive-dependent tests explicitly skip when their inputs are absent; skipped scenarios are not certified. Offline checks use installed dependencies and temporary stores, not a new online dependency installation. Python dependencies have version bounds rather than a complete lockfile.

Pattern scanning cannot prove the absence of every possible secret or semantic personal detail. No binary documents or screenshots are distributed, reducing that review surface. Research semantic quality remains `QUALITY_PENDING_HUMAN_REVIEW`; citation validity is not factual accuracy. CI infrastructure/container deployment and real provider compatibility are not revalidated by these offline checks.

Suggested initial commit:

```text
feat: release InsightOS MVP v1.0

Add the evidence-based research agent, hybrid RAG workspace and versioned reports.
Include source setup, offline regression checks and deployment examples.
Use synthetic public fixtures and omit private runtime and acceptance artifacts.
```
