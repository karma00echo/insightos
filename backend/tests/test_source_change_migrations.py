import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, inspect, text
from test_memory_migrations import engine_at, upgrade


@pytest.mark.parametrize("start", [None, "007_chunk_locator", "009_research_reports", "012_actions"])
def test_source_change_upgrade(tmp_path, start):
    engine = engine_at(tmp_path)
    if start:
        upgrade(engine, start)
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,email,name,password_hash,created_at,disabled) VALUES ('old','old@example.com','old','untouched',CURRENT_TIMESTAMP,0)"))
    upgrade(engine, "013_source_changes")
    upgrade(engine, "013_source_changes")
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM source_changes")) == 0
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "013_source_changes"
        assert not c.execute(text("PRAGMA foreign_key_check")).all()
        if start:
            assert c.scalar(text("SELECT password_hash FROM users WHERE id='old'")) == "untouched"
    engine.dispose()


def test_ddl_failure_rollback_retry_and_downgrade_policy(tmp_path):
    from pathlib import Path

    engine = engine_at(tmp_path)
    upgrade(engine, "012_actions")

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE alembic_version"):
            raise RuntimeError("injected after table creation")

    event.listen(engine, "before_cursor_execute", fail)
    with pytest.raises(RuntimeError, match="injected"):
        upgrade(engine, "013_source_changes")
    event.remove(engine, "before_cursor_execute", fail)
    with engine.connect() as c:
        assert "source_changes" not in inspect(c).get_table_names()
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "012_actions"
    upgrade(engine, "013_source_changes")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    with engine.begin() as c:
        config.attributes["connection"] = c
        with pytest.raises(RuntimeError, match="verified backup"):
            command.downgrade(config, "012_actions")
    engine.dispose()
