"""New temporary databases only: upgrade, constraints and rollback policy."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError
from test_memory_migrations import engine_at, upgrade


@pytest.mark.parametrize("start", [None, "007_chunk_locator", "009_research_reports", "011_decisions"])
def test_upgrade_empty_and_previous(tmp_path, start):
    engine = engine_at(tmp_path)
    if start:
        upgrade(engine, start)
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,email,name,password_hash,created_at,disabled) "
                           "VALUES ('old','old@example.com','old','untouched',CURRENT_TIMESTAMP,0)"))
    upgrade(engine, "012_actions")
    upgrade(engine, "012_actions")
    from app.action_models import Action, ActionMutation, ActionRevision
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "012_actions"
        for model in [Action, ActionRevision, ActionMutation]:
            actual = {col["name"]: col for col in inspect(c).get_columns(model.__tablename__)}
            assert set(actual) == set(model.__table__.columns.keys())
            for col in model.__table__.columns:
                assert actual[col.name]["nullable"] == col.nullable
            assert c.scalar(text(f"SELECT count(*) FROM {model.__tablename__}")) == 0
        if start:
            assert c.scalar(text("SELECT password_hash FROM users WHERE id='old'")) == "untouched"
        assert not c.execute(text("PRAGMA foreign_key_check")).all()
    engine.dispose()


def test_failed_migration_rollback_retry_and_destructive_downgrade_refused(tmp_path):
    engine = engine_at(tmp_path)
    upgrade(engine, "011_decisions")

    def fail(conn, cursor, statement, parameters, context, executemany):
        if "CREATE TABLE action_revisions" in statement:
            raise RuntimeError("injected")

    event.listen(engine, "before_cursor_execute", fail)
    with pytest.raises(RuntimeError, match="injected"):
        upgrade(engine, "012_actions")
    event.remove(engine, "before_cursor_execute", fail)
    with engine.connect() as c:
        assert "actions" not in inspect(c).get_table_names()
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "011_decisions"
    upgrade(engine, "012_actions")
    config = Config()
    from pathlib import Path
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    with engine.begin() as c:
        config.attributes["connection"] = c
        with pytest.raises(RuntimeError, match="verified backup"):
            command.downgrade(config, "011_decisions")
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "012_actions"
    engine.dispose()


def test_fk_and_status_constraints(tmp_path):
    engine = engine_at(tmp_path)
    upgrade(engine, "012_actions")
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO action_revisions VALUES ('missing',1,'todo','{}',NULL,'create','missing',CURRENT_TIMESTAMP)"))
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,email,name,password_hash,created_at,disabled) "
                           "VALUES ('u','u@example.com','u','hash',CURRENT_TIMESTAMP,0)"))
            c.execute(text("INSERT INTO actions(id,owner_id,current_version,status,approved_by,approved_at,created_at,updated_at) "
                           "VALUES ('a','u',1,'executing','u',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    engine.dispose()
