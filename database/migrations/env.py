import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = None


def _database_url():
    valor = (
        config.attributes.get("connection_url")
        or os.environ.get("SUPABASE_MIGRATION_URL")
        or os.environ.get("DATABASE_URL")
        or ""
    ).strip()
    if not valor:
        raise RuntimeError(
            "Defina SUPABASE_MIGRATION_URL com a conexão de migration do PostgreSQL."
        )

    url = make_url(valor)
    if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise RuntimeError("A migration Alembic exige uma conexão PostgreSQL.")
    if url.port == 6543:
        raise RuntimeError("Use a conexão direta ou o Session pooler na porta 5432.")

    query = dict(url.query)
    query.setdefault("sslmode", os.environ.get("DATABASE_SSLMODE", "require"))
    certificado = (os.environ.get("DATABASE_SSLROOTCERT") or "").strip()
    if certificado:
        query.setdefault("sslrootcert", certificado)
    url = url.set(drivername="postgresql+psycopg", query=query)
    return url.render_as_string(hide_password=False)


def _configure(connection=None):
    options = {
        "target_metadata": target_metadata,
        "version_table": "alembic_version",
        "version_table_schema": "despacho",
        "include_schemas": True,
        "compare_type": True,
    }
    if connection is None:
        options.update(
            url=_database_url(),
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )
    else:
        options["connection"] = connection
    context.configure(**options)


def run_migrations_offline():
    _configure()
    with context.begin_transaction():
        context.execute("CREATE SCHEMA IF NOT EXISTS despacho")
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(_database_url(), poolclass=NullPool)
    with engine.connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.execute("CREATE SCHEMA IF NOT EXISTS despacho")
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
