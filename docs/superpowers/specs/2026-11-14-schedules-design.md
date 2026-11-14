# Schedules: work that runs while the person is away

Date: 2026-11-14. Builds on the operation layer and on memory.

## Intent

A person tells Folio when a piece of work should happen, and Folio does it at that time, with nobody at the screen. `Goal.md` names "periodic autonomous execution" as an original feature. The product rules are in `docs/product/schedules.md`, `docs/product/failure.md`, and `docs/product/records.md`. This design says how they are built.

This is also the first work that runs outside a chat request. The queue and the worker built here are the base for research and the browser later.

## What the person gets

- They say in a thread, for example, "every weekday at 9, give me three things to focus on from my notes".
- The agent creates the schedule and says when it will first run.
- At that time Folio does the work and writes the result as a message in that thread.
- The thread name shows a mark while a run is working.
- They can ask the agent to change or delete the schedule. Deleting the thread deletes its schedules.
- They can read the upcoming schedules in the profile panel.

## Goals

- Schedules created, changed, and deleted by the agent through operations.
- A schedule fires at its time and runs the task with the agent's tools and memory.
- A run writes exactly one result, even if the queue delivers it twice.
- When a run of a schedule is still working, the next occurrence of that schedule is skipped.
- A failed run is retried three times, then marked dead, told to the person in one line, and logged.
- A stopped account, or an account at its token cap, does not start a run.
- Every model call of a run writes a usage row with the operation `schedule`.

## Non-goals

- Research runs, browser runs, and reports. A scheduled run writes a message. Reports arrive with research.
- A waiting run that asks the person a question. The status exists in the table and is not used yet.
- A control for the person to create or edit a schedule directly.
- Sending email or a push notification when a run finishes.
- A staging environment for the queue.

## Anti-goals

- A schedule that silently stops firing.
- A run that writes its result twice.
- A run for one account that can read another account's rows.
- A public address that lets anyone on the internet start a run.

## How a schedule fires

1. **The clock.** Cloud Scheduler calls the worker once a minute. This call is the tick.
2. **The tick.** The worker finds every schedule whose next time has come. For each one, in one database save:
   - If the account is stopped or at its cap, nothing happens. The schedule stays due and is looked at again next minute.
   - If a run of this schedule is still queued or working, this occurrence is skipped: the next time is worked out and stored, and no run is made.
   - Otherwise a run row is made with the status `queued`, and the next time is worked out and stored.
3. **The queue.** For each new run, the worker puts one task on Cloud Tasks. The task carries only the run id.
4. **The run.** Cloud Tasks calls the worker with the run id. The worker marks the run `working`, runs the agent loop with the schedule's task as the instruction, and then, in one database save, writes the result message into the thread, sets `delivered_at`, and marks the run `ready`.
5. **A second delivery.** If Cloud Tasks delivers the same run again, the worker sees `delivered_at` is set and does nothing.

## Working out the next time

The schedule keeps the person's own words (`when_text`) and one stored time (`next_run_at`). There is no recurrence rule in the table.

- When the agent creates or changes a schedule, it gives the next time itself, in the same tool call.
- When a schedule fires, the worker asks the model one small question: given these words, the person's time zone, and the time now, what is the next time, or is there none? The answer is a time or "none".
- "None" stores no next time. The schedule stays listed and does not fire. This is how a one-time schedule ends.
- If that small call fails, the next time is set to none, a line is written in the thread saying the schedule has no next time, and the failure is logged. A schedule never stops without telling the person.
- That small call writes its own usage row.

## The person's time zone

"Every weekday at 9" needs a time zone. The website sends the browser's time zone with each chat message. The system prompt tells the agent the time zone and the time now. The agent writes the zone into the schedule's words, for example "every weekday at 09:00, America/New_York". No column is added for it.

## What a run can do

- A run uses the same agent loop as chat, with the same tools: memory and threads. It has the account's `profile.md`.
- A run cannot create, change, or delete a schedule. Those three tools are left out of a run, so a scheduled task cannot multiply itself.
- A run has the same limit of 8 tool rounds as a chat turn.
- A run is refused before it starts when the account is stopped or at its cap.

## When a run fails

- The first attempt and up to three retries are made by Cloud Tasks, with a growing wait between them.
- Each attempt adds one to `attempt_count` on the run.
- When the fourth attempt fails, the run is marked `dead`, one line is written in the thread ("A scheduled run failed."), and the failure is logged with the run id.
- A run that fails because the account is stopped or capped is marked `cancelled` and is not retried.
- If the worker crashes in the middle of a run, the run stays `working`. A run that has been `working` for more than 30 minutes is treated as failed by the next tick: it is marked `dead` and told in the thread. This is also what frees the schedule to fire again.

## The records

Two new tables, as drawn in `docs/diagrams/schema.html`, and two foreign keys that the picture already shows and that were waiting for the `run` table.

**`schedule`**: `id`, `account_id`, `thread_id`, `task`, `when_text`, `next_run_at` (may be empty), `created_at`, `updated_at`. Unique on (`id`, `account_id`). The thread link is (`thread_id`, `account_id`) and deleting the thread deletes the schedule. An index on `next_run_at` where it is set.

**`run`**: `id`, `account_id`, `thread_id` (cleared when the thread is deleted), `schedule_id` (cleared when the schedule is deleted), `kind`, `status`, `task`, `question`, `work_ms`, `attempt_count`, `delivered_at`, and `created_at`. Unique on (`id`, `account_id`). At most one run that is `queued`, `working`, or `waiting` for one schedule. An index on `status` where it is `queued`.

One difference from the picture: `run` gains `created_at`. The 30 minute rule for a stuck run needs to know when the run started, and no drawn column says it. `started_at` would also do. This needs the person's approval.

**New foreign keys on existing tables:** `message.run_id` and `usage.run_id` point at `run`, and are cleared when the run is deleted, as the picture draws.

## The operations

All three are agent tools, and only in chat.

| Operation | Input | Does |
| --- | --- | --- |
| `list_schedules` | none | Every schedule of the account: id, task, the words for when, the next time, and the thread. |
| `write_schedule` | `schedule_id` (optional), `task`, `when_text`, `next_run_at` | Creates a schedule in the thread of the turn, or replaces the one with that id. |
| `delete_schedule` | `schedule_id` | Deletes one schedule. |

- `next_run_at` must be a time with a zone, and in the future. A time in the past is refused with a sentence.
- `list_schedules` is also offered inside a run, so a task can mention its own schedule. `write_schedule` and `delete_schedule` are not.

## The website

- The profile panel gains a third list, "Schedules": the task, the words for when, the next time in the browser's zone, and the thread name. Read only.
- `GET /schedules` serves it.
- `GET /threads` gains `working`, true while a run is queued or working in that thread. The thread list shows a small mark beside that name.
- While any thread is marked, the thread list is asked for again every 20 seconds. When the open thread stops being marked, its messages are loaded again, so the result appears without a refresh.
- The message request sends the browser's time zone.

## The cloud

All of this is Terraform in `infra/`, applied by the person after reading the plan, as today.

- **A second Cloud Run service, `folio-worker`.** The same container image as the API, started with a different command, so it serves only the two internal routes. It is not public: only the queue's own service account may call it. It has the same database and model secrets as the API, and a request timeout of 30 minutes.
- **A Cloud Tasks queue, `folio-runs`.** At most 4 attempts, with a wait between them that grows from 10 seconds to 5 minutes. At most 3 runs at the same time.
- **A Cloud Scheduler job, `folio-tick`.** Every minute, it calls the worker's tick route with a Google identity token.
- **A service account, `folio-invoker`.** Cloud Scheduler and Cloud Tasks call the worker as this account. It can do one thing: call the worker.
- **The API service account** gains permission to put tasks on the queue. The worker creates the tasks, so the API does not need it yet. It is left out until research needs it.
- **The deploy workflow** deploys the worker with the same image, after the migration job and before the live check.

## Running it on a laptop

There is no Cloud Tasks on a laptop. The worker has one small interface, "put this run on the queue". In the cloud it calls Cloud Tasks. Locally, and in tests, it runs the run directly in the same process. The tick is called by hand or by a test. Nothing else in the code knows which one is in use.

## Questions and answers

**Why does the agent, and not code, work out the next time?**
A person says "every other Friday", "the first working day of the month", or "tomorrow at 9". Turning words like these into a rule is a question of meaning, and the project rule is that the model handles meaning. Code would need a grammar for every way of saying it, and would be wrong at the edges. So code does one simple, exact thing: it fires at a stored time. The model does the part that needs understanding.

**Is it not risky to depend on a model call for the next time?**
It is the main risk in this design, so it is handled in three ways. The call is small and separate from the task, so a long task cannot crowd it out. It happens when the schedule fires, before the task runs, so a failed task does not lose the next time. And if it fails, the person is told in the thread and the failure is logged. The schedule cannot die quietly.

**Why a tick every minute, and not one queued task per schedule set for its time?**
With a tick, the database is the only place that knows what is due. If a task is lost, or the queue is emptied, or a deploy goes wrong, the next tick still finds every due schedule. With one task per schedule, a single lost task ends that schedule for ever with no one knowing. A tick costs one small database read a minute.

**Why a queue at all, if the tick already knows what is due?**
The tick must finish in seconds. A run can take minutes. The queue lets the tick hand the run over and return. It also gives retries with waits, a limit on how many runs happen at once, and a second delivery if a worker dies, without any of that being written by hand.

**What does "exactly one result" depend on?**
The run row. Its id is the id of the queued task. The result message and `delivered_at` are written in the same database save. A second delivery of the same task sees `delivered_at` and stops. The queue is allowed to deliver twice, and the row makes the second one harmless.

**Why a second service, and not routes on the API?**
The API is public. A route that starts work for any account must not be reachable from the internet. As a separate service, the worker is closed by Google's own access control: a call without the invoker's identity never reaches the code. It also lets the worker have a long timeout without giving one to the public API, and it is where the browser will run later.

**Why the same image for both services?**
One build, one version, one set of code that talks to the database. A second image would be a second thing to keep in step, and there is nothing in it yet that the API image lacks.

**Why can a run not create schedules?**
A scheduled task that could schedule more tasks could grow without anyone asking for it, and spend the account's cap overnight. Schedules are made only in a conversation with the person present.

**Why is the next occurrence skipped, and not queued, when the last run is still working?**
That is the product rule. A run that takes longer than the gap between occurrences would otherwise build a queue that never empties. Skipping means the person gets the newest result as soon as there is room for it.

**Why does a capped or stopped account's schedule stay due, and not move on?**
Working out the next time is a model call, and a stopped or capped account may not make one. Leaving the schedule as it is costs one database check a minute. When the cap is raised, it fires once and carries on.

**Why is there no "dead letter queue" service?**
The product rule says a failed run stays on a dead letter queue for developers. Cloud Tasks has no such queue built in. The run row with the status `dead` is that record: it stays in the database with its task and its attempt count, it can be listed, and every dead run is logged with its id. Adding a separate queue service would hold the same information a second time.

**Why does the time zone come from the browser, and not from a setting?**
It is right without the person doing anything, and it is right again when they travel. The zone is written into the schedule's own words when the schedule is made, so the schedule does not change if the person later opens Folio from another zone.

**What does a schedule cost?**
Each occurrence is one small model call for the next time, plus the model calls of the task itself. All of them are usage rows with the operation `schedule`, and all count toward the account's cap. The tick itself calls no model.

## Approaches considered

- **A. Tick, queue, and worker, with the next time from the model (chosen).** Described above.
- **B. One queued task per schedule, set for its time.** No tick. Rejected: a lost task ends a schedule silently, and Cloud Tasks cannot hold a task more than 30 days ahead.
- **C. A recurrence rule in the table, worked out by code.** No model call per occurrence. Rejected: it needs a new column, a grammar for the person's words, and code that decides meaning, which the project rules keep for the model.
- **D. The tick runs the work itself, with no queue.** Fewer parts. Rejected: no retries, no limit on runs at once, and one long run would delay every other schedule.

## Calls made by the designer, not the person

- `run` gains `created_at`. This changes the picture and needs approval.
- A run cannot create, change, or delete a schedule.
- A run stuck in `working` for 30 minutes is marked dead.
- The queue runs at most 3 runs at once.
- The thread list is asked for again every 20 seconds while a run is working.
- The failure line in the thread is "A scheduled run failed."
- A one-time schedule stays listed with no next time after it has run. The agent deletes it when asked.

## Left to the builder

- Function and file names not fixed by the plan, the exact wording of the instruction given to a run, and the look of the mark on a thread name within the existing palette.
