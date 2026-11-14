# Schedules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person tells the agent when work should happen, and Folio runs it at that time and writes the result in the thread.

**Architecture:** Two new tables, each with its own module. Three operations for the agent. A worker app, separate from the public API, with a tick route and a run route. A queue interface with a Cloud Tasks form and a local form. Terraform for the worker service, the queue, and the clock.

**Tech Stack:** Python 3.13, FastAPI, psycopg 3, Pydantic, LangGraph, Postgres 18, Cloud Run, Cloud Tasks, Cloud Scheduler, Terraform, Next.js 16, React 19.

**Spec:** `docs/superpowers/specs/2026-11-14-schedules-design.md`

## Global Constraints

- The only schema changes are the two tables and the two foreign keys written in Task 1, exactly as written. No other table, column, or constraint.
- One module per table: `backend/app/schedule.py` and `backend/app/run.py`, with `apply(connection, force=False)` in the style of `backend/app/thread.py`. `backend/app/migrate.py` runs both with `force=True`.
- Every SQL statement on these tables filters on `account_id`, except the tick's search for due schedules and stuck runs, which is across accounts by design and never returns a row to a person.
- The worker's routes exist only in `backend/app/worker.py`. `backend/app/main.py` never imports or mounts them. The worker app has no session routes.
- The model never supplies an account id. A row of another account is reported as not found.
- Every model call writes a usage row. A call made for a schedule has the operation `schedule`.
- A refused operation returns `{"ok": False, "code": ..., "error": <sentence>}` and never raises for bad input or a missing row.
- One new backend dependency is allowed: `google-cloud-tasks`. No other new dependency in the backend or the frontend.
- No AI or tool attribution anywhere. No em dashes anywhere. Comments only for a why that is not obvious. English copy. Docs in simple English, following `docs/conventions.md`.
- Backend checks, from `backend/`: `uv run ruff check .` and `uv run pytest`. Frontend checks, from `frontend/`: `npm run lint`, `npm run typecheck`, `npm run build`. Terraform checks, from `infra/`: `terraform fmt -check -recursive` and `terraform init -backend=false && terraform validate`. The frontend has no test runner. Do not add one.
- Terraform is never applied by an agent or by CI. A person applies it.
- Branches: `schedules` (Tasks 1 to 3), `schedules-web` (Task 4), `schedules-infra` (Task 5). Each starts from `schedules-design`. Tasks 4 and 5 run at the same time as Tasks 1 to 3.

## Review Focus

1. Cloud Tasks delivers the same run twice, or two tick calls overlap. Expected: one run row per occurrence, one result message, no error. Tests in Task 3.
2. A second account lists, changes, or deletes with the first account's schedule id, or a run of one account reads the other's memory. Expected: not found, nothing changes. Tests in Task 2 and Task 3.
3. The model answers the next-time question with text that is not a time, with a time in the past, or the call fails. Expected: the next time is stored as none, one line is written in the thread, and the failure is logged. Test in Task 3.
4. The thread is deleted while its run is queued or working. Expected: the schedule is gone, the run finishes without a message and without an error, and nothing is retried. Test in Task 3.
5. `write_schedule` is given a time with no zone, a time in the past, or text that is not a time. Expected: refused with a sentence, nothing stored. Test in Task 2.

---

### Task 1: The two tables and their modules

**Files:**
- Create: `backend/migrations/schedule.sql`, `backend/migrations/run.sql`, `backend/app/schedule.py`, `backend/app/run.py`
- Modify: `backend/app/migrate.py`, `backend/tests/test_migrate.py` (add both tables to `TABLES`)
- Test: `backend/tests/test_schedule.py`, `backend/tests/test_run.py`

**The SQL, exactly:**

```sql
-- schedule.sql
CREATE TABLE IF NOT EXISTS schedule (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    thread_id uuid NOT NULL,
    task text NOT NULL,
    when_text text NOT NULL,
    next_run_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, account_id),
    CONSTRAINT schedule_thread_account_fkey FOREIGN KEY (thread_id, account_id)
        REFERENCES thread (id, account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS schedule_next_run_at
    ON schedule (next_run_at)
    WHERE next_run_at IS NOT NULL;

-- run.sql
CREATE TABLE IF NOT EXISTS run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    thread_id uuid,
    schedule_id uuid,
    kind text NOT NULL,
    status text NOT NULL,
    task text NOT NULL,
    question text,
    work_ms integer NOT NULL DEFAULT 0,
    attempt_count integer NOT NULL DEFAULT 0,
    delivered_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, account_id),
    CONSTRAINT run_kind_allowed CHECK (kind IN ('research', 'schedule', 'browser', 'web')),
    CONSTRAINT run_status_allowed CHECK (
        status IN ('queued', 'working', 'waiting', 'ready', 'failed', 'cancelled', 'dead')
    ),
    CONSTRAINT run_thread_account_fkey FOREIGN KEY (thread_id, account_id)
        REFERENCES thread (id, account_id) ON DELETE SET NULL (thread_id),
    CONSTRAINT run_schedule_account_fkey FOREIGN KEY (schedule_id, account_id)
        REFERENCES schedule (id, account_id) ON DELETE SET NULL (schedule_id)
);

CREATE INDEX IF NOT EXISTS run_status_queued
    ON run (status)
    WHERE status = 'queued';

CREATE UNIQUE INDEX IF NOT EXISTS run_one_in_flight_per_schedule
    ON run (schedule_id)
    WHERE schedule_id IS NOT NULL AND status IN ('queued', 'working', 'waiting');

-- The two keys that waited for this table. Each is added once.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'message_run_account_fkey') THEN
        ALTER TABLE message ADD CONSTRAINT message_run_account_fkey
            FOREIGN KEY (run_id, account_id) REFERENCES run (id, account_id)
            ON DELETE SET NULL (run_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'usage_run_account_fkey') THEN
        ALTER TABLE usage ADD CONSTRAINT usage_run_account_fkey
            FOREIGN KEY (run_id, account_id) REFERENCES run (id, account_id)
            ON DELETE SET NULL (run_id);
    END IF;
END $$;
```

`run.apply` applies `schedule`, `message`, and `usage` first, because its keys name them.

**Interfaces:**

```python
# app/schedule.py
ScheduleRow = tuple[uuid.UUID, uuid.UUID, uuid.UUID, str, str, datetime | None, datetime, datetime]
    # id, account_id, thread_id, task, when_text, next_run_at, created_at, updated_at
def apply(connection, force: bool = False) -> None
def list_schedules(connection, account_id) -> list[ScheduleRow]                 # soonest first, none last
def get_schedule(connection, account_id, schedule_id) -> ScheduleRow | None
def create_schedule(connection, account_id, thread_id, task, when_text, next_run_at) -> ScheduleRow | None
    # None when the thread is not the account's
def update_schedule(connection, account_id, schedule_id, task, when_text, next_run_at) -> ScheduleRow | None
def delete_schedule(connection, account_id, schedule_id) -> bool
def due_schedules(connection, limit: int = 50) -> list[ScheduleRow]              # next_run_at <= now(), all accounts
def set_next_run(connection, account_id, schedule_id, expected: datetime, next_run_at: datetime | None) -> bool
    # changes the row only when next_run_at still equals expected; False otherwise

# app/run.py
RunRow = tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, uuid.UUID | None, str, str, str, int, int, datetime | None, datetime]
    # id, account_id, thread_id, schedule_id, kind, status, task, work_ms, attempt_count, delivered_at, created_at
def apply(connection, force: bool = False) -> None
def create_run(connection, account_id, thread_id, schedule_id, kind: str, task: str) -> RunRow | None
    # None when a run of that schedule is already queued, working, or waiting (the unique index)
def get_run(connection, run_id) -> RunRow | None                                  # by id only: the worker has no session
def start_attempt(connection, run_id) -> RunRow | None
    # sets status working and adds one to attempt_count, only when status is queued or working
    # and delivered_at is empty; None otherwise
def finish_run(connection, run_id, status: str, work_ms: int, delivered: bool) -> None
def in_flight_threads(connection, account_id) -> set[uuid.UUID]                   # threads with a queued or working run
def stuck_runs(connection, minutes: int = 30) -> list[RunRow]                     # working, created_at older than that
```

- [ ] **Step 1: Write the failing tests** (helpers as in `backend/tests/test_operations.py`): create, list order, update, delete; another account cannot get, update, or delete; deleting the thread deletes its schedules and clears `run.thread_id`; deleting a schedule clears `run.schedule_id`; `create_run` returns None for a second in-flight run of one schedule and succeeds after the first is `ready`; `start_attempt` counts attempts and returns None once `delivered_at` is set; `set_next_run` changes nothing when `expected` no longer matches; `due_schedules` returns only rows whose time has come; deleting a run clears `message.run_id` and `usage.run_id`; a run with an unknown status or kind is refused by the database.
- [ ] **Step 2: Run** them. Expected: FAIL, modules missing.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run ruff check . && uv run pytest -q`. Expected: all pass, also on a new empty database.
- [ ] **Step 5: Commit** `Store schedules and runs.`

---

### Task 2: Schedule operations and the time zone

**Files:**
- Modify: `backend/app/operations.py`, `backend/app/turn.py`, `backend/app/main.py`
- Test: `backend/tests/test_schedule_operations.py`, additions to `backend/tests/test_thread_routes.py`

**Interfaces:**
- `Operation` gains `chat_only: bool = False`. `agent_tools(in_run: bool = False)` leaves out operations with `chat_only=True` when `in_run` is true. `call(..., in_run: bool = False)` answers `{"ok": False, "code": "unknown", "error": UNKNOWN}` for a `chat_only` operation when `in_run` is true.
- Three operations, all `agent=True`:

| Name | Input fields | `chat_only` | `ok` result |
| --- | --- | --- | --- |
| `list_schedules` | none | no | `{"schedules": [{"id", "task", "when_text", "next_run_at", "thread_id"}]}` |
| `write_schedule` | `schedule_id: uuid.UUID \| None = None`, `task: str`, `when_text: str`, `next_run_at: str` | yes | `{"schedule": {"id", "task", "when_text", "next_run_at"}}` |
| `delete_schedule` | `schedule_id: uuid.UUID` | yes | `{"schedule_id"}` |

- Constants: `NO_SCHEDULE = "Schedule not found."`, `BAD_TIME = "Give the next time as a date and time with a time zone, in the future."`, `BAD_TASK = "A schedule needs a task and the words for when."`.
- Rules: `task` and `when_text` are stripped and each must be 1 to 2,000 characters with no null character and encodable as UTF-8, else `invalid` with `BAD_TASK`. `next_run_at` is parsed with `datetime.fromisoformat`; it must carry a zone offset and be later than now, else `invalid` with `BAD_TIME`. A new schedule belongs to `context.thread_id`; with no thread in the context it is `not_found` with the existing thread sentence. A missing schedule, or another account's, is `not_found` with `NO_SCHEDULE`.
- The descriptions tell the model: put the time zone into `when_text`; give `next_run_at` in ISO 8601 with an offset; a schedule belongs to the thread of this conversation.
- `POST /threads/{id}/messages` and `POST /threads/{id}/retry` accept an optional `time_zone` string in the body. It is used only when `zoneinfo.ZoneInfo(name)` accepts it. Otherwise UTC is used. It is never stored.
- `_system_prompt` gains one line, after the memory instruction: `The time now is {now in that zone, ISO 8601 to the minute, with offset}. The person's time zone is {zone name}.` Tests must stay meaningful with a moving clock: pass the clock in, or match the line by pattern.

- [ ] **Step 1: Write the failing tests:** create in the turn's thread and list; update by id; delete; another account's id is not found for update and delete and its row is unchanged; every bad time in Review Focus 5 is refused and nothing is stored; `agent_tools(in_run=True)` has `list_schedules` and lacks the other two; `call(..., in_run=True)` refuses `write_schedule`; deleting the thread through the `delete_thread` operation removes its schedules; a message sent with `time_zone` `Asia/Tokyo` puts that zone and a `+09:00` time in the prompt the model sees; a bad zone name gives UTC and no error; through the stream helpers, the scripted model calls `write_schedule` and the schedule exists in that thread.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run ruff check . && uv run pytest -q`.
- [ ] **Step 5: Commit** `Let the agent create, change, and delete schedules.`

---

### Task 3: The worker, the queue, and the read routes

**Files:**
- Create: `backend/app/worker.py`, `backend/app/queue.py`, `backend/app/scheduled.py`
- Modify: `backend/app/main.py` (two read changes only), `backend/pyproject.toml` (`google-cloud-tasks`), `backend/uv.lock`, `docs/product/schedules.md`, `docs/product/failure.md`, `docs/diagrams/schema.html` (`run.created_at`), `docs/plan/decisions.md`, `README.md`, `docs/hand-in/report.md`
- Test: `backend/tests/test_scheduled.py`, `backend/tests/test_worker.py`, `backend/tests/test_queue.py`

**Interfaces:**

```python
# app/queue.py
def enqueue(run_id: uuid.UUID) -> None
```

- When the environment has `RUN_QUEUE` (the full queue path), `WORKER_URL`, and `INVOKER_ACCOUNT`, `enqueue` creates a Cloud Tasks HTTP task: `POST {WORKER_URL}/runs/{run_id}`, an OIDC token for `INVOKER_ACCOUNT` with the audience `WORKER_URL`, the task name ending in the run id so a second enqueue of one run is refused by the queue, and a dispatch deadline of 1800 seconds. An "already exists" answer is not an error.
- Without those variables, `enqueue` runs `scheduled.execute(run_id)` in a new thread. This is the local form.
- The Cloud Tasks client is imported inside the function, so tests and local runs never load it.

```python
# app/scheduled.py
FAILED_LINE = "A scheduled run failed."
NO_NEXT_LINE = "This schedule has no next time now. Tell Folio when it should run again."
MAX_ATTEMPTS = 4

def tick(model: Model) -> dict[str, int]
    # {"due", "started", "skipped", "held", "stuck"}
def next_time(model: Model, when_text: str, now: datetime) -> tuple[datetime | None, Reply | None, bool]
    # (the time or None, the reply for the usage row or None, failed)
def execute(run_id: uuid.UUID, model: Model | None = None) -> str
    # the final status, or "duplicate" when the run was already delivered, or "retry" when it should be tried again
```

- `tick`, for each due schedule, in this order:
  1. Load the account's email, cap, stop, and recorded tokens. Stopped or at the cap: count `held`, change nothing.
  2. Ask `next_time`. Record its usage row with the operation `schedule`.
  3. In one transaction: `set_next_run(..., expected=<the due time>, next_run_at=<answer>)`. If it returns False another tick took it: stop here. Then `create_run`. None means a run is in flight: count `skipped`. Otherwise count `started`.
  4. After the transaction commits, `enqueue` the new run. If `next_time` failed, write `NO_NEXT_LINE` as an assistant message of kind `failure` in the thread and log an error with the schedule id.
- `tick` then handles `stuck_runs()`: each is finished as `dead`, `FAILED_LINE` is written in its thread when the thread still exists, and an error is logged with the run id.
- `next_time` sends one `model.complete` call with a fixed instruction: answer with the next date and time after now, in ISO 8601 with an offset, or the single word `none`. It parses the reply with `datetime.fromisoformat`. `none` gives `(None, reply, False)`. Text that is not a time, a time without an offset, a time not later than now, or an exception gives `(None, reply or None, True)`.
- `execute`:
  1. `start_attempt`. None: return `"duplicate"`.
  2. Stopped or at the cap: finish as `cancelled`, return `"cancelled"`.
  3. Build the prompt with the same `_system_prompt` as chat, plus the line `You are running a scheduled task while the person is away. Do the task and write the result for them. Do not ask a question.` Send one person block with the run's task. Run `stream_turn` with `operations.agent_tools(in_run=True)` and a `call_tool` that uses `operations.call(..., in_run=True)` with `Context(run.thread_id, Turn(opened=set()))`.
  4. On success, in one transaction: insert the text as an assistant message of kind `result` with `run_id` when the thread still exists, write one usage row per model call with the operation `schedule`, `run_id`, and the message id, set `delivered_at`, and finish as `ready`. Return `"ready"`.
  5. On an exception: log it with the run id, write the usage rows for the calls that were made, and when `attempt_count < MAX_ATTEMPTS` return `"retry"`. Otherwise finish as `dead`, write `FAILED_LINE` in the thread, log an error, and return `"dead"`.
  6. `work_ms` is the time spent in step 3.

```python
# app/worker.py : a separate FastAPI app, `app.worker:app`
POST /tick          -> 200 with the counts from tick()
POST /runs/{run_id} -> 200 for ready, duplicate, cancelled, dead; 503 for retry, so the queue tries again; 404 for an id that is not a run
GET  /health        -> {"status": "ok"}
```

- `main.py`: `GET /schedules` returns `[{"id", "task", "when_text", "next_run_at", "thread_id", "thread_name"}]` for the session's account, soonest first. `GET /threads` items gain `"working": bool` from `in_flight_threads`. `main.py` gains nothing else.

- [ ] **Step 1: Write the failing tests.** Use `app.model.FakeModel(script=...)`. Set a schedule's `next_run_at` in the past with SQL to make it due. Monkeypatch `app.scheduled.enqueue` to collect run ids, then call `execute` directly.
  - A due schedule gets one run, the scripted next time is stored, and the run id is enqueued. A schedule that is not due is untouched.
  - Two `tick` calls in a row for one due time make one run. Two threads calling `tick` at once make one run.
  - A run in flight: the occurrence is counted `skipped`, the next time still moves on, no second run.
  - A stopped account and a capped account: `held`, `next_run_at` unchanged, no model call, no usage row.
  - Every case of Review Focus 3: next time is none, `NO_NEXT_LINE` is in the thread once, an error is logged.
  - `none` from the model: next time is empty, no line is written, the run still starts.
  - `execute`: the result is one message of kind `result` with the run id; usage rows have the operation `schedule` and the run id; the status is `ready`; `delivered_at` is set. A second `execute` returns `"duplicate"` and adds no message and no usage row.
  - A run uses memory: a scripted `read_knowledge` call returns the account's file. A scripted `write_schedule` call inside a run is answered with `unknown` and creates nothing.
  - A failing model: `"retry"` three times with `attempt_count` 1, 2, 3, then `"dead"` on the fourth with `FAILED_LINE` once in the thread and one error logged.
  - Review Focus 4: the thread is deleted after the run is made. `execute` returns without an error, writes no message, and is not `"retry"`.
  - A stuck run, with `created_at` set 31 minutes back: the next `tick` marks it dead, writes the line, and the schedule can start a new run.
  - Worker routes with `TestClient(app.worker.app)`: the status codes above. `app.main.app` has no `/tick` and no `/runs/{id}` route.
  - `GET /schedules` and the `working` field need a session and show only the caller's rows.
  - `enqueue` without the environment runs the run locally. With the environment, a stub client receives the URL, the audience, the service account, and a task name ending in the run id.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Update the docs.** `schedules.md` and `failure.md` state what is built: the tick, the next time from the model, the skipped occurrence, the four attempts, the dead run, the 30 minute rule. The schema picture gains `run.created_at`. `decisions.md` records the designer's calls from the spec. The README status table moves schedules to "Present" and adds how to run the worker locally (`uv run uvicorn app.worker:app --port 8001`, and `curl -X POST localhost:8001/tick`). The report draft gains short lines under the periodic execution feature with the reasons, taken from the spec's questions and answers.
- [ ] **Step 5: Run** `uv run ruff check . && uv run pytest -q`.
- [ ] **Step 6: Commit** `Run a schedule at its time and write the result in the thread.`

---

### Task 4: Schedules in the website

Branch `schedules-web`, from `schedules-design`. Build against the route shapes in Tasks 2 and 3.

**Files:**
- Modify: `frontend/lib/api.ts`, `frontend/app/profile-panel.tsx`, `frontend/app/thread-list.tsx`, `frontend/app/folio-app.tsx`, `frontend/app/globals.css`

Behavior:
- `api.ts`: `Thread` gains `working: boolean`, read as false when the field is missing. `ScheduleItem = { id; task; when_text; next_run_at: string | null; thread_id; thread_name }` and `listSchedules()`. `streamThreadMessage` and `streamThreadRetry` send `time_zone` from `Intl.DateTimeFormat().resolvedOptions().timeZone` in the request body, left out when it is not a string.
- The profile panel gains a third section after Abilities, with the heading "Schedules". Each item shows the task, then the words for when, then "Next: " and the next time in the browser's locale, or "No next time" when it is empty, then "In: " and the thread name. The empty state, exactly: "No schedules yet. Tell Folio when something should happen." It reloads with `reloadKey` like the other lists.
- The thread list shows a small mark before the name of a thread whose `working` is true: a quiet pulsing dot with the label "Working" for screen readers (`role="status"` is not used on each row; use visually hidden text). It respects `prefers-reduced-motion`.
- While at least one thread is `working`, `folio-app.tsx` asks for the thread list again every 20 seconds. The timer stops when none is working, when the page is hidden (`document.visibilityState`), and on sign-out. When the open thread goes from working to not working, its messages are loaded again and `reloadKey` is raised.
- A message of kind `result` renders as an assistant reply, with Markdown, as a chat reply does.
- `toolStatus` gains: `list_schedules` "Looking at your schedules", `write_schedule` "Saving a schedule", `delete_schedule` "Deleting a schedule".
- No control creates, changes, or deletes a schedule.

- [ ] **Step 1 to 3:** the API functions, the panel section, the mark and the timer.
- [ ] **Step 4: Run** `npm run lint && npm run typecheck && npm run build`.
- [ ] **Step 5: Commit** `Show schedules in the profile panel and mark a thread while a run works.`

---

### Task 5: The cloud

Branch `schedules-infra`, from `schedules-design`.

**Files:**
- Create: `infra/worker.tf`
- Modify: `infra/versions.tf` or the file that lists enabled services (add `cloudtasks.googleapis.com` and `cloudscheduler.googleapis.com`), `infra/outputs.tf`, `infra/README.md`, `.github/workflows/deploy.yml`

Resources, following the names and style of `infra/run.tf`:
- `google_service_account.worker` (`folio-worker`, the worker's own identity) with access to the same two secrets as the API, and `roles/cloudtasks.enqueuer` on the queue.
- `google_service_account.invoker` (`folio-invoker`). It has `roles/run.invoker` on the worker service and nothing else.
- The worker identity may act as the invoker (`roles/iam.serviceAccountUser` on the invoker account), which Cloud Tasks requires to attach the invoker's token.
- `google_cloud_run_v2_service.worker` (`folio-worker`): the same image variable as the API, `args` or `command` so it starts `uvicorn app.worker:app --host 0.0.0.0 --port 8080`, `ingress = "INGRESS_TRAFFIC_ALL"` with no public invoker binding (only the invoker account may call it), the same VPC access, the same plain and secret environment as the API, plus `RUN_QUEUE`, `WORKER_URL`, and `INVOKER_ACCOUNT`, a request timeout of 1800 seconds, `min_instance_count = 0`, `max_instance_count = 2`, and the same `ignore_changes` on the image. `WORKER_URL` must not create a cycle: use the service's own deterministic URL from the project number and region, or set it with a second small resource; say which in the README.
- `google_cloud_tasks_queue.runs` (`folio-runs`): `max_attempts = 4`, `min_backoff = "10s"`, `max_backoff = "300s"`, `max_concurrent_dispatches = 3`.
- `google_cloud_scheduler_job.tick` (`folio-tick`): every minute, `POST {worker url}/tick`, an OIDC token for the invoker with the worker URL as the audience, an attempt deadline of 60 seconds, and no retries, since the next minute is the retry.
- The deploy identity may deploy the worker service and act as the worker identity, as it already may for the API.
- Outputs: the worker URL and the queue name.

`deploy.yml`: after the migration job and before the live check, deploy the same image to `folio-worker` with the same command the API step uses, keeping the worker's start command and environment as Terraform set them. The step must not fail the first deploy before a person has applied Terraform: if the worker service does not exist yet, print a clear line and skip.

`infra/README.md`: what the three new things are for, in simple English, and the order for a person: apply Terraform, then merge or re-run the deploy.

- [ ] **Step 1: Write** the Terraform and the workflow step.
- [ ] **Step 2: Run** `terraform fmt -recursive`, then `terraform fmt -check -recursive` and `terraform init -backend=false && terraform validate`. Expected: pass. Do not run `terraform plan` or `apply`.
- [ ] **Step 3: Commit** `Describe the worker, the run queue, and the minute clock in Terraform.`
