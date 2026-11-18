"""Only new temporary SQLite databases; never resolve application DB settings."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text


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


@pytest.mark.parametrize(
    "start", [None, "007_chunk_locator", "008_research", "009_research_reports", "010_personal_memory"]
)
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
    upgrade(engine, "011_decisions")
    upgrade(engine, "011_decisions")
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "011_decisions"
        for name in ["decisions", "decision_revisions", "decision_basis", "decision_mutations"]:
            assert c.scalar(text(f"SELECT count(*) FROM {name}")) == 0
        if start:
            assert c.scalar(text("SELECT password_hash FROM users WHERE id='old'")) == "untouched"
        assert c.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    from app.decision_models import Decision, DecisionBasis, DecisionMutation, DecisionRevision

    schema = inspect(engine)
    for model in [Decision, DecisionRevision, DecisionBasis, DecisionMutation]:
        actual = {col["name"]: col for col in schema.get_columns(model.__tablename__)}
        assert set(actual) == set(model.__table__.columns.keys())
        for column in model.__table__.columns:
            assert actual[column.name]["nullable"] == column.nullable
    engine.dispose()


def test_failed_migration_rolls_back_and_retries(tmp_path):
    engine = engine_at(tmp_path)
    upgrade(engine, "010_personal_memory")

    def failure(conn, cursor, statement, parameters, context, executemany):
        if "CREATE TABLE decision_revisions" in statement:
            raise RuntimeError("injected migration failure")

    event.listen(engine, "before_cursor_execute", failure)
    with pytest.raises(RuntimeError, match="injected"):
        upgrade(engine, "011_decisions")
    event.remove(engine, "before_cursor_execute", failure)
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "010_personal_memory"
        assert "decisions" not in inspect(c).get_table_names()
    upgrade(engine, "011_decisions")
    engine.dispose()


def test_basis_composite_foreign_key_and_transaction_rollback(tmp_path):
    from sqlalchemy.exc import IntegrityError

    engine = engine_at(tmp_path)
    upgrade(engine, "011_decisions")
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO users(id,email,name,password_hash,created_at,disabled) "
                "VALUES ('u','u@example.com','u','hash',CURRENT_TIMESTAMP,0)"
            )
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("INSERT INTO decisions VALUES ('d','u',1,'active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            c.execute(text("INSERT INTO decision_basis VALUES ('d',99,0,'user_input','{}')"))
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM decisions")) == 0
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(
                text("INSERT INTO decisions VALUES ('d','missing',1,'active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
            )
    engine.dispose()
