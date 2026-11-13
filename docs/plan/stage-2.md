# Stage 2. One chat turn

One thread, one reply, and one usage row. Decisions D1 to D14 and questions Q1 to Q4 are in `docs/plan/decisions.md`. The rules for every unit are in `docs/plan/stage-1.md`.

Stage 2 is true at 2.g. Units 2.h to 2.k finish the chat rules and can follow.

## 2.a Thread and message tables

- True when done: a thread (name defaults to New thread) and messages (role, kind chat, unlimited body) can be written. `thread` and `message` are each unique on `id` and `account_id`, and the child key is `(thread_id, account_id)`. `message.run_id` is a plain nullable column (D9).
- Depends on: 1.c.
- Creates: thread, message. Writes: both.
- Test: a cross-account insert fails at the database. A 1 MB body is stored. Deleting a thread deletes its messages. Deleting the account deletes both.
- Must not include: summary writes, run, schedule or confirmation keys, routes.

## 2.b Model interface

- True: one interface takes a system prompt and blocks, yields text, and ends with usage (input, output, total, or "missing") or an error. A fake serves tests. The Gemini implementation uses the `google-genai` SDK and reads the platform key and model id from settings (D6). The key is never logged. Expected cost comes from a configured price. A missing price or missing usage gives a null cost.
- Depends on: nothing.
- Creates: none. Writes: none.
- Test: one contract suite against the fake. A live Gemini check is manual and skipped without a key.
- Must not include: Google ADK, tools, retries, a queue, KMS (the env key stands in for it), a second provider.

## 2.c Usage table and writer

- True: one row per model call: lowercase email, account id, time, provider, model, tokens, expected cost, `usage_missing`, operation chat, `message_id` when a reply exists. The cap total is a query over `usage.total_tokens` for an email, not a stored object. A row survives thread deletion and account deletion with `account_id` cleared (D8). `run_id` is a plain column (D9).
- Depends on: 1.c, 2.a.
- Creates: usage. Writes: usage.
- Test: missing usage is stored null with `usage_missing` true and adds zero. Account deletion leaves the rows with the email intact and the sum unchanged. Thread deletion keeps `account_id`. A new account for the email sees the old rows in the sum.
- Must not include: a run key, cost display, views.

## 2.d Context window and system prompt

- True: a call gets the system prompt (habit, persona, and the one physical-harm line of the guard, no task domain, no biography), then the newest blocks that fit. The latest 10 person blocks and latest 10 assistant blocks are always included. Older blocks stay stored. The person is never told a message is too long. There is no summary yet (Q2). The size estimate and budget come from D6.
- Depends on: 2.a.
- Creates: none. Writes: none.
- Test: with 30 blocks, the last 10 of each role are present, in order. Older blocks drop first. A very long single block raises no error.
- Must not include: summary writing, profile.md, tools, the send-confirmation text of the guard.

## 2.e Chat turn service

No HTTP.

- True: given a session, a thread and a person message, the gate runs on the email. Stopped gives "A system admin has stopped your account. Contact khamseaffan@gmail.com." A sum at or above the cap gives "You have reached the allowed usage limit." with no figures. Then the person message is stored and the window built. Then the model is called. In one save, the assistant message and its usage row (`message_id` set) are stored. A call that crosses the cap finishes, and the next turn is refused (D13). Overlapping turns are allowed (D13). Several person messages before a reply are several blocks. Provider errors follow D5. If the account is deleted mid-call, no assistant message is stored, and a usage row is written with `account_id` and `message_id` cleared.
- Depends on: 1.e, 1.g, 2.b, 2.c, 2.d.
- Creates: none. Writes: thread, message, usage, session (`last_active_at`).
- Test: against the fake model. One turn gives two messages and one usage row with the account email. A gate refusal stores nothing and calls nothing. Another account's thread id is "not found", for the admin too. Missing usage is stored as missing.
- Must not include: streaming, stop, summary, tools, queue, thread rename or delete.

## 2.f Chat routes

- True: send a message (this creates the thread when none is open, D4), list my threads, read my thread. All are scoped to the session's account.
- Depends on: 2.e.
- Creates: none. Writes: as 2.e.
- Test: another account's thread is "not found" on all three routes. No route reads another account's thread, including for the admin.
- Must not include: rename, delete, streaming.

## 2.g Frontend: chat

- True: thread list, open thread, composer, the reply, and the two limit lines. English. A narrow window works.
- Depends on: 1.i, 2.f.
- Creates: none. Writes: none.
- Test: component tests, and by hand one turn end to end.
- Must not include: streaming, reports, files, profile panel. Stage 2 is true here.

## 2.h Streaming and stop

- True: the reply streams, with a stop button. Stop keeps the partial reply and writes a usage row (missing if no figure). Closing the tab or ending the session does not stop the reply. A finished reply shows after a refresh.
- Depends on: 2.g.
- Creates: none. Writes: message, usage.
- Test: stop mid-stream stores the partial once with one usage row. End the session mid-stream and the reply still completes.
- Must not include: a run table, a queue, resuming a stream, showing a still-running reply after refresh (Q1).

## 2.i Abort on admin stop and on account deletion

- True: when the admin stops the email, or the account is deleted, the current call aborts and the partial reply is kept where the account still exists.
- Depends on: 2.h, 1.h.
- Creates: none. Writes: message, usage.
- Test: stop or delete mid-stream aborts. The usage row is still written. This works on one API instance only (Q4).
- Must not include: cross-instance coordination.

## 2.j Usage views

Backend.

- True: the person sees input, output and total tokens for their email, with no cost (D7). The admin sees one email's total and its split by provider and model, with expected cost.
- Depends on: 1.h, 2.c.
- Creates: none. Writes: none.
- Test: totals include rows from deleted earlier accounts. Missing rows add nothing. A non-admin cannot see cost or another email.
- Must not include: UI.

## 2.k Frontend: token totals and admin page

- True: settings shows the totals. The admin has a page to read one email's usage, raise its cap, and stop it.
- Depends on: 1.j, 2.j.
- Creates: none. Writes: none.
- Test: totals match the API. A non-admin gets no page.
- Must not include: cost shown to non-admins.
