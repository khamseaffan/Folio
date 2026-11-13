# Shadow hand-in

A copy of the hand-in. The last section is a note on reliability, consistency, availability, scale, and security. The note does not change the design.

# Hand-in report

Draft version 1. The design is recorded. The site is not deployed, and there is no live URL. The URL, the test run on that site, and switching on monitoring are filled in when the app is running.

## Service overview

Folio is a private assistant for one person.

**Conversation**

- You talk in threads.
- A reply appears as it is written, and you can stop it.
- A message has no length limit.
- Older messages stay saved. The model sees a short summary of them. You do not.

**Model**

- One model, `gemini-4-argon`. Thinking level is medium. I pay for it.
- You do not pick another model, and you do not paste in a key.

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
- The worker calls Gemini, reads public pages, and can drive Chrome.
- Google Drive is the first integration. It is one file you pick. Folio keeps a private copy from that moment. A later integration is another row, with its own secret and tools.
- If Gemini is down, the job tries again three times, then stops. You see one line in the thread. I can inspect the failed job.

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

A change reaches the main branch through a pull request. GitHub Actions runs Ruff, pytest, lint, and typecheck. The same checks run before a commit. The main branch is protected. The first build on this machine is an account and one chat turn. The deployment pipeline comes after that chat turn and before files. The website stays on Vercel. Terraform describes Google Cloud.

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
- The database is on a private IP.
- The scheduler calls the worker as Google, not with a shared password.

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

## How it holds up

Five questions for a service of about 25 people, with room for a few hundred. The care sits on a lost report, a token counted twice, or a form sent twice. A new machine, a second queue, or a second region waits until a real limit shows up.

**Reliability. A finished job stays finished.**

Already in the design:

- Chat is answered on the request. Research and a scheduled run use the queue.
- The same job delivered twice writes one result.
- A dead model is tried three more times. The person then sees one line. A stopped email is not tried again.
- A report is saved as one set: the question, the writing, how it stopped, and the sources.
- If the database is down, the app stops and takes no new work.

Worth a look when that part is built:

- A database outage is a wait. It is not one of the three tries kept for a dead model.
- The queue hears that the job is done only after that one result is saved. The same model call is one usage row, even if the job arrives again.
- The 30 minute stop still leaves time to save the report.
- A schedule moves its next time in the same save that starts the run. A second delivery of that same fire does nothing.

**Consistency. Two records of the same thing agree.**

Already in the design:

- A child row names the same account as its parent.
- Deleting the account leaves the usage rows, the cap, and the stop on the email.
- A file appears after its bytes are stored and its row is saved. An unfinished upload is dropped after a day.
- A third sign-in ends the oldest session in that same save.
- The same schedule does not start a second copy while one is still going.

Worth a look when that part is built:

- The report file is stored first. The ready row is saved after that. The app treats the row as the finished report.
- The usage row is saved with the reply. A missing figure is stored as missing in that same save.
- A worker that finds the run cancelled, already finished, or gone writes nothing.
- The account is held until a pending upload is counted, so two uploads cannot both slip past 5 GB.
- The next sign-in creates the account row when the sign-in exists and the row does not. Deleting the account removes the sign-in again if it is still there.

**Availability. The product is down, or one job failed.**

Already in the design:

- Database down is a notice for everyone, and the app waits.
- One failed run is one line in that thread.
- One region. If the database is lost, a few minutes of records can be lost, then it comes back in that same region.
- A research run inside its 30 minutes is work in progress. The person can leave the page.

Worth a look when the app is running:

- The website, the API, or sign-in being down is the same kind of notice as the database. Saved reports and files still open when the database is up and only the model is down.
- The four alerts named above turn on then. Who receives them, and at what count, stays unset.

**Scale. The limits already match the crowd.**

Already in the design:

- One queue for the slow work.
- Research stops at 20 opened sources or 30 minutes.
- One upload stops at 50 MB. Uploads and task files stop at 5 GB.
- An email stops at 10 million tokens unless that cap is raised.
- A reply opens at most five notes. A large file is read in parts.
- At most two sessions. Only an allowed email can join.

Worth a look when that part is built:

- A long run lets go of the database while it reads the web, and saves at the end.
- If the model asks the service to slow down, that wait uses the tries already planned. A second key waits until those errors are real.
- A cap on how many browser jobs run at once waits until those jobs actually pile up.
- A size rule for Drive copies waits until a large import is a real problem. Those copies sit outside the 5 GB on purpose.

**Security. One person's files and sends stay theirs.**

Already in the design:

- An allowed email, Google or a one-time link, no password, and two sessions.
- Usage and a stop are visible. Another person's threads, files, and notes are not.
- Code checks identity, the account, and the send card before a tool runs. The model does not pass that check.
- Secrets and cookies are encrypted. Logs leave out the conversation, the prompt, keys, cookies, and magic links.
- A send matches the site, the fields, and a card inside 24 hours.

Worth a look when that part is built:

- A secret, a cookie, or a Drive token stays in the worker. It does not enter the prompt, the thread, or a tool result.
- The Drive connection fetches the one chosen file.
- A download checks this session. The file stays private. The link lasts minutes.
- The worker reads the account from the saved run.
- A web read opens a public page.
- A failed job keeps the run id, the account, and one error line.
- The send card is marked used before the request leaves, so a retry cannot send it again.
- Each browser run uses a fresh browser for that account, then removes it. The task using a site writes that site's cookies at the end.
- A card stays with the account, the site, and the fields. Whether logout cancels an unused card is a question for when that send is built.

**Left as it is**

- Chat stays on the request. The person can send it again.
- A crashed research run starts over. The report is written once, at the end.
- Two runs may write in one thread. Two calls that both started under the cap may both finish. The next call reads the new total.
- A deleted account stays deleted.
- Reading a page, a file, or a Drive copy does not ask for a card.
- There is one database, one queue for slow work, and one region.

**Why**

A service this size fails in a few familiar ways. Those ways already have a rule, or they get one when that part is built. The rest is a solution waiting for a problem.
