from alembic import context


def run(connection):
    # sqlite3 legacy mode skips BEGIN for DDL. Start the physical transaction
    # before the first revision, so a later failure rolls back the whole chain.
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")
    context.configure(connection=connection, target_metadata=None, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is None:
    from app.config import get_settings
    from sqlalchemy import create_engine
    with create_engine(get_settings().database_url).begin() as connection:
        run(connection)
else:
    run(connection)
