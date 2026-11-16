import logging
import uuid
from collections.abc import Callable

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from app import scheduled
from app.db import connect
from app.file import discard_stale
from app.model import Model, open_model
from app.run import get_run
from app.storage import open_storage

# Closed to the internet by Cloud Run, so only the queue and the clock can call these routes.
# There are no session routes here.
app = FastAPI()
_log = logging.getLogger("folio.worker")


def handle_run(run_id: str, model: Callable[[], Model]) -> Response:
    # Shared by the schedule worker and the research service.
    try:
        parsed = uuid.UUID(run_id)
    except ValueError:
        return JSONResponse(status_code=404, content={"error": "Run not found."})
    with connect() as connection:
        found = get_run(connection, parsed)
    if found is None:
        return JSONResponse(status_code=404, content={"error": "Run not found."})
    outcome = scheduled.execute(parsed, model())
    if outcome == "retry":
        # Any answer but a success makes Cloud Tasks deliver the run again later.
        return JSONResponse(status_code=503, content={"status": outcome})
    return JSONResponse(content={"status": outcome})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tick")
def post_tick() -> dict[str, int]:
    counts = scheduled.tick(open_model())
    try:
        # An upload whose bytes never arrived is dropped after a day, and stops counting.
        with connect() as connection:
            discard_stale(connection, open_storage())
    except Exception as error:
        _log.error("unfinished uploads could not be discarded: %s", type(error).__name__)
    return counts


@app.post("/runs/{run_id}")
def post_run(run_id: str) -> Response:
    return handle_run(run_id, open_model)
