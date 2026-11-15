# Plan decisions

The plan for stage 1 and stage 2 is in `docs/plan/stage-1.md` and `docs/plan/stage-2.md`. Stage 2.5 (CI/CD), stages 3 to 5, files, memory, research, schedules, Drive, the browser, and voice are not in it.

## Sequence

One builder, in this order.

1. Build 1.a, 1.b, 1.c, 1.d, 1.e, 1.f, 1.g, 1.i0, 1.i. Stage 1 is true.
2. Build 2.a, 2.b, 2.c, 2.d, 2.e, 2.f, 2.g. Stage 2 is true.
3. These can wait until then: 1.h, 1.j, 2.h, 2.i, 2.j, 2.k. Build 1.h and 1.j before the two stages are signed off, because the admin and delete-account rules live there.

## Decisions the person must accept

None of these is chosen yet.

- D1. Local sign-in. The person types an email, the API checks the allowlist, and signs them in with no inbox proof. `firebase_uid` is `local:{email}`, as the schema says. It stands in for Firebase's verified email. The one-time and six-hour magic link rules cannot be tested until Firebase is added. Alternative: the Firebase Auth Emulator, which is more faithful but needs extra tooling and gives different uids.
- D2. The database major is PostgreSQL 18. How it runs on this machine, and using real Postgres in tests, are still open. Postgres 16 is not used.
- D3. The picture has no token column, so the session id (a random uuid) is the cookie value. Anyone who can read the session table can use a live session. A hashed token would need a new column.
- D4. A thread is created when the person sends a message with no thread open.
- D5. If Gemini fails in a chat turn, the person gets an error, the person message stays, no assistant message is stored, and a usage row marked missing is written if the call was sent.
- D6. The person supplies the Gemini model id, the price per token, the context budget, and how blocks are measured. The plan invents none of them. The env key stands in for Cloud KMS.
- D7. Settings totals are per email, because that is what the cap sums. `docs/product/models.md` says "that account's".
- D8. Every composite "set null" key must clear only its own id column, not `account_id`. This is a Postgres 15 and later column list. Otherwise deleting a thread would clear `usage.account_id`. The picture says only "set null", so this is a picture change.
- D9. `message.run_id` and `usage.run_id` exist without a key until a run table exists. Adding the keys later is a change to those tables and needs approval then.
- D10. The existing `ci.yml` gains a Postgres service. Nothing else in CI changes.
- D11. The browser calls the API through the Next.js server, so the cookie is same-origin. The sign-in cookie is httpOnly with SameSite=Lax, and state-changing requests are checked against the web app's origin.
- D12. Chat is refused until the display name is set. This is a reading of "required".
- D13. Two chat turns may overlap. The cap is checked at the start of each turn. A call that crosses the cap finishes, and the next turn is refused.
- D14. SQLAlchemy 2, Alembic, and psycopg 3 for the database. Vitest and Testing Library for the frontend.
- D15. A thread name is trimmed and is 1 to 80 characters. The check is in the operation. The table has no check.
- D16. A chat turn runs at most 8 rounds of tool calls. The reply then says "I stopped after 8 tool steps."
- D17. An action on a record is one operation in `backend/app/operations.py`. A route and the agent both call it. Account actions are routes only.
- D18. A request runs a table's CREATE statements only when that table is missing. The migration job always runs them. CREATE statements inside a request took table locks that blocked other writers.
- D19. A session ends after 24 hours of silence, not 30 minutes.
- D20. A POST, PUT, PATCH, or DELETE that names an origin is refused with 403 unless the origin is the configured website, the local website, or the host the request was sent to. This is checked before any route runs. It adds to D11.
- D21. A failed chat turn, a failed save, a refused tool call, and a request refused by origin are written to the log. The person sees only the fixed sentence.
- D22. A thread name with a null character is refused with the name sentence.
- D23. A stopped model call is stored with token counts that Folio counted itself, marked `estimated`. It counts toward the cap. A failed call stays missing. Before, a stopped call was missing and added nothing to the cap.
- D24. A knowledge file name and an ability name are trimmed and are 1 to 80 characters. The check is in the operation. The table has no check.
- D25. An ability is addressed by its id, because its name is not unique.
- D26. `knowledge_file` has no `storage_key`. The row holds the text, and no Cloud Storage copy exists.
- D27. `profile.md` holds at most 20,000 characters. A write, append, or edit that would pass it is refused and the agent is told. It is sent on every call, so an unlimited file could outgrow the model's context and stop every turn.

## Schedules

- D28. `run` has a `created_at` column that the picture did not show. The rule for a stuck run needs to know when the run started.
- D29. A run cannot create, change, or delete a schedule. Those three tools are left out of a run.
- D30. A run that has been `working` for more than 90 minutes since its latest attempt began is marked dead by the next tick. A run that never left the queue is marked dead after 30 minutes from `created_at`. A queued run older than 2 minutes is put on the queue again.
- D31. The queue runs at most 3 runs at once.
- D32. The thread list is asked for again every 20 seconds while a run is working.
- D33. The line written in the thread for a dead run is "A scheduled run failed."
- D34. A one-time schedule stays listed with no next time after it has run. The agent deletes it when asked.
- D35. `message.run_id` and `usage.run_id` now have keys to `run`, as the picture draws. They clear when the run is deleted.
- D36. The worker is a second Cloud Run service made from the same image. It serves only the tick and the run routes, and only the queue's service account may call it.
- D37. `run` has a `started_at` column. It is set each time an attempt begins. Time spent waiting in the queue is not counted against a working run.

## Research

- D38. The browser is Playwright with Chromium, in a new empty context for each page. Search is Tavily, behind one small interface.
- D39. Research runs in a second Cloud Run service with its own queue and image. A research run is a `run` of kind `research`, and the same `execute` runs it with a switch on the kind.
- D40. A page becomes a digest of about 300 words, made by a separate model call with no tools. The research loop never sees the page text, and the text is not stored.
- D41. The loop can open only an address that a search result or an opened page showed it.
- D42. Limits that the product docs do not give: 25 searches in an attempt, 60 steps, 8 results for a search, 30 links for a page, 40,000 characters read from a page, 30 seconds to load a page, 12,000 characters of a knowledge file, a report of at most 100,000 characters, and a report name of 1 to 80 characters.
- D43. Reading stops at 25 minutes of the 30. The time of each attempt adds up across attempts. A retry with less than 3 minutes left is not started.
- D44. The token cap does not throw work away: a run that reaches it writes one last report. The admin's stop cancels the run.
- D45. A run with no source is `failed`, writes no report, and is not tried again.
- D46. A cancelled run writes no report. A run cancelled because the admin stopped the account puts one line in the thread. A run cancelled because the thread was deleted says nothing.
- D47. The loop, the digests, and the report use the one model the admin chose.
- D48. A search is a usage row with the price of one search. It does not count toward the token cap.
- D49. `report` has no `storage_key`, and `report_source` has no `file_id` yet.
- D50. A run for an account that is stopped or at its limit when it starts shows the matching line in the thread, so the person is not left waiting.

## Open questions

The schema picture lacks an answer to each.

- Q1. Showing a reply that is still running after a refresh or in another browser has no place in the picture. It needs a status on message or another place. Not added.
- Q2. Folding old blocks into the thread summary is a model call. The usage operation list has no value for it. Stage 2 leaves it out, and older blocks drop from the call.
- Q3. The docs do not say whether the admin can undo a stop.
- Q4. Aborting a call from another request works on one API instance only. Cloud Run with several instances needs shared state that the picture lacks.
