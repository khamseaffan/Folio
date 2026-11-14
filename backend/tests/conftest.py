import pytest
from app.migrate import run


@pytest.fixture(scope="session", autouse=True)
def _tables() -> None:
    # A request creates only a missing table, so a change to an existing one needs the migration.
    run()
