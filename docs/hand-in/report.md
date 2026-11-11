# Hand-in report

Draft version 1. The application is not deployed, and the live URL is not set. This draft describes the design.

## Service overview

Sakana is a personal AI agent for one person at a time. An invited person signs in, talks with the agent, and keeps a private memory, files, research reports, and scheduled runs. The platform holds the model key. The person does not bring a key and cannot change the model. The current model is Gemini.

Chat is a thread. A reply streams. The person can stop it, and the partial reply is kept. A message has no length limit. One call sends the system prompt, `profile.md`, the thread summary when one exists, and as many of the newest message blocks as fit, including the latest 10 person blocks and the latest 10 assistant blocks. Blocks that do not fit are folded into the summary before the call. The person cannot see or edit the summary.

An account is created for `khamseaffan@gmail.com` or an address at `sakana.ai`. An invite may name any email, and any other email is refused when the invite is sent. Sign-in is Firebase Authentication with Google or a one-time magic link. The person can end a session. Deleting an account is immediate and cannot be restored.

The service is for about 25 people, with room for a few hundred, in one region.

I kept the agent original. The loop is project code in the API and the worker. OpenClaw, Hermes, and an agent framework are not the core. A small personal service stays understandable when the loop is code I can read, and the goal asks for an original agent.

### Deep research

Research is a required part of this version, and it is separate from chat.

A research run starts when the person asks for a report in the thread, or when a schedule they told the agent to run writes a report. The run is a queued job on the worker. Its id is its own row. The report points at that run, so a second delivery of the same job cannot write a second report.

The run may read the account's files, imported Drive copies, and the public web. It opens at most 20 sources, then writes the report from those sources. It works for at most 30 minutes, then writes the report from what it has. Time spent waiting on the person does not count. Each run writes one report. One account can keep several.

A ready report keeps the list of pages and account files it read. That list commits with the text and the ready status. The person sees the list when the report is ready. Until then the app shows that the run is in progress, or the question it is waiting on. When an answer is missing, the run waits and the question stays in the app. Nothing is sent outside the account.

Research reads. A send on a website still requires the person to confirm that exact send. The person tells the agent to name, rename, or delete a report. Deleting a report leaves other files, threads, and Drive copies. Cancelling a run discards the partial text. The last ready report stays when one already exists.

The person downloads the file that run wrote. That file is Markdown, PDF, or another type the task needs.

I separated research from chat because a long read of the web should not sit inside one request. The 20 sources and the 30 minutes give the run an end, and the source list is committed with the text so a ready report can be checked against what it read.

## Technology and architecture

The web app is Next.js on Vercel. The API and the worker are Python FastAPI on Cloud Run. Postgres on Cloud SQL is the record after sign-in. Cloud Storage holds file bytes. Firebase Authentication holds sign-in. Cloud Tasks and Cloud Scheduler run the queue. Cloud KMS encrypts the model keys, the Drive refresh token, and the browser cookies. Secret Manager holds app secrets. Terraform manages the Google Cloud resources in `us-central1`. GitHub Actions runs the checks.

A short chat returns on the API request. Research and a scheduled run go through the queue. The worker calls the model, and it may read the public web or drive Chrome when the task needs it. A file prefix belongs to one account.

A queued run retries a dead provider three times after the first attempt. The failed run then stays on a dead letter queue. The person sees one line in the thread that the run failed.

I put the web app on Vercel and the data and jobs on Google Cloud because the goal requires Google Cloud and a React frontend, and the database stays in one region with a private IP. Chat stays on the request so a short reply does not wait for a job. Research and schedules use the queue because they outlive the page.

## Additional features

The goal requires two original features. This version has long-term memory and the scheduled run.

Memory is a set of English Markdown files for one account. `profile.md` holds manner and working preferences and is included in every model call. Other files are opened by a word search, at most five files, and `profile.md` is extra. The agent writes a short fact into the fitting file. The person reads the files in the profile panel and tells the agent to change or delete them.

A schedule exists because the person said in the chat when the work should happen. The agent creates, updates, and deletes it. The schedule belongs to that thread, and deleting the thread deletes the schedule. The run does the described task. It uses files, imported Drive copies, the web, and a remembered ability when the task needs them. It writes a message in the thread or a report. When a run of that same schedule is still working or waiting, the next occurrence is skipped. A different schedule runs at the same time.

These parts are beyond the goal:

- The agent remembers abilities and uses one when a task needs it. A send from an ability still needs the person to confirm that exact send.
- Chrome on the worker reads pages. A send on a website runs only after the person confirms that card. The card shows the site, the field names, the values, and whether money or personal data is in the fields. The card expires 24 hours after it appears.
- The assistant writes a file when the task needs that file, and the person can download it.
- The person sees token totals and the file byte count. The account stops at 10 million recorded tokens unless that cap is raised. Uploads and task files stop at 5 GB. One upload is at most 50 MB.
- Day one has chat, reports, files, a profile panel, and settings.

I added the browser because some tasks finish on a website, and I kept the send behind a card for that exact action. I added the token cap and the file cap so a small service has a stop I can raise per account. Abilities stay with the agent so the person teaches the work in chat instead of filling a settings form.

## Omissions

This section covers work beyond the goal. The policy is to add each one in a later version without changing the rule that one email is one private account.

- Several named bots on one account. The account stays one account.
- The assistant names a new thread. This version names a new thread "New thread" until the person tells the agent to rename it.
- Search across threads, and editing an earlier message.
- The model writes new code and runs it in a sandbox. This version runs built-in tools only.
- A repeated form, when code matches the site, the form, the allowed fields, and an expiry. The model does not decide that two forms are the same form.
- Another integration, as a connection on the account, a secret in Cloud KMS, and one or more tools. This version's integration is Google Drive.
- An export of an account's data. Deletion does not wait for an export.
- Accounts grouped in an organization. This version's rows carry the account id, and nothing is shared across accounts.

Drive is a read-only import of one file. A later edit in Drive waits for another import. This version does not write back to Drive. A spoken conversation is not in this version. Speech is transcribed, the transcript becomes a chat message when the person sends it, and the audio is discarded.

## Quality assurance

GitHub Actions runs the checks on a pull request. The API checks are Ruff and pytest. The web checks are lint and typecheck. The same checks run in the local commit hook. `main` is protected, and a change lands through a pull request.

The tests that guard the design are:

- A send runs only when an unused confirmation for this account matches the site and the fields, and an expired card does not send.
- An upload over 50 MB is refused, and a file that would pass 5 GB is refused.
- A research run opens at most 20 sources and stops after 30 minutes of work. Waiting on the person does not count.
- A second delivery of the same run does not write a second result.
- A report is ready only when its text, its source list, and its ready status commit together.
- When a run of the same schedule is still working or waiting, the next occurrence is skipped.
- Deleting a thread deletes its schedules. Deleting an account removes that account's rows and does not remove the platform model keys.
- A model call with no usage is stored as missing, and that record adds nothing to the token cap.

Before the live URL is submitted, I will walk through sign-in, a chat, a research report, a schedule, a file upload, and a Drive import on the deployed app.

I put the dangerous checks in code rather than in the model's judgment. The model can propose a send. The code decides whether that card is unused, unexpired, and an exact match.

## Monitoring and logging

This is the draft design for the running service.

API and worker logs go to Cloud Logging. A log line carries the account id, the run id, and the operation. It does not carry the message text, the transcript, a file's bytes, a prompt, a model key, a Drive token, a browser cookie, or a magic link.

Cloud Monitoring alerts when the API returns errors, when the worker fails, when the dead letter queue receives a run, and when Cloud SQL is unavailable. The person already sees one line in the thread when a run fails, and a popup when Postgres is down. The dead letter queue is for me to inspect the failed run. The person does not see that queue.

Usage is a row per model call: account, time, provider, model, input tokens, output tokens, total tokens, and expected cost. Missing usage is stored as missing. I can read the total per account and raise that account's token cap.

I kept a third-party product analytics tool out of this version. The service is small, and the logs I need are the request, the job, and the token row. Masking the prompt and the file bytes keeps a debug log from becoming a copy of the person's private account.

## Security

An account requires an invite, and the email must be on the allowlist. The account stores no password. Firebase Authentication holds the sign-in. A magic link works once and expires after 24 hours. A session ends after 30 minutes of silence from both the person and the assistant. An account has at most two sessions.

Every row and file prefix has an owner. The super-admin account can read usage, raise a token cap, and stop an account. It cannot read another account's threads, files, or knowledge. A stop by that account tells the person to contact `khamseaffan@gmail.com`.

Model keys and Drive refresh tokens are envelope-encrypted with Cloud KMS. Browser cookies are encrypted the same way and deleted with the account. Secret Manager holds app secrets only. Cloud SQL has a private IP. Cloud Scheduler and Cloud Tasks call Cloud Run with Google identity.

A send on a website is the only action that sends data. Code sends only for an unused, unexpired confirmation that matches the site and the fields. A page read, a file read, a file write inside the account, and a Drive-copy read need no confirmation. A task that would cause physical harm stops and asks.

Deleting an account removes the Firebase sign-in, the sessions, the rows, the files, the Drive token, the browser cookies, and the schedules. It does not remove the platform model keys.

I separated sign-in from the product database so a session provider is not the record of the person's work. I bound a send to one card so a later, similar form is not treated as the same yes.

## Appendix

The person sees tokens, not the expected cost. The expected cost stays on the usage row so I can check the bill without putting a dollar figure in the product.

Postgres holds the account and the product rows. A fast store is not part of this version.

The record picture, the tables, and the indexes come before any table is created. This draft does not include that picture.
