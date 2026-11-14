import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg

from app.db import table_exists
from app.message import apply as apply_message
from app.model import MODEL_ID

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "usage.sql"

# https://developers.openai.com/api/docs/pricing short context input $2 output $10 per 1M.
_PRICES: dict[tuple[str, str], tuple[Decimal, Decimal]] = {
    ("openai", MODEL_ID): (
        Decimal("2.00") / Decimal(1_000_000),
        Decimal("10.00") / Decimal(1_000_000),
    ),
}

UsageRow = tuple[
    uuid.UUID,
    uuid.UUID | None,
    str,
    datetime,
    str,
    str,
    int | None,
    int | None,
    int | None,
    Decimal | None,
    bool,
    uuid.UUID | None,
    uuid.UUID | None,
    str,
]


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "usage"):
        return
    apply_message(connection, force)
    connection.execute(_MIGRATION.read_text())


def record_usage(
    connection: psycopg.Connection,
    account_id: uuid.UUID | None,
    email: str,
    operation: str,
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    message_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> UsageRow:
    apply(connection)
    usage_missing = (
        input_tokens is None or output_tokens is None or total_tokens is None
    )
    expected_cost: Decimal | None = None
    if (
        input_tokens is not None
        and output_tokens is not None
        and total_tokens is not None
    ):
        price = _PRICES.get((provider, model))
        if price is not None:
            input_price, output_price = price
            expected_cost = input_tokens * input_price + output_tokens * output_price
    stored = connection.execute(
        """
        INSERT INTO usage (
            account_id,
            email,
            provider,
            model,
            input_tokens,
            output_tokens,
            total_tokens,
            expected_cost,
            usage_missing,
            message_id,
            run_id,
            operation
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING
            id,
            account_id,
            email,
            at,
            provider,
            model,
            input_tokens,
            output_tokens,
            total_tokens,
            expected_cost,
            usage_missing,
            message_id,
            run_id,
            operation
        """,
        (
            account_id,
            email.lower(),
            provider,
            model,
            input_tokens,
            output_tokens,
            total_tokens,
            expected_cost,
            usage_missing,
            message_id,
            run_id,
            operation,
        ),
    ).fetchone()
    if stored is None:
        raise RuntimeError("usage was not stored")
    return stored


def tokens_for_email(connection: psycopg.Connection, email: str) -> int:
    apply(connection)
    total = connection.execute(
        """
        SELECT SUM(total_tokens)
        FROM usage
        WHERE email = %s
          AND usage_missing = false
        """,
        (email.lower(),),
    ).fetchone()
    if total is None or total[0] is None:
        return 0
    return int(total[0])


def token_totals_for_email(connection: psycopg.Connection, email: str) -> tuple[int, int, int]:
    apply(connection)
    row = connection.execute(
        """
        SELECT COALESCE(SUM(input_tokens), 0),
               COALESCE(SUM(output_tokens), 0),
               COALESCE(SUM(total_tokens), 0)
        FROM usage
        WHERE email = %s
          AND usage_missing = false
        """,
        (email.lower(),),
    ).fetchone()
    if row is None:
        return 0, 0, 0
    return int(row[0]), int(row[1]), int(row[2])
