"""Run backend tests with temporary storage and no provider network access."""
import os
import socket
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    for key in list(os.environ):
        if key.startswith("RAG_"):
            del os.environ[key]
    os.environ.update(
        HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
        LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false",
        RAG_ENVIRONMENT="development", RAG_EMBEDDING_PROVIDER="hash",
        RAG_EMBEDDING_DIMENSION="384", RAG_LLM_MODEL="", RAG_LLM_API_KEY="",
        RAG_LLM_BASE_URL="https://offline.invalid/v1", RAG_LLM_PROVIDER="openai",
        RAG_EMBEDDING_API_KEY="", RAG_REDIS_URL="", RAG_QDRANT_URL="",
        RAG_QDRANT_API_KEY="", RAG_RERANKER_MODEL="", RAG_VISION_MODEL="",
        RAG_CITATION_VERIFIER="numeric", RAG_SECRET_KEY="development-only-offline-test-secret",
        RAG_OIDC_ISSUER="", RAG_OIDC_CLIENT_ID="", RAG_OIDC_CLIENT_SECRET="",
    )

    def guard(method):
        def connect(sock, address):
            if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "localhost", "::1"}:
                raise AssertionError("Offline tests forbid non-loopback network connections")
            return method(sock, address)
        return connect

    socket.socket.connect = guard(socket.socket.connect)
    socket.socket.connect_ex = guard(socket.socket.connect_ex)
    os.chdir(root / "backend")
    sys.path.insert(0, str(root / "backend"))
    temporary_root = root / "artifacts" / "offline-tests"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="insightos-offline-", dir=temporary_root) as runtime:
        os.environ["RAG_DATA_DIR"] = runtime
        os.environ["RAG_DATABASE_URL"] = "sqlite:///" + (Path(runtime) / "test.db").as_posix()
        import pytest

        return pytest.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
