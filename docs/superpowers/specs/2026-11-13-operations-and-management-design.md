# Operations, thread management, and account management

Date: 2026-11-13. Memory and schedules are a separate design that builds on the operation layer here.

## Intent

Every action on a person's records is one operation that two callers can use: a button in the website, and the agent in the chat. The person may tell the agent to rename a thread, or click rename. Both run the same code and pass the same gate. This is the base for memory and schedules, whose actions are agent only.

The second half is the missing account controls: the settings screen, the sessions, the token totals, and deleting the account. `Goal.md` requires that a person can delete their own account.

## Goals

- An operation layer that the API routes and the agent loop both call.
- The agent loop can call tools, more than once in a turn.
- A person creates, renames, and deletes a thread with a control, or by telling the agent.
- A settings screen with the display name, the sessions with an end control, the token totals, and deleting the account.

## Non-goals

- Memory, schedules, research, files, Drive, the browser, voice.
- The admin page, raising a cap, stopping an email, selecting the provider and model (units 1.h, 2.i, 2.j admin half, 2.k admin half).
- Integrations in settings. None exists.
- Searching threads, or the assistant naming a new thread (MLP).

## Anti-goals

- A button that runs different code from the agent's tool for the same action.
- The model supplying an account id, or deciding that a gate passed.
- Account actions (delete account, end session, display name) reachable by the agent.
- A new table or column. None is needed.

## Approaches considered

- **A. Operation registry (chosen).** Each action is a plain Python function with a typed input. A route calls it with the account id from the session. The loop exposes the same function as a tool and fills in the account id itself. The gate is inside the operation.
- **B. LangChain tools as the source.** Routes would call `tool.invoke()`. Rejected: every button would depend on LangChain, and keeping the account id from the model is awkward.
- **C. The agent calls the HTTP API.** Rejected: the agent would need its own credentials, each call is a network hop, and a scheduled run has no session cookie.

## The operation layer

- `backend/app/operations.py` holds the registry. An operation has a name, a one-sentence description for the model, a Pydantic input model, a function `(connection, account_id, context, input) -> result`, and a flag saying whether the agent may call it.
- `context` carries the thread the turn is running in, or nothing for a route call.
- The operation checks that every id in its input belongs to `account_id`. A row of another account is reported as not found.
- A result is a small dictionary the model can read, or an error sentence. An operation never raises for a missing row or a bad input.
- The table modules (`thread.py` and the rest) keep the SQL. An operation calls them.

## Thread operations

| Operation | Input | Route | Agent tool |
| --- | --- | --- | --- |
| `list_threads` | none | `GET /threads` | yes |
| `create_thread` | optional name | `POST /threads` | yes |
| `rename_thread` | thread id, name | `PATCH /threads/{id}` | yes |
| `delete_thread` | thread id | `DELETE /threads/{id}` | yes |

- A name is trimmed and is 1 to 80 characters. The check is in the operation. The table is unchanged.
- For the agent, the thread id is optional and defaults to the thread of the turn. The agent uses `list_threads` to find another thread by its name.
- Deleting a thread first stops a reply that is streaming in it. The existing foreign keys remove its messages and keep its usage rows.
- The agent may delete the thread it is replying in. The turn ends, the usage row is written with no message, and the website returns to the thread list.

## The loop

- The graph becomes model, then tools, then model, until the model calls no tool. A turn stops after 8 tool rounds and the reply says so.
- The `Model` interface gains tool calls: a call receives the tool list, and a reply may carry tool calls. `LangChainBrain` binds the tools. `FakeModel` takes a script of tool calls for tests.
- The tool list is the registry entries flagged for the agent. The loop builds each tool's schema from the input model, without the account id.
- Every model call in a turn writes its own usage row. All rows of a turn point at the one stored assistant message.
- Text from every model call in the turn streams to the person and is stored as one assistant message. Tool calls and results are not stored as messages.
- Stop works between and during calls. No tool starts after stop.
- When the turn ends the website reloads the thread list, so a rename or delete by the agent shows.

## Account

Direct controls only. These are routes on table-module functions, not registry operations.

| Route | Does |
| --- | --- |
| `GET /sessions` | The account's open sessions: start time, last activity, and which one is this browser. |
| `POST /sessions/{id}/end` | Ends one of the account's sessions. |
| `GET /usage` | Input, output, and total tokens for the account's email, including rows from an earlier deleted account. No cost. |
| `DELETE /account` | Deletes the account. |

- Deleting the account stops its streaming replies, deletes the Firebase user, then deletes the account row in one save. Foreign keys remove sessions, threads, and messages. Usage rows and the `email_limit` row stay. The cookie is cleared.
- If Firebase refuses, the account is not deleted and the person sees an error.
- The existing `POST /display-name` is used for the name.

## Screens

- Thread list: a new thread control (present today), and on each thread a rename and a delete control. Rename edits the name in place. Delete asks once, stating that the messages and schedules of the thread go with it.
- Settings opens from the signed-in shell. Sections: name, sessions, usage, delete account. Delete asks once, stating that it is immediate and cannot be restored. After it the public page shows.
- Settings is its own component file. `folio-app.tsx` is not grown further.
- A narrow window remains usable. Every control works by keyboard and has a label.

## Product docs changed by this work

- `docs/product/control.md`, `conversations.md`, `mvp-mlp.md` item 8: a thread is created, renamed, and deleted by a control or by telling the agent.
- `docs/plan/decisions.md`: the tool round limit and the thread name length.

## Build order

1. Operation layer, the loop with tools, the four thread operations and their routes. Main session.
2. Account routes. Subagent.
3. Thread controls in the website. Subagent. Depends on 1.
4. Settings screen. Subagent. Depends on 2.

2 and 3 run at the same time, each in its own worktree. Each unit is one pull request with tests first, and CI must pass.

## Calls made by the designer, not the person

- Thread name limit of 80 characters.
- The limit of 8 tool rounds in a turn.
- The agent may delete the thread it is in.
- Tool calls are not stored as messages.
- Firebase user deletion runs before the row delete, and a Firebase failure cancels the delete.
- Admin controls are left for later.

## Left to the builder

- Function and file names inside the layer, the event name for a tool round in the stream, and the visual details of the controls within the existing Fuji palette.
