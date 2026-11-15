import os
import threading
import uuid
from typing import Any

DISPATCH_DEADLINE_SECONDS = 1800


# The variables each kind of run needs, as (queue, service address). The invoker is shared.
_TARGETS = {
    "schedule": ("RUN_QUEUE", "WORKER_URL"),
    "research": ("RESEARCH_QUEUE", "RESEARCH_URL"),
}


class QueueNotConfigured(Exception):
    pass


def enqueue(run_id: uuid.UUID, kind: str = "schedule", client: Any = None) -> None:
    queue_name, url_name = _TARGETS.get(kind, _TARGETS["schedule"])
    queue = os.environ.get(queue_name)
    worker_url = os.environ.get(url_name)
    invoker = os.environ.get("INVOKER_ACCOUNT")
    if queue and worker_url and invoker:
        _put_on_cloud_queue(run_id, queue, worker_url.rstrip("/"), invoker, client)
        return
    if os.environ.get("K_SERVICE"):
        # On Cloud Run a missing variable is a setup mistake. Running the run in a thread of a
        # service that may stop at any moment would lose it, so it is refused instead.
        raise QueueNotConfigured(kind)
    threading.Thread(target=_run_here, args=(run_id,), name="folio-run").start()


def _run_here(run_id: uuid.UUID) -> None:
    # Imported here because scheduled imports this module.
    from app import scheduled

    # Cloud Tasks retries a run itself. On a laptop nothing does, so the same attempts happen here.
    for _ in range(scheduled.MAX_ATTEMPTS):
        if scheduled.execute(run_id) != "retry":
            return


def _put_on_cloud_queue(
    run_id: uuid.UUID, queue: str, worker_url: str, invoker: str, client: Any
) -> None:
    # Imported here so tests and a laptop never load the Cloud Tasks library.
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import tasks_v2

    if client is None:
        client = tasks_v2.CloudTasksClient()
    request = {
        "parent": queue,
        "task": {
            # A task name is kept by the queue, so a second put of one run is refused.
            "name": f"{queue}/tasks/{run_id}",
            "dispatch_deadline": {"seconds": DISPATCH_DEADLINE_SECONDS},
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{worker_url}/runs/{run_id}",
                "oidc_token": {"service_account_email": invoker, "audience": worker_url},
            },
        },
    }
    try:
        client.create_task(request=request)
    except AlreadyExists:
        return
