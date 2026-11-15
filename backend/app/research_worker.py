from fastapi import FastAPI, Response

from app.model import open_model
from app.worker import handle_run

# Closed to the internet by Cloud Run, so only the queue can call it. It has no tick and no
# session routes.
app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs/{run_id}")
def post_run(run_id: str) -> Response:
    return handle_run(run_id, open_model)
