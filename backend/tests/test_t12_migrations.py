"""Whole-chain fault rollback/retry, separate from per-revision migration tests."""
import pytest
from sqlalchemy import event, inspect, text
from test_memory_migrations import engine_at, upgrade


@pytest.mark.parametrize('start', ['007_chunk_locator', '009_research_reports'])
@pytest.mark.parametrize('fail_table', ['decisions', 'impact_review_events'])
def test_whole_chain_failure_restores_start_and_retries(tmp_path, start, fail_table):
    engine = engine_at(tmp_path)
    upgrade(engine, start)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users(id,email,name,password_hash,created_at,disabled) VALUES ('old','old@example.com','old','preserved',CURRENT_TIMESTAMP,0)"))
    before = set(inspect(engine).get_table_names())

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith('CREATE TABLE ' + fail_table + ' ('):
            raise RuntimeError('T12 injected DDL fault')

    event.listen(engine, 'before_cursor_execute', fail)
    with pytest.raises(RuntimeError, match='T12 injected'):
        upgrade(engine, 'head')
    event.remove(engine, 'before_cursor_execute', fail)
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == start
        assert set(inspect(conn).get_table_names()) == before
        assert conn.scalar(text("SELECT password_hash FROM users WHERE id='old'")) == 'preserved'
    upgrade(engine, 'head')
    upgrade(engine, 'head')
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '014_impact_reviews'
        assert conn.scalar(text('PRAGMA integrity_check')) == 'ok'
        assert not conn.execute(text('PRAGMA foreign_key_check')).all()
    engine.dispose()
