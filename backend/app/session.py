import uuid
from pathlib import Path

import psycopg

from app import account, email_limit
from app.db import connect

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "session.sql"


def apply(connection: psycopg.Connection) -> None:
    connection.execute(_MIGRATION.read_text())


def start_session(account_id: uuid.UUID) -> uuid.UUID:
    with connect() as connection:
        with connection.transaction():
            email_limit.apply(connection)
            account.apply(connection)
            apply(connection)
            locked = connection.execute(
                "SELECT id FROM account WHERE id = %s FOR UPDATE",
                (account_id,),
            ).fetchone()
            if locked is None:
                raise RuntimeError("account is missing")
            connection.execute(
                """
                UPDATE session
                SET ended_at = now()
                WHERE account_id = %s
                  AND ended_at IS NULL
                  AND last_active_at <= now() - interval '30 minutes'
                """,
                (account_id,),
            )
            open_rows = connection.execute(
                """
                SELECT id
                FROM session
                WHERE account_id = %s
                  AND ended_at IS NULL
                ORDER BY created_at ASC
                FOR UPDATE
                """,
                (account_id,),
            ).fetchall()
            if len(open_rows) == 2:
                connection.execute(
                    """
                    UPDATE session
                    SET ended_at = now()
                    WHERE id = %s
                      AND ended_at IS NULL
                    """,
                    (open_rows[0][0],),
                )
            created = connection.execute(
                """
                INSERT INTO session (account_id)
                VALUES (%s)
                RETURNING id
                """,
                (account_id,),
            ).fetchone()
            if created is None:
                raise RuntimeError("session was not stored")
            session_id = created[0]
    return session_id


def end_session(session_id: uuid.UUID) -> None:
    with connect() as connection:
        connection.execute(
            """
            UPDATE session
            SET ended_at = now()
            WHERE id = %s
              AND ended_at IS NULL
            """,
            (session_id,),
        )


def open_session(session_id: uuid.UUID) -> uuid.UUID | None:
    with connect() as connection:
        with connection.transaction():
            row = connection.execute(
                """
                SELECT account_id,
                       ended_at IS NOT NULL,
                       last_active_at <= now() - interval '30 minutes'
                FROM session
                WHERE id = %s
                FOR UPDATE
                """,
                (session_id,),
            ).fetchone()
            if row is None or row[1]:
                return None
            if row[2]:
                connection.execute(
                    """
                    UPDATE session
                    SET ended_at = now()
                    WHERE id = %s
                      AND ended_at IS NULL
                    """,
                    (session_id,),
                )
                return None
            return row[0]


def set_last_active_at(
    connection: psycopg.Connection,
    session_id: uuid.UUID,
    account_id: uuid.UUID,
) -> None:
    connection.execute(
        """
        UPDATE session
        SET last_active_at = now()
        WHERE id = %s
          AND account_id = %s
        """,
        (session_id, account_id),
    )
