# Decisions

`Goal.md` is the requirement list. This file is what we added. Every line in `Goal.md` still stands.

## Product

- The product is for ongoing personal use. The submission checklist is the floor.
- Chat with memory, research reports, scheduled runs, and files plus Drive are all required.
- The two original features are long-term memory and the scheduled run.
- This version and the later version are listed in `docs/product/mvp-mlp.md`.

## People

- A person on the allowlist creates an account by signing in. The allowlist, sign-in, and sessions are in `docs/product/access.md`.
- Each person has a private account.
- The person changes product records by telling the agent in the chat. The direct controls are in `docs/product/control.md`.
- Day one screens are chat, reports, files, a profile panel, and settings. Detail is in `docs/product/screens.md`.
- Memory, files, Drive, research, and scheduled runs are not shared across accounts.

## Models

- The platform holds the provider keys.
- The current model is Gemini. The person cannot change it.
- The call goes through one internal interface. Detail is in `docs/product/models.md`.
- Each email stops at 10 million recorded total tokens unless that cap is raised. The cap and the stop stay with the email after the account is deleted. The person sees tokens and the file byte count.

## Harness

- The loop is project code in the API and the worker. It is an agent harness: the agent has tools and memory.
- The model chooses the tool, the memory, and the words. Code runs a gate before the tool. The model does not decide that a gate passed.
- OpenClaw, Hermes, and an agent framework are not the core.
- Chat runs in the API request. It reads `profile.md`, the thread window, and files it looks up.
- An ability is a way of working the agent remembers for that account. Detail is in `docs/product/abilities.md`.
- Research runs on the worker and writes one report. Detail is in `docs/product/research.md`.
- A scheduled run uses the same worker. It runs the task the person described and writes a message or a report. Detail is in `docs/product/schedules.md`.
- The worker drives the browser. Detail is in `docs/product/browser.md`.
- A scheduled run does not send email or write to Drive. It may open the web when the task needs it. A send on a website runs only after the person confirms that exact send. Detail is in `docs/product/confirmations.md`.

## Inputs

- Voice is speech turned into a chat message. Detail is in `docs/product/voice.md`.
- A Drive import is read-only and private.
- A Drive import is a copy from that moment. Later Drive edits wait for another import. Detail is in `docs/product/drive.md`.

## Data

- Postgres is the source of truth for the account and everything after sign-in. Reads and writes use the primary. The record rules are in `docs/product/records.md`.
- Firebase Authentication holds the sign-in.
- The queue delivers at least once. A queued run is its own row, and its id is the job id. The report or the message points at that run. A queued run retries a dead provider three times after the first attempt, then the failed run stays on a dead letter queue. Detail is in `docs/product/records.md` and `docs/product/failure.md`.
- A file is visible after the bytes are stored and the database row commits. Upload and task-file bytes are counted per account. The model reads the opened file. A file that fits is sent whole. A file that does not fit is read in successive parts in that same run. This version keeps no embedding index. Detail is in `docs/product/files.md`.
- A report's question, its text, how the run stopped, its source list, and its ready status commit together.
- Memory rows commit with the chat turn that created them.

## Security

- An account requires an allowlisted email.
- Firebase Authentication holds the sign-in. The account stores no password. Session rules are in `docs/product/access.md`.
- Every row and file prefix has an owner.
- Model keys and integration secrets are envelope-encrypted with Cloud KMS.
- Secret Manager holds app secrets only.
- Cloud SQL has a private IP.
- Scheduler and Cloud Tasks call Cloud Run with Google identity tokens.
- Deleting an account is immediate and cannot be restored. It removes the Firebase sign-in, sessions, rows, files, the integration secrets, browser cookies, and schedules. It leaves the usage rows for that email. It does not remove the platform model keys.

## Capacity

- About 25 people, with room for a few hundred.
- One region, `us-central1`. Cloud SQL regional failover is for outage, not for reads.
- A second live region is out of scope.
- If the database primary is lost: minutes of data loss, minutes to fail over.
- While Postgres is unavailable, the app stops and shows that the system is down.

## Hand-in

- Deliverables stay those in `Goal.md`: live URL, report, GitHub.
- Automated deployment is planned once the app runs on this machine.
- The draft report is `docs/hand-in/report.md`.
- The report's omissions section covers only work beyond `Goal.md`.
