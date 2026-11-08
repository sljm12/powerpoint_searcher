from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest


class SecretDsn(str):
    def __repr__(self) -> str:
        return "<configured PostgreSQL DSN>"


@pytest.fixture(params=("sqlite", "postgresql"), ids=("sqlite", "postgresql"))
def database_backend(request: pytest.FixtureRequest) -> tuple[str, str | None]:
    backend = request.param
    return (backend, request.getfixturevalue("postgres_dsn") if backend == "postgresql" else None)


@pytest.fixture
def postgres_dsn() -> Iterator[str]:
    base = os.environ.get("LIBRARY_TEST_POSTGRES_DSN")
    if not base:
        pytest.skip("Set LIBRARY_TEST_POSTGRES_DSN to a disposable, dedicated PostgreSQL database.")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    name = "library_case_" + uuid4().hex[:16]
    with psycopg.connect(base, autocommit=True, connect_timeout=5) as admin:
        if not admin.execute("SELECT rolsuper OR rolcreatedb FROM pg_roles WHERE rolname=current_user").fetchone()[0]:
            pytest.skip("The disposable PostgreSQL test role needs CREATEDB.")
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield SecretDsn(make_conninfo(base, dbname=name))
        finally:
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid <> pg_backend_pid()", (name,))
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
