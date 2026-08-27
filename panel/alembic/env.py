"""Alembic async env.py — reads DB credentials from environment variables.

Works in two contexts:
  1. Host-side CLI: load .env from /home/onnix/.env (developer workstation).
  2. Container entrypoint: env vars already injected by Docker Compose (env_file +
     environment overrides). load_dotenv is a no-op when vars are already set.
"""
import asyncio
import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Attempt to load .env from the canonical host location.
# Inside Docker the file does not exist; load_dotenv silently skips it.
# On the developer host it loads credentials for local alembic CLI usage.
load_dotenv("/home/onnix/.env")

# Build async database URL from individual env vars.
# Docker Compose injects POSTGRES_HOST / POSTGRES_DB per environment:
#   prod  → POSTGRES_HOST=onnix-postgres, POSTGRES_DB=onnix_prod  (docker-compose.yml)
#   staging → POSTGRES_HOST=onnix-postgres, POSTGRES_DB=onnix_dev  (docker-compose.dev.yml)
_user = os.environ["POSTGRES_USER"]
_pass = os.environ["POSTGRES_PASSWORD"]
_db = os.environ.get("POSTGRES_DB", "onnix_prod")
_host = os.environ.get("POSTGRES_HOST", "onnix-postgres")
_port = os.environ.get("POSTGRES_PORT", "5432")

DATABASE_URL_ASYNC = (
    f"postgresql+asyncpg://{_user}:{_pass}@{_host}:{_port}/{_db}"
)

# Alembic Config object
config = context.config

# Override sqlalchemy.url with constructed async URL
config.set_main_option("sqlalchemy.url", DATABASE_URL_ASYNC)

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No target_metadata for now (raw SQL migrations)
target_metadata = None


def run_migrations_offline() -> None:
    """Run in 'offline' mode — generate SQL script."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
