from app import session, usage
from app.db import connect


def run() -> None:
    # usage.apply and session.apply each apply what they depend on first. Every step is repeatable.
    with connect() as connection:
        usage.apply(connection, force=True)
        session.apply(connection, force=True)


if __name__ == "__main__":
    run()
