"""Only new temporary SQLite databases; never resolve application DB settings."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError


def engine_at(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "migration.db").as_posix(), hide_parameters=True)

    @event.listens_for(engine, "connect")
    def fk(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    return engine


def upgrade(engine, target):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, target)


@pytest.mark.parametrize("start", [None, "007_chunk_locator", "008_research", "009_research_reports"])
def test_empty_and_existing_version_upgrade(tmp_path, start):
    engine = engine_at(tmp_path)
    if start:
        upgrade(engine, start)
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO users(id,email,name,password_hash,created_at,disabled) "
                    "VALUES ('old','old@example.com','old','untouched',CURRENT_TIMESTAMP,0)"
                )
            )
    upgrade(engine, "010_personal_memory")
    upgrade(engine, "010_personal_memory")
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "010_personal_memory"
        for name in ["personal_memories", "memory_revisions", "memory_operations"]:
            assert c.scalar(text(f"SELECT count(*) FROM {name}")) == 0
        if start:
            assert c.scalar(text("SELECT password_hash FROM users WHERE id='old'")) == "untouched"
        assert c.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    from app.memory_models import MemoryOperation, MemoryRevision, PersonalMemory

    schema = inspect(engine)
    for model in [PersonalMemory, MemoryRevision, MemoryOperation]:
        actual = {col["name"]: col for col in schema.get_columns(model.__tablename__)}
        assert set(actual) == set(model.__table__.columns.keys())
        for column in model.__table__.columns:
            assert actual[column.name]["nullable"] == column.nullable
    engine.dispose()


def test_failed_migration_rolls_back_and_retries(tmp_path):
    engine = engine_at(tmp_path)
    upgrade(engine, "009_research_reports")

    def failure(conn, cursor, statement, parameters, context, executemany):
        if "CREATE TABLE memory_revisions" in statement:
            raise RuntimeError("injected migration failure")

    event.listen(engine, "before_cursor_execute", failure)
    with pytest.raises(RuntimeError, match="injected"):
        upgrade(engine, "010_personal_memory")
    event.remove(engine, "before_cursor_execute", failure)
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "009_research_reports"
        assert "personal_memories" not in inspect(c).get_table_names()
    upgrade(engine, "010_personal_memory")
    engine.dispose()


def test_foreign_keys_constraints_and_transaction_rollback(tmp_path):
    engine = engine_at(tmp_path)
    upgrade(engine, "010_personal_memory")
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO personal_memories VALUES "
                    "('m','missing',1,'active','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO users(id,email,name,password_hash,created_at,disabled) "
                "VALUES ('u','u@example.com','u','hash',CURRENT_TIMESTAMP,0)"
            )
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO personal_memories VALUES ('m','u',1,'active','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
            c.execute(
                text(
                    "INSERT INTO memory_revisions VALUES "
                    "('m',1,'ai_inference','secret','','confirmed','u',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM personal_memories")) == 0
        assert c.scalar(text("SELECT count(*) FROM memory_revisions")) == 0
    engine.dispose()
