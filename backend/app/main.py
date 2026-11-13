from fastapi import FastAPI, Response

from app.db import database_ready

app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready(response: Response) -> dict[str, str]:
    if database_ready():
        return {"status": "ok"}
    response.status_code = 503
    return {"status": "down"}
