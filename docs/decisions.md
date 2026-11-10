# Decisions

`Goal.md` is the requirement list. This file is what we added. Every line in `Goal.md` still stands.

## Product

- The product is for ongoing personal use. The submission checklist is the floor.
- Chat with memory, research reports, scheduled runs, and files plus Drive are all required.
- The two original features are long-term memory and the scheduled run.

## People

- Access is invite-only. The allowlist, sign-in, and sessions are in `docs/product/access.md`.
- Each invited person has a private account.
- Memory, files, Drive, research, and scheduled runs are not shared across accounts.

## Models

- The platform holds the provider keys.
- The current model is Gemini. The person cannot change it.
- The call goes through one internal interface. Detail is in `docs/product/models.md`.

## Harness

- The loop is project code in the API and the worker.
- OpenClaw, Hermes, and an agent framework are not the core.
- Chat runs in the API request. It reads `profile.md`, the thread window, and files it looks up.
- An ability is a way of working the agent remembers for that account. Detail is in `docs/product/abilities.md`.
- Research runs on the worker. It may read that person's files and the public web, then writes one report.
- A scheduled run uses the same worker. It may read memory and files, then writes a message or a report.
- A scheduled run does not send email or write to Drive. It may open the web when the task needs it. A browser submit runs only for a confirmation of that exact submit. Detail is in `docs/product/confirmations.md`.

## Inputs

- Voice is speech turned into a chat message.
- A Drive import is read-only and private.
- A Drive import is a copy from that moment. Later Drive edits wait for another import. Detail is in `docs/product/drive.md`.

## Data

- Postgres is the source of truth for the account and everything after sign-in. Reads and writes use the primary.
- Firebase Authentication holds the sign-in.
- The queue delivers at least once. The job id is the identity of the report or message.
- A file is visible after the bytes are stored and the database row commits. Upload and task-file bytes are counted per account. Detail is in `docs/product/files.md`.
- A report's text and its ready status commit together.
- Memory rows commit with the chat turn that created them.

## Security

- An account requires an invite.
- Firebase Authentication holds the sign-in. The account stores no password. Session rules are in `docs/product/access.md`.
- Every row and file prefix has an owner.
- Model keys and Drive refresh tokens are envelope-encrypted with Cloud KMS.
- Secret Manager holds app secrets only.
- Cloud SQL has a private IP.
- Scheduler and Cloud Tasks call Cloud Run with Google identity tokens.
- Deleting an account removes the Firebase sign-in, sessions, rows, files, keys, and schedules.

## Capacity

- About 25 people, with room for a few hundred.
- One region, `us-central1`. Cloud SQL regional failover is for outage, not for reads.
- A second live region is out of scope.
- If the database primary is lost: minutes of data loss, minutes to fail over.

## Hand-in

- Deliverables stay those in `Goal.md`: live URL, report, GitHub.
- The report's omissions section covers only work beyond `Goal.md`.
