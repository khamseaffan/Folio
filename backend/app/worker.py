import uuid

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from app import scheduled
from app.db import connect
from app.model import open_model
from app.run import get_run

# Closed to the internet by Cloud Run, so only the queue and the clock can call these routes.
# There are no session routes here.
app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tick")
def post_tick() -> dict[str, int]:
    return scheduled.tick(open_model())


@app.post("/runs/{run_id}")
def post_run(run_id: str) -> Response:
    try:
        parsed = uuid.UUID(run_id)
    except ValueError:
        return JSONResponse(status_code=404, content={"error": "Run not found."})
    with connect() as connection:
        found = get_run(connection, parsed)
    if found is None:
        return JSONResponse(status_code=404, content={"error": "Run not found."})
    outcome = scheduled.execute(parsed, open_model())
    if outcome == "retry":
        # Any answer but a success makes Cloud Tasks deliver the run again later.
        return JSONResponse(status_code=503, content={"status": outcome})
    return JSONResponse(content={"status": outcome})
