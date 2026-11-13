# Agent rules

- `Goal.md` is the requirement list.
- `docs/decisions.md` holds the product decisions added after that list.
- `docs/architecture.md` is the system diagram.
- `docs/conventions.md` is how docs are written.

## Rules required by the user

- Never write any migration code before pitching to the user and getting the permission to write it.
- Edit this file only if asked by the user.
- Before implementing always gather evidence, then recommend or implement the required actions.
- The record picture is `docs/diagrams/schema.html`. Ask the user before changing a column, a table, or where a fact is stored.
- Before making any changes, if the data, storage location (columns, tables) are changing you need to first get approval from the user to check if the proposed locations, usage and flows are correct.
- Product behavior keeps determinism minimal. A new deterministic rule is discussed with the user and written only after the user agrees. Deterministic code stays limited to identity, authorization, tenant scope, allowed IDs, schemas, idempotency, evidence provenance, state integrity, and persistence.
- This product is an agent harness. The agent has tools and memory. The model chooses the tool, the memory, and the words. Code runs a gate before a tool. The model does not decide that a gate passed. A gate is one of the deterministic limits above, or a cap or confirmation already written in the product docs.

## Always

- Frontend TypeScript: `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`. The backend is Python.
- Client files never import the database package.
- Every new or modified service that can incur a cost, AI or non-AI, records a usage row for each billable call. The row stores the account email, the time, the operation, the provider, and the model where applicable, plus measured tokens and the expected cost. Missing usage is stored as missing, never assumed to be zero. The implementation is complete only after that usage row has been verified.
- No ghost packages. No demo routes. No Vercel crons in this foundation.
- The record picture keeps one entity per table. Code that creates tables stays one module per table.
- Comments only for a why that is not obvious.
- Use LLMs to interpret meaning in unstructured content such as email, resumes, and free text. Deterministic code may enforce identity, authorization, tenant scope, allowed IDs, schemas, idempotency, evidence provenance, state integrity, and persistence, but must not impose exact phrasing or transport-format assumptions or reinterpret content semantics.
- Ground interpreted content in the current authorized source, tolerate presentation-only normalization, represent ambiguity explicitly, and isolate unsupported fields instead of rejecting an otherwise valid message.
- `main` is protected. PR + squash-merge. CI is a merge gate.
- For stacked pull requests, use the `gh-stack` CLI extension.
- Never add "Made with Cursor", "Made with Claude", or any LLM or harness attribution. Not in code, commits, pull requests, docs, or UI.
