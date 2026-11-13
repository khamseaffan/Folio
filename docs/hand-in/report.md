# Hand-in report

Draft version 2. The site is deployed at https://folio.khamseaffan.com with sign-in and chat working. Research, memory, schedules, files, Drive, voice and monitoring are not built yet, and the test run on the finished site is filled in when they are.

## Service overview

Folio is a private assistant for one person.

**Conversation**

- You talk in threads.
- A reply appears as it is written, and you can stop it.
- A message has no length limit.
- Older messages stay saved. The model sees a short summary of them. You do not.

**Model**

- I select the provider and the model in settings. The starting model is `gpt-6.1-sol`. I pay for it.
- You do not pick another model, and you do not paste in a key. The tools, the memory, and the loop stay the same when I change the model.

**Who can join**

- The site opens on one public page. You sign in from that page.
- My email is the first account.
- An account is `khamseaffan@gmail.com` or `@sakana.ai`. Any other email is refused at sign-in.
- Sign-in is Google, or a magic link that works once and lasts six hours.
- You can end your session. A third sign-in ends the oldest one.
- Deleting an account is immediate, and the account cannot be restored. The token rows for that email stay, so a new account does not start from zero.

**Size**

- About 25 people, with room for a few hundred, in one region.

**Why this shape**

The assistant is code written for this project, in the API and in a background worker. The assignment asks for an original agent, and a service this small should be readable from the message to the reply.

## Deep research

A research report is separate from a chat reply. You can leave the page. The finished report is there to open and download.

**How a run starts**

- You ask for it in the thread.
- Or a schedule you already set writes one.

**What it may read**

- Your files.
- Google Drive files you have already imported.
- Public pages.

**When it stops**

- After 20 opened sources, after 30 minutes of work, or when new pages mostly repeat the ones already opened.
- A page that cannot be opened does not count. A page that disagrees is kept.
- If it needs an answer from you, the clock pauses and the question stays in the thread.
- Nothing is sent out of the account while it waits.

**What you get**

- One report per run. You can keep several.
- The file contains the question, the writing, a few lines on how the run stopped, and the sources. Each source has a title, an address or a file name, and one line on what it was used for.
- Until then, the screen shows that it is still working, or shows the question.
- You download the file it produced: Markdown, PDF, or another file the task needed.
- If you cancel, the unfinished writing is discarded. The last finished report stays.
- Research only reads. A send on a website still needs you to confirm that exact action.

**Why**

Reading the web and writing a long piece should continue after you leave. The limits give the run an ending. The source list is saved with the text so a finished report can be checked.

## Technology and architecture

You use a website. Longer work runs in the background.

| Piece | Choice |
| --- | --- |
| Website | Next.js on Vercel |
| API and worker | Python 3.13 on Cloud Run |
| Account record | PostgreSQL 18 on Cloud SQL |
| Files | Cloud Storage, one folder per person |
| Sign-in | Firebase, sign-in only |
| Queue | Cloud Tasks and Cloud Scheduler |
| Keys | Cloud KMS for model keys, integration secrets, and browser cookies |
| App secrets | Secret Manager |
| Region | `us-central1`, described in Terraform |
| Checks | GitHub Actions |

**How a request moves**

- A short chat is answered on the API request.
- Research and a scheduled run go on a queue. A worker picks them up.
- The worker calls the selected model, reads public pages, and can drive Chrome.
- Google Drive is the first integration. It is one file you pick. Folio keeps a private copy from that moment. A later integration is another row, with its own secret and tools.
- If that provider is down, the job tries again three times, then stops. You see one line in the thread. I can inspect the failed job.

**Why**

The assignment asks for a React app and for Google Cloud. The website sits on Vercel. The records and the jobs stay in one region, and the database is on a private IP. Chat stays on the request so a reply feels immediate. Research uses a queue because it can take half an hour.

## Additional features

The assignment asks for two features of my own.

**1. Memory**

- Notes in English, kept as Markdown.
- Your profile, how you like to work, is read on every call.
- Other notes are found by their words. One call opens at most five.
- The assistant writes a short fact into the note where it belongs.
- You read them in a panel beside the chat, and you change them by saying so.

**2. Work while you are away**

- You say when, in the chat. There is no form.
- The assistant creates the schedule, changes it, and removes it.
- It belongs to that thread. Deleting the thread deletes the schedule.
- The run can use your files, imported Drive copies, the web, and a way of working it has learned.
- The result is a message in the thread, or a report.
- If that same task is still running or still waiting for you, the next one is skipped.
- A different task can run at the same time.

**Beyond the assignment**

- A remembered way of working, taught in the chat, removed when you say so.
- Chrome on the server. Reading a page does not ask. A form, a post, or a payment shows a card and sends only if you confirm it. The card lasts 24 hours.
- A file written when the result of the task is a file, which you can download.
- Token totals and file space in settings. The email stops at 10 million tokens unless I raise it. Deleting the account leaves that total, the raised cap, and a stop. Files stop at 5 GB. One upload stops at 50 MB.
- The site opens on one public page. After sign-in: chat, reports, files, a profile panel, and settings.

**Why these extras**

Some tasks finish on a website, and that send should be a specific yes. The caps give a small service a limit I can see and raise. Ways of working stay in the conversation, which is where you already explain the job.

## Omissions

Later, and not required by the assignment. One email stays one private account.

- Several named assistants on one account.
- The assistant naming a new thread by itself.
- Search across old threads, and editing a message you already sent.
- The model writing new code and running it.
- Repeating a form, with code deciding it is the same form, and with an expiry.
- Connections other than Google Drive. A later one is another row: a connection, a secret, and tools.
- A full export of the account before deletion.
- Grouping accounts into an organization.

**Narrower than the assignment might sound**

- Drive is one file, imported as a private copy. Folio does not write back to Drive.
- Voice is dictation. The words land in the composer, you send them as a chat message, and the recording is discarded. The assistant does not speak.

## Quality assurance

A change reaches the main branch through a pull request. GitHub Actions runs Ruff, pytest, lint, and typecheck. The same checks run before a commit. The main branch is protected: a change needs the backend, frontend, Terraform and container checks to pass. A merge to main deploys by itself. Vercel builds the website, and a workflow builds the API image tagged with the commit, runs the database migration, deploys Cloud Run, and checks that the live site answers. Only the newest commit on main is deployed. The workflow signs in to Google with a short-lived identity tied to this repository, so no key is stored. Terraform describes Google Cloud and is applied by me after I read the plan, so the pipeline never holds infrastructure-admin power.

**Release environments**

- Development is a branch and a pull request, checked by CI.
- Staging will be what a merge to the main branch produces.
- Production will happen only when a release is made on GitHub.
- Today there is one pipeline. A merge to the main branch deploys straight to the one live environment. The split into staging and production is a planned step and is not built.
- Every API image carries a version tag (`v0.1.` and the deploy number) as well as the commit it was built from, so a release can later point at an exact image.

**Checks I will not leave to the model**

- A website send matches the site, the fields, and a card inside 24 hours.
- A file over 50 MB, or one that would pass 5 GB, is refused.
- Research stops at 20 opened sources and at 30 minutes. The model stops when new pages mostly repeat, and the file says so.
- The same job delivered twice does not create two reports.
- A report is ready only when the question, the writing, how the run stopped, and the source list are saved together.
- A schedule does not start a second copy of itself while the first is still going.
- Deleting a thread deletes its schedules.
- Deleting an account deletes that person's threads, files, and reports, and leaves the usage rows and the platform model keys.
- A call with no usage figure is stored as missing, not as zero.

**Known trade-offs of this deployment**

- The first request after a quiet period is slow, because the API runs no idle instance and Cloud Run has to start one. I did not keep one warm because that costs money all month for 25 people. With real users I would keep one instance warm.
- The database is one zone with no failover, for the same cost reason.

**Before the URL is submitted**

Sign in, hold a conversation, ask for a research report, set a schedule, upload a file, and import one Drive file, on the deployed site.

## Monitoring and logging

Design for the running service. Not switched on yet.

**Logs**

- API and worker logs go to Cloud Logging.
- A line names the account, the run, and the operation.
- It leaves out the conversation, the transcript, file contents, the prompt, keys, integration secrets, cookies, and magic links.

**Alerts**

- API errors, worker failures, a run that has given up after three tries, and the database being down.
- You see one line in the thread when a run fails, and a notice if the database is down.
- The list of failed runs is for me.

**Usage**

- One row per model call: when, which model, token counts, and the expected cost.
- That is how I watch the bill and raise one account's limit.
- No separate analytics product. The request log, the job, and the token row are enough.

## Security

**Getting in**

- The email has to be allowed. Sign-in from the public page creates the account.
- No password. Google, or a one-time link, proves the address.
- A session ends after 30 minutes if you and the assistant are both quiet.
- At most two sessions.

**What I can see**

- Usage, the token limit, and a stop button.
- Not another person's threads, files, or notes.
- A stop says a system admin stopped the account, and shows my email.

**Where secrets live**

- Model keys and integration secrets are encrypted with Cloud KMS.
- Browser cookies are encrypted the same way and deleted with the account.
- The database is on a private IP inside a VPC. Only the API reaches it, over Cloud Run's direct VPC connection, with TLS and a generated password held in Secret Manager. The website never touches the database.
- I did not buy a static outbound address or Cloud NAT, because nothing outside Google Cloud needs to be allowlisted.

**What can leave the account**

- A send on a website, and only for the card you confirmed, for that site and those fields, within 24 hours.
- Reading a page, reading a file, saving a file in the account, and reading a Drive copy do not ask.
- A task that would cause physical harm stops and asks you first.

**Why**

Sign-in stays in Firebase. The notes stay in Postgres. A yes on one form is not a yes for the next form.

## Appendix

- Settings shows tokens, not a price. The expected cost is stored so I can compare it with the bill.
- Postgres holds the account. This version has no separate cache.
- The record picture is drawn. Each table is created when the work that writes it is built. No table exists yet.
