from app import ability, knowledge_file, schedule, session, usage
from app import run as run_table
from app.db import connect


def run() -> None:
    # usage.apply and session.apply each apply what they depend on first. Every step is repeatable.
    with connect() as connection:
        usage.apply(connection, force=True)
        session.apply(connection, force=True)
        knowledge_file.apply(connection, force=True)
        ability.apply(connection, force=True)
        schedule.apply(connection, force=True)
        run_table.apply(connection, force=True)


if __name__ == "__main__":
    run()
