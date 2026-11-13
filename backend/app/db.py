import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

DEFAULT_DATABASE_URL = "postgresql://folio:folio@localhost:5432/folio"

# A short timeout so an unreachable host fails the ready check instead of hanging.
CONNECT_TIMEOUT_SECONDS = 3


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    with psycopg.connect(
        database_url(),
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    ) as connection:
        yield connection


def database_ready() -> bool:
    try:
        with connect() as connection:
            row = connection.execute("SELECT 1").fetchone()
        return row == (1,)
    except Exception:
        return False
