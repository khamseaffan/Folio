# Agent rules

- `Goal.md` is the requirement list.
- `docs/decisions.md` holds the product decisions added after that list.
- `docs/architecture.md` is the system diagram.
- `docs/conventions.md` is how docs are written.

## Rules required by the user

- Never write any migration code before pitching to the user and getting the permission to write it.
- Edit this file only if asked by the user.
- Before implementing always gather evidence, then recommend or implement the required actions.
- DB schema is written in `docs/source-of-truth/db-schema`.
- Before editing any files in `docs/source-of-truth`, ask the user first. Never change it without approval.
- Before making any changes, if the data, storage location (columns, tables) are changing you need to first get approval from the user to check if the proposed locations, usage and flows are correct.

## Always

- TypeScript: `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`.
- Client files never import `@centh/db`.
- Every new or modified service that can incur a cost, AI or non-AI, must use the shared cost tracker for each billable request or operation. Map it to the company, feature, operation, provider, and model where applicable. Record measured usage and known costs in `cost_entry`, with supporting details in the linked `audit_event`. Missing usage or pricing must be recorded explicitly, never assumed to be zero. The implementation is complete only after its cost mapping and tracking have been verified.
- No ghost packages. No demo routes. No Vercel crons in this foundation.
- Schema is modular under `packages/db/src/schema/`. Do not collapse into one file.
- Comments only for a why that is not obvious.
- Use LLMs to interpret meaning in unstructured content such as email, resumes, and free text. Deterministic code may enforce identity, authorization, tenant scope, allowed IDs, schemas, idempotency, evidence provenance, state integrity, and persistence, but must not impose exact phrasing or transport-format assumptions or reinterpret content semantics.
- Ground interpreted content in the current authorized source, tolerate presentation-only normalization, represent ambiguity explicitly, and isolate unsupported fields instead of rejecting an otherwise valid message.
- `main` is protected. PR + squash-merge. CI is a merge gate.
- Never add "Made with Cursor", "Made with Claude", or any LLM or harness attribution. Not in code, commits, pull requests, docs, or UI.
