import threading
import uuid

import pytest
from app import queue as run_queue
from google.api_core.exceptions import AlreadyExists, PermissionDenied
from google.cloud import tasks_v2

QUEUE = "projects/p/locations/europe-west1/queues/folio-runs"
WORKER = "https://folio-worker-abc.a.run.app"
INVOKER = "folio-invoker@p.iam.gserviceaccount.com"


class _Stub:
    def __init__(self, error: Exception | None = None) -> None:
        self.requests: list[dict] = []
        self._error = error

    def create_task(self, request: dict) -> None:
        self.requests.append(request)
        if self._error is not None:
            raise self._error


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_QUEUE", QUEUE)
    monkeypatch.setenv("WORKER_URL", WORKER)
    monkeypatch.setenv("INVOKER_ACCOUNT", INVOKER)


@pytest.fixture
def local(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("RUN_QUEUE", "WORKER_URL", "INVOKER_ACCOUNT"):
        monkeypatch.delenv(name, raising=False)


def test_the_cloud_form_makes_one_named_task_with_a_token(cloud) -> None:
    run_id = uuid.uuid4()
    stub = _Stub()

    run_queue.enqueue(run_id, client=stub)

    assert len(stub.requests) == 1
    request = stub.requests[0]
    assert request["parent"] == QUEUE
    task = request["task"]
    assert task["name"].endswith(f"/tasks/{run_id}")
    assert task["name"].startswith(QUEUE)
    assert task["dispatch_deadline"] == {"seconds": 1800}
    http = task["http_request"]
    assert http["url"] == f"{WORKER}/runs/{run_id}"
    assert http["http_method"] == tasks_v2.HttpMethod.POST
    assert http["oidc_token"] == {"service_account_email": INVOKER, "audience": WORKER}
    # Only the run id travels in the task.
    assert "body" not in http
    # The request is a shape the Cloud Tasks library accepts.
    assert tasks_v2.CreateTaskRequest(request).task.http_request.url.endswith(str(run_id))


def test_a_trailing_slash_on_the_worker_address_is_not_doubled(
    cloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKER_URL", WORKER + "/")
    run_id = uuid.uuid4()
    stub = _Stub()

    run_queue.enqueue(run_id, client=stub)

    http = stub.requests[0]["task"]["http_request"]
    assert http["url"] == f"{WORKER}/runs/{run_id}"
    assert http["oidc_token"]["audience"] == WORKER


def test_a_task_that_already_exists_is_not_an_error(cloud) -> None:
    run_queue.enqueue(uuid.uuid4(), client=_Stub(AlreadyExists("again")))


def test_another_failure_is_raised(cloud) -> None:
    with pytest.raises(PermissionDenied):
        run_queue.enqueue(uuid.uuid4(), client=_Stub(PermissionDenied("no")))


def test_the_local_form_runs_the_run_in_another_thread(
    local, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = uuid.uuid4()
    done = threading.Event()
    seen: list[tuple[uuid.UUID, str]] = []

    def execute(given: uuid.UUID) -> str:
        seen.append((given, threading.current_thread().name))
        done.set()
        return "ready"

    monkeypatch.setattr("app.scheduled.execute", execute)

    run_queue.enqueue(run_id)

    assert done.wait(timeout=10)
    assert seen[0][0] == run_id
    assert seen[0][1] != threading.main_thread().name


def test_the_local_form_tries_again_after_a_retry(local, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = ["retry", "retry", "ready"]
    calls: list[int] = []
    done = threading.Event()

    def execute(given: uuid.UUID) -> str:
        calls.append(1)
        answer = answers.pop(0)
        if answer == "ready":
            done.set()
        return answer

    monkeypatch.setattr("app.scheduled.execute", execute)

    run_queue.enqueue(uuid.uuid4())

    assert done.wait(timeout=10)
    assert len(calls) == 3


def test_the_cloud_client_is_not_loaded_for_the_local_form(local) -> None:
    import sys

    # The module imports nothing from Cloud Tasks at the top.
    source = run_queue.__file__
    with open(source) as handle:
        head = handle.read().split("def enqueue")[0]
    assert "google.cloud" not in head
    assert "tasks_v2" not in head
    assert sys.modules.get("app.queue") is run_queue
