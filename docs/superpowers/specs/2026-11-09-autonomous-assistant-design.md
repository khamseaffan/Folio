# Autonomous assistant

Each invited account has an assistant. The person asks for a task. The assistant does the task, asks when a missing answer would change the result, stores that answer, and looks it up on a later task. The assistant begins work without a new message only for a schedule the person told it to run, or to say that a run finished or is waiting.

Chat, deep research, voice, files, Drive import, and scheduled runs remain required. The platform holds the model keys. Each account has a usage record, and the platform can stop an account.

# Habit

A direct request starts the work. The assistant does not ask permission for the task the person just requested.

The assistant asks when a missing answer would change the result. The stored fact is a short declarative form of what the person said. It keeps the substance and drops the rest. The assistant writes it into the fitting knowledge file without asking.

A later gap reuses the stored answer, rewrites it for the new situation, or returns to the person because the situation changed. When reuse is uncertain, the assistant shows a draft and asks whether to keep it or regenerate it.

The system prompt states the habit, the guard, and the persona. It names no task domain and no biography.

# Persona

The voice is steady and plain on the first conversation and on every later one.

The assistant does the requested work. It speaks in short sentences. It asks one question when a missing answer would change the result. It shows a draft when reuse of a stored answer is uncertain. When it reuses or rewrites a stored answer, it says so in a short line.

It does not perform a character, a mood, or a specialty.

The voice stays fixed as the profile grows. Manner and working preferences live in `profile.md` and are included in every model call. Other knowledge files are not.

# Guard

A public page read, a file read, a file write inside the account, and a Drive-copy read run without a confirmation.

A send on a website is the only action that sends data. It runs only for a confirmation of that exact send. The card, the match, and the record are in `docs/product/confirmations.md`.

A task that would cause physical harm stops and asks.

# Loop

Chat, research, and scheduled runs use one loop.

The loop loads `profile.md` and the task, then calls the platform model. Code checks the guard before a tool runs. A missing fact is looked up or asked. Tool results return to the model. The loop repeats until the task is done or it is waiting on the person.

The model decides whether a stored fact still fits. Code decides whether a tool may run.

The reply, any file, and any report commit with the turn that created them. A knowledge file is visible after its bytes are stored and its ownership row commits.

# Tools

The built-in tools are file read, file write, Drive-copy read, public web read, browser read, `browser_submit`, and knowledge search. Knowledge search matches words and opens at most five files. It does not return every file.

The model opens a remembered ability when the task needs it. This build runs the built-in tools. It does not run model-written code.

Knowledge files are specified in `docs/product/profile.md`. Abilities are specified in `docs/product/abilities.md`. Uploads and task files are specified in `docs/product/files.md`. Drive is specified in `docs/product/drive.md`. Research is specified in `docs/product/research.md`. Schedules are specified in `docs/product/schedules.md`. Submit confirmations are specified in `docs/product/confirmations.md`.

# Waiting

In chat, the question is that turn's reply. The task stays open on the thread. The person's answer continues the same task.

On a queued run, the run is marked waiting and the question is left in the app. The run id is unchanged. A second delivery does not ask again and does not run the tool again. The run resumes when the person answers that question.

# Failure

A file is visible after its bytes are stored and its row commits. A report is ready when its text, its source list, and its ready status commit together. A failed attempt leaves the last ready result in place. Cancelling a research run discards the partial text.

If a browser action may already have completed, the run waits and tells the person. It does not repeat that action.

A down provider is retried on a queued run. In chat, the person sees a short failure and can send again. A stopped account is not retried.

Each model call writes the usage record in `docs/product/models.md`. Missing usage is stored as missing. An account stops when its recorded total tokens reach its cap. The default cap is 10 million.

A send on a website without a matching unused confirmation waits and does not send. Committed work stays.

Deleting an account stops an in-flight run and removes the Firebase sign-in, waiting questions, unused confirmations, knowledge files, abilities, other files, the Drive token, browser cookies, and schedules. It does not remove the platform model keys.

# Placement

```mermaid
flowchart LR
  person[Person]
  webapp[Next.js]
  api[Cloud Run API]
  sql[Cloud SQL]
  storage[Cloud Storage]
  tasks[Cloud Tasks]
  scheduler[Cloud Scheduler]
  worker[Cloud Run worker]
  model[Model provider]
  browser[Browser]
  web[Public web]

  person --> webapp
  webapp --> api
  api --> sql
  api --> storage
  api --> tasks
  scheduler --> tasks
  tasks --> worker
  worker --> sql
  worker --> storage
  worker --> model
  worker --> browser
  worker --> web
  api --> model
```

- `webapp` calls `api`.
- A short chat turn runs on `api`. It may read the profile, this account's files, and Drive copies, then return the reply.
- A turn that needs `browser`, a long read of `web`, or research becomes one Cloud Task. The API reply says the work is in progress.
- `worker` runs the same loop for that task and for a scheduled run.
- `browser` runs on `worker`. An account may run more than one browser task. Two tasks may not drive the same signed-in site at the same time.
- `scheduler` enqueues a scheduled run through `tasks`.
- Voice becomes a chat message when the person sends the transcript, then enters the loop. Detail is in `docs/product/voice.md`.
- A Drive import is a read-only copy. The loop reads the copy.
- Platform model keys stay envelope-encrypted with Cloud KMS. A Drive refresh token stays encrypted the same way, and it belongs to one account.

# Tests

- The system prompt states the habit, the guard, and the steady voice, and names no task domain and no biography.
- A direct request creates no permission question.
- A missing fact creates one question. A second delivery of the same run creates no second question.
- `browser_submit` runs only when an unused confirmation for this account matches the site and the fields.
- A confirmation from another account, a missing confirmation, a used confirmation, and a confirmation whose site, field, value, or amount differs do not send.
- A knowledge file is absent until its bytes and its ownership row commit.
- A file is absent until its bytes and its row commit.
- An upload over 50 MB is refused. A file that would make the account's upload and task-file count exceed 5 GB is refused. Knowledge files and Drive copies are outside that count.
- A report is not ready until its text and its source list commit with its ready status.
- A research run opens at most 20 sources and stops after 30 minutes of work. Waiting on the person does not count. Cancelling discards the partial text and leaves the last ready report.
- A provider failure on a queued run retries. A stopped account does not.
- A cost entry records missing usage when the provider returns none. That record adds nothing to the token cap. The default cap is 10 million recorded total tokens, and it can be raised for one account.
- A scheduled run may open a remembered ability. A send on a website from that run still requires the person to confirm that exact send.
- Deleting a thread deletes its schedules. A schedule has no count cap. When a scheduled run is waiting on the person, the next occurrence is skipped.
- Deleting the account removes the Firebase sign-in, the waiting run, unused confirmations, the knowledge files, the abilities, the other files, the Drive token, browser cookies, and the schedules. It does not remove the platform model keys.

# Product rules

Access is `docs/product/access.md`. Who acts is `docs/product/control.md`. Models and usage are `docs/product/models.md`. Threads are `docs/product/conversations.md`. Knowledge files are `docs/product/profile.md`. Abilities are `docs/product/abilities.md`. Uploads and task files are `docs/product/files.md`. Drive is `docs/product/drive.md`. Research is `docs/product/research.md`. Schedules are `docs/product/schedules.md`. The browser is `docs/product/browser.md`. Voice is `docs/product/voice.md`. Submit confirmations are `docs/product/confirmations.md`. Later work is `docs/product/later.md`. The system map is `docs/diagrams/system-map.html`.

# Decision updates

A scheduled run may use the public web and the browser when the task needs them. A send on a website still requires a confirmation of that exact send.

A chat turn may read the profile and this account's files, and may enqueue the worker for browser, web, and research work.

`docs/decisions.md` and `docs/architecture.md` gain these two rules when this spec is accepted.
