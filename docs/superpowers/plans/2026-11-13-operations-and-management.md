# Operations, Thread Management, and Account Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One operation layer that routes and the agent both call, thread create, rename and delete from a control or the chat, and the account controls (sessions, token totals, delete account, settings screen).

**Architecture:** `backend/app/operations.py` is a registry of plain functions with Pydantic inputs. A route calls an operation with the account id from the session. The LangGraph loop offers the same operations to the model as tools and fills in the account id itself. Account actions are plain routes and are never tools.

**Tech Stack:** Python 3.13, FastAPI, psycopg 3, Pydantic, LangGraph, LangChain chat model, PostgreSQL 18, Next.js 16, React 19, TypeScript strict.

**Spec:** `docs/superpowers/specs/2026-11-13-operations-and-management-design.md`

## Global Constraints

- No new table, column, constraint, or migration file. If a task seems to need one, stop and ask the person.
- No AI or tool attribution in code, commits, pull requests, or docs. No em dashes anywhere.
- The model never supplies an account id. Every id in an operation input is checked against the caller's account. A row of another account is reported as not found.
- Account actions (delete account, end session, display name) are never registered as agent tools.
- Every model call writes a usage row. Missing counts are stored as missing.
- Thread name: trimmed, 1 to 80 characters. Tool rounds in one turn: at most 8.
- Comments only for a why that is not obvious. One module per table for SQL.
- Frontend: `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`. Read the guide in `frontend/node_modules/next/dist/docs/` before writing Next.js code. English copy only.
- Backend checks: from `backend/`, `uv run ruff check .` and `uv run pytest`. Frontend checks: from `frontend/`, `npm run lint`, `npm run typecheck`, `npm run build`. The frontend has no test runner. Do not add one.
- `main` is protected. One pull request per unit, squash merge, CI must pass. The person merges, from the bottom of the stack up.
- The four units are one stack, managed with `gh-stack`: `main`, then `operation-layer` (Tasks 1 to 4), then `account-routes` (Task 5), then `thread-controls` (Task 6), then `settings-screen` (Task 7). No unit waits for a merge.
- Tasks 1 to 4 run in order in the main session. Tasks 5 and 6 start when Task 4 passes its checks locally and run at the same time, each in its own worktree branched from `operation-layer`. When both pass, `thread-controls` is restacked onto `account-routes`. Task 7 then starts from `thread-controls`.

## Review Focus

1. The model calls a tool that does not exist, or sends arguments that do not fit the input. Expected: the tool result is an error sentence and the turn continues. Test in Task 3.
2. A rename to only spaces, to 81 characters, or of another account's thread. Expected: refused, nothing changes. Tests in Task 2 and Task 4.
3. A thread is deleted by the button while its reply is streaming. Expected: the stream ends, no failure line is orphaned, and the usage row is still written with no message. Test in Task 4.
4. The account is deleted while a second session is open in another browser. Expected: that browser's next request gets 401, not 503. Test in Task 5.
5. The database fails inside a tool call. Expected: the turn ends with the failure line and does not hang. Test in Task 3.

---

### Task 1: Tool calls in the model interface

**Files:**
- Modify: `backend/app/model.py`, `backend/app/brain.py`
- Test: `backend/tests/test_model.py`

**Interfaces:**
- Produces, in `app.model`:

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict[str, object]  # JSON schema of the arguments

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]

@dataclass(frozen=True)
class Block:
    role: Literal["person", "assistant", "tool"]
    text: str
    tool_calls: tuple[ToolCall, ...] = ()   # only on an assistant block
    call_id: str | None = None              # only on a tool block

@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    tool_calls: tuple[ToolCall, ...] = ()

class Model(Protocol):
    def complete(self, prompt: str, blocks: list[Block], tools: Sequence[ToolSpec] = ()) -> Reply: ...
    def count_tokens(self, prompt: str, blocks: list[Block]) -> int: ...
    def stream(self, prompt: str, blocks: list[Block], tools: Sequence[ToolSpec] = ()) -> TextStream: ...
```

- `FakeModel` gains `script: tuple[Reply, ...] = ()`. Each `complete` or `stream` call takes the next scripted reply (its text is streamed as one chunk, its `tool_calls` appear on `reply()`). When the script is used up the existing `reply`/`chunks` behavior applies. `FakeModel.seen: list[tuple[list[Block], tuple[ToolSpec, ...]]]` records each call.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_model.py`

```python
def test_block_allows_tool_role_with_call_id():
    block = Block(role="tool", text='{"ok": true}', call_id="c1")
    assert block.call_id == "c1"

def test_fake_model_plays_a_script_then_falls_back():
    call = ToolCall(id="c1", name="list_threads", arguments={})
    model = FakeModel(reply="done", script=(Reply("", 1, 1, 2, (call,)),))
    first = model.stream("p", [], (ToolSpec("list_threads", "d", {}),))
    assert list(first) == []
    assert first.reply().tool_calls == (call,)
    second = model.stream("p", [])
    assert list(second) == ["done"]
    assert model.seen[0][1][0].name == "list_threads"

def test_brain_maps_tool_blocks_to_langchain_messages():
    # LangChainBrain(_RecordingChat())._messages(...) with an assistant block carrying
    # tool_calls and a tool block: assert an AIMessage with tool_calls[0]["id"] == "c1"
    # followed by a ToolMessage with tool_call_id == "c1".

def test_brain_binds_tools_and_reads_tool_calls():
    # A fake chat whose bind_tools(specs) records the specs and whose invoke returns
    # AIMessage(content="", tool_calls=[{"id": "c1", "name": "rename_thread", "args": {"name": "Tax"}}]).
    # assert reply.tool_calls == (ToolCall("c1", "rename_thread", {"name": "Tax"}),)
    # assert the bound spec is {"type": "function", "function": {"name", "description", "parameters"}}.
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_model.py -v`. Expected: the four new tests FAIL on missing names.
- [ ] **Step 3: Implement** the types above in `model.py`. In `brain.py`: `_messages` emits `AIMessage(content=text, tool_calls=[...])` and `ToolMessage(content=text, tool_call_id=call_id)`; `complete` and `stream` call `self._chat.bind_tools([...])` only when `tools` is not empty; `_reply` and `_ChatStream` read `tool_calls` from the message. In a stream, add the chunks together (`total = chunk if total is None else total + chunk`) and read `tool_calls` from the sum, because a tool call arrives in pieces.
- [ ] **Step 4: Update every test fake** that implements `complete` or `stream` (search `def stream(self, prompt` under `backend/tests/`) to accept `tools=()`.
- [ ] **Step 5: Run** `uv run pytest && uv run ruff check .`. Expected: all pass.
- [ ] **Step 6: Commit** `Let a model call carry tools and return tool calls.`

---

### Task 2: Operation registry and thread operations

**Files:**
- Create: `backend/app/operations.py`, `backend/app/live.py`
- Modify: `backend/app/thread.py`, `backend/app/turn.py` (move the live registry out)
- Test: `backend/tests/test_operations.py`, `backend/tests/test_thread.py`

**Interfaces:**
- Consumes: `ToolSpec` from Task 1.
- Produces, in `app.live` (moved unchanged from `turn.py`, names made public): `LiveReply`, `register(account_id, thread_id, live)`, `unregister(account_id, thread_id, live)`, `stop_reply(account_id, thread_id) -> None`, and new `stop_account(account_id) -> None` that sets stop on every live reply of that account. `turn.py` imports them and still exports `stop_reply`.
- Produces, in `app.thread`:

```python
def create_thread(connection, account_id: uuid.UUID, name: str | None = None) -> ThreadRow
def rename_thread(connection, account_id: uuid.UUID, thread_id: uuid.UUID, name: str) -> ThreadRow | None
def delete_thread(connection, account_id: uuid.UUID, thread_id: uuid.UUID) -> bool
```

- Produces, in `app.operations`:

```python
NOT_FOUND = "Thread not found."
BAD_NAME = "A thread name is 1 to 80 characters."
BAD_INPUT = "That input is not valid."
UNKNOWN = "Unknown operation."

@dataclass(frozen=True)
class Context:
    thread_id: uuid.UUID | None   # the thread of the turn, None for a route call

@dataclass(frozen=True)
class Operation:
    name: str
    description: str
    input: type[BaseModel]
    run: Callable[[psycopg.Connection, uuid.UUID, Context, Any], dict[str, object]]
    agent: bool

OPERATIONS: dict[str, Operation]

def call(connection, account_id: uuid.UUID, context: Context, name: str,
         arguments: dict[str, object]) -> dict[str, object]
def agent_tools() -> list[ToolSpec]
```

- A result is `{"ok": True, ...}` or `{"ok": False, "code": "not_found" | "invalid" | "unknown", "error": <sentence>}`. `call` validates `arguments` with the input model and returns `invalid` with `BAD_INPUT` on a validation error. It never raises for a bad input or a missing row.
- The four operations and their results:

| Name | Input model fields | `ok` result |
| --- | --- | --- |
| `list_threads` | none | `{"threads": [{"id", "name", "created_at"}]}` |
| `create_thread` | `name: str \| None = None` | `{"thread": {"id", "name", "created_at"}}` |
| `rename_thread` | `thread_id: uuid.UUID \| None = None`, `name: str` | `{"thread": {...}}` |
| `delete_thread` | `thread_id: uuid.UUID \| None = None` | `{"thread_id": "<id>"}` |

- A missing `thread_id` means `context.thread_id`. With neither, the result is `not_found`. A name is stripped, then must be 1 to 80 characters, else `invalid` with `BAD_NAME`. `delete_thread` calls `live.stop_reply(account_id, thread_id)` before the delete.
- `agent_tools()` returns a `ToolSpec` for each operation with `agent=True`, with `schema` from `input.model_json_schema()`. All four are `agent=True`.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_operations.py`. Create accounts with `ensure_account` as `tests/test_thread.py` does.

```python
def test_rename_changes_only_that_thread(): ...
    # two threads; call(..., "rename_thread", {"thread_id": str(a), "name": "  Tax  "})
    # result["thread"]["name"] == "Tax"; the other thread keeps "New thread"

def test_rename_refuses_bad_names():
    for name in ("", "   ", "x" * 81):
        result = call(conn, account, Context(None), "rename_thread", {"thread_id": str(t), "name": name})
        assert result == {"ok": False, "code": "invalid", "error": BAD_NAME}
    assert call(conn, account, Context(None), "rename_thread", {"thread_id": str(t), "name": "x" * 80})["ok"]

def test_another_accounts_thread_is_not_found():
    # rename and delete with the other account's id both return code "not_found"; the row is unchanged

def test_missing_thread_id_uses_the_context_thread(): ...
def test_no_thread_id_and_no_context_is_not_found(): ...

def test_delete_removes_messages_and_keeps_usage():
    # insert a message and a usage row pointing at it; delete_thread;
    # the message is gone, the usage row remains with message_id NULL and account_id set

def test_unknown_operation_and_bad_arguments():
    assert call(conn, account, Context(None), "drop_tables", {})["code"] == "unknown"
    assert call(conn, account, Context(None), "rename_thread", {"thread_id": "nope", "name": "a"})["code"] == "invalid"

def test_agent_tools_never_expose_an_account_id():
    for spec in agent_tools():
        assert "account_id" not in json.dumps(spec.schema)
    assert {spec.name for spec in agent_tools()} == {"list_threads", "create_thread", "rename_thread", "delete_thread"}
```

- [ ] **Step 2: Run** `uv run pytest tests/test_operations.py -v`. Expected: FAIL, module missing.
- [ ] **Step 3: Move the live registry** from `turn.py` to `live.py` and add `stop_account`. Run `uv run pytest`. Expected: existing tests still pass.
- [ ] **Step 4: Implement** the three `thread.py` functions and `operations.py`.
- [ ] **Step 5: Run** `uv run pytest && uv run ruff check .`. Expected: all pass.
- [ ] **Step 6: Commit** `Add the operation registry with the four thread operations.`

---

### Task 3: The loop calls tools

**Files:**
- Modify: `backend/app/loop.py`, `backend/app/turn.py`
- Test: `backend/tests/test_turn.py`, `backend/tests/test_thread_routes.py`

**Interfaces:**
- Consumes: `ToolSpec`, `ToolCall`, `Block`, `Reply`, `FakeModel(script=...)` from Task 1; `operations.call`, `operations.agent_tools`, `Context` from Task 2.
- Produces, in `app.loop`:

```python
MAX_TOOL_ROUNDS = 8
ROUNDS_REACHED = "I stopped after 8 tool steps."

def stream_turn(model: Model, prompt: str, blocks: list[Block], stop: threading.Event,
                tools: Sequence[ToolSpec] = (),
                call_tool: Callable[[ToolCall], str] | None = None)
```

- The graph is `model -> tools -> model`, ending when a reply has no tool calls. Custom stream events: `{"text": str}`, `{"usage": Reply}` once per model call, `{"tool": name}` before each tool runs.
- After a model reply with tool calls, the loop appends an assistant `Block` with those calls, runs each call through `call_tool`, and appends one tool `Block` per call with the returned text. No tool starts once `stop` is set. If the model asks for tools after round 8, the loop emits `{"text": ROUNDS_REACHED}` and ends.
- `ask()` and the summary fold stay without tools.
- In `turn.py`: `_write_reply` passes `operations.agent_tools()` and a `call_tool` that opens `connect()`, runs `operations.call(connection, account_id, Context(thread_id), call.name, call.arguments)`, and returns `json.dumps(result)`. It collects every `Reply` from `usage` events and publishes `{"tool": name}` on the stream. The `persist` callback type becomes `Callable[[str, list[Reply], bool, bool], None]`. `_save` and `_save_retry` write one usage row per reply, each with the stored assistant message id, and one missing row when the list is empty.
- An exception from `call_tool` fails the turn the same way a model error does.
- When the turn's own thread no longer exists at save time, `_save` stores no message and still writes the usage rows with no message id.

- [ ] **Step 1: Write the failing tests.** Use `app.model.FakeModel(script=...)` and the streaming helpers already in `tests/test_thread_routes.py`.

```python
def test_agent_renames_the_thread_it_is_in():
    # script: Reply("", 5, 1, 6, (ToolCall("c1", "rename_thread", {"name": "Taxes"}),)), then reply "Renamed."
    # after the stream: thread name == "Taxes"; one assistant message "Renamed.";
    # two usage rows, both with that message id; model.seen[1][0] ends with a tool block whose
    # text parses to {"ok": True, ...}

def test_unknown_tool_and_bad_arguments_continue_the_turn():
    # script calls "drop_tables", then rename_thread with {"name": ""}, then text "Sorry."
    # the stream ends with done; the tool blocks seen by the model carry code "unknown" and "invalid"

def test_turn_stops_after_eight_tool_rounds():
    # script of 9 replies that each call list_threads
    # stored assistant body ends with ROUNDS_REACHED; nine usage rows; list_threads ran 8 times

def test_agent_deletes_its_own_thread():
    # script: delete_thread with {} then text "Deleted."
    # the stream ends with done; the thread and its messages are gone;
    # the usage rows exist with message_id NULL

def test_no_tool_runs_after_stop():
    # hold the first model call, set stop, release; the scripted tool call never runs

def test_database_failure_inside_a_tool_fails_the_turn(monkeypatch):
    # monkeypatch operations.call to raise psycopg.OperationalError
    # the stream ends with {"failed": true} within 5 seconds and the failure line is stored
```

- [ ] **Step 2: Run** `uv run pytest tests/test_turn.py tests/test_thread_routes.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the loop and the turn changes above.
- [ ] **Step 4: Run** `uv run pytest && uv run ruff check .`. Expected: all pass.
- [ ] **Step 5: Commit** `Let the agent call operations as tools during a turn.`

---

### Task 4: Thread routes on the operations, and the docs

**Files:**
- Modify: `backend/app/main.py`, `docs/product/control.md`, `docs/product/conversations.md`, `docs/product/mvp-mlp.md` (item 8), `docs/plan/decisions.md`, `README.md` (status table)
- Test: `backend/tests/test_thread_routes.py`

**Interfaces:**
- Consumes: `operations.call`, `Context` from Task 2.
- Produces:

| Route | Body | Success | Errors |
| --- | --- | --- | --- |
| `GET /threads` | none | 200, list of `{"id", "name", "created_at"}` (unchanged shape) | 401 |
| `POST /threads` | optional `{"name": str}` | 200, the thread | 400 `BAD_NAME`, 401 |
| `PATCH /threads/{id}` | `{"name": str}` | 200, the thread | 400 `BAD_NAME`, 404, 401 |
| `DELETE /threads/{id}` | none | 204 | 404, 401 |

- All four go through `operations.call(connection, account_id, Context(None), ...)`. Map `code` to status: `invalid` to 400, `not_found` to 404. A thread id that is not a uuid is 404, as today. Add `PATCH` and `DELETE` to the CORS allowed methods if the middleware lists methods.

- [ ] **Step 1: Write the failing tests**

```python
def test_patch_renames_and_trims(): ...          # 200, name "Tax"; GET /threads shows it
def test_patch_refuses_bad_names(): ...          # "", "   ", "x"*81 -> 400 with BAD_NAME; missing body -> 400
def test_patch_and_delete_another_accounts_thread_are_404(): ...
def test_delete_returns_204_and_the_thread_is_gone(): ...   # GET /threads/{id} -> 404 after
def test_post_thread_with_a_name(): ...
def test_routes_need_a_session(): ...            # PATCH and DELETE without a cookie -> 401

def test_delete_while_the_reply_is_streaming():
    # hold the model mid-stream, DELETE the thread, release
    # the stream ends; no message row for that thread exists; one usage row exists with message_id NULL
```

- [ ] **Step 2: Run** the file. Expected: new tests FAIL with 405.
- [ ] **Step 3: Implement** the routes. Move `GET /threads` and `POST /threads` onto `operations.call`.
- [ ] **Step 4: Update the docs.** `control.md`, `conversations.md`, and `mvp-mlp.md` item 8 say: a thread is created, renamed, and deleted by a control or by telling the agent. `decisions.md` records the 80 character name limit and the 8 tool rounds. Follow `docs/conventions.md`.
- [ ] **Step 5: Run** `uv run pytest && uv run ruff check .`. Expected: all pass.
- [ ] **Step 6: Run the app by hand.** With `OPENAI_API_KEY` set, tell the agent "rename this thread to Groceries" and confirm `GET /threads` shows the name.
- [ ] **Step 7: Commit** `Serve thread create, rename, and delete through the operations.` Push `operation-layer`, open the pull request, wait for CI.

---

### Task 5: Account routes

Branch `account-routes`, from `operation-layer` once Task 4 passes its checks.

**Files:**
- Modify: `backend/app/session.py`, `backend/app/usage.py`, `backend/app/account.py`, `backend/app/sign_in_proof.py`, `backend/app/main.py`
- Test: `backend/tests/test_account_routes.py` (new), `backend/tests/test_session.py`, `backend/tests/test_usage.py`

**Interfaces:**
- Consumes: `live.stop_account(account_id)` from Task 2.
- Produces:

```python
# session.py
def list_open_sessions(account_id: uuid.UUID) -> list[tuple[uuid.UUID, datetime, datetime]]
    # (id, created_at, last_active_at), not ended and active within 30 minutes, oldest first
def end_account_session(account_id: uuid.UUID, session_id: uuid.UUID) -> bool
    # False when the session is not an open session of that account

# usage.py
def token_totals_for_email(connection, email: str) -> tuple[int, int, int]
    # input, output, total over rows where usage_missing is false

# sign_in_proof.py
def delete_firebase_user(firebase_uid: str) -> None
    # A user Firebase does not know counts as deleted. Any other failure raises SignInError.

# account.py
def delete_account(account_id: uuid.UUID) -> bool
```

| Route | Success | Errors |
| --- | --- | --- |
| `GET /sessions` | 200, `[{"id", "created_at", "last_active_at", "current": bool}]` | 401 |
| `POST /sessions/{id}/end` | 204. Ending the current one also clears the cookie. | 404, 401 |
| `GET /usage` | 200, `{"input_tokens", "output_tokens", "total_tokens"}` | 401 |
| `DELETE /account` | 204, cookie cleared | 502 `{"error": "The account could not be deleted. Try again."}`, 401 |

- `DELETE /account` order: `live.stop_account`, then `delete_firebase_user` (read `firebase_uid` from the account row; skip when it is NULL), then `delete_account`. A Firebase failure returns 502 and deletes nothing.
- None of these is added to `operations.OPERATIONS`.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_account_routes.py`. Monkeypatch `delete_firebase_user` where `main.py` looks it up.

```python
def test_sessions_lists_open_ones_and_marks_the_current(): ...   # two sign-ins; exactly one has current True
def test_ending_another_session_keeps_this_one(): ...            # 204; the other cookie now gets 401 on GET /session
def test_ending_another_accounts_session_is_404(): ...
def test_usage_sums_the_email_and_skips_missing_rows():
    # rows (10, 5, 15), (1, 1, 2) and one missing row -> {"input_tokens": 11, "output_tokens": 6, "total_tokens": 17}
def test_usage_includes_rows_from_a_deleted_earlier_account(): ...
def test_usage_has_no_cost_field(): ...                          # set(body) == the three keys

def test_delete_account_removes_rows_and_keeps_usage_and_limit():
    # after 204: no account, session, thread, or message rows; usage rows remain with account_id NULL;
    # the email_limit row keeps a raised token_cap and stopped value; Set-Cookie clears "session"

def test_delete_account_with_a_second_session_open():
    # the second browser's GET /session returns 401, not 503

def test_firebase_refusal_keeps_the_account():
    # delete_firebase_user raises SignInError -> 502; the account and its threads still exist

def test_sign_in_again_after_deletion_creates_a_new_account_with_the_old_cap(): ...
def test_account_actions_are_not_agent_tools():
    assert not {"delete_account", "end_session", "set_display_name"} & set(OPERATIONS)
```

- [ ] **Step 2: Run** the file. Expected: FAIL with 404 or 405.
- [ ] **Step 3: Implement** the functions and routes.
- [ ] **Step 4: Run** `uv run pytest && uv run ruff check .`. Expected: all pass.
- [ ] **Step 5: Commit** `Serve the sessions, the token totals, and account deletion.` Push, open the pull request, wait for CI.

---

### Task 6: Thread controls in the website

Branch `thread-controls`, from `operation-layer` once Task 4 passes its checks. Restacked onto `account-routes` when both pass.

**Files:**
- Create: `frontend/app/thread-list.tsx`
- Modify: `frontend/lib/api.ts`, `frontend/app/folio-app.tsx`, `frontend/app/globals.css`

**Interfaces:**
- Consumes: the Task 4 routes.
- Produces, in `frontend/lib/api.ts`: `call` accepts `"PATCH"` and `"DELETE"`, and

```ts
export async function renameThread(id: string, name: string): Promise<ApiResult<Thread>>
export async function deleteThread(id: string): Promise<ApiResult<true>>
```

- `thread-list.tsx` exports `ThreadList`, which takes the threads, the open thread id, and callbacks `onOpen`, `onCreate`, `onRename(id, name)`, `onDelete(id)`. Move the existing thread list markup out of `folio-app.tsx` into it. `folio-app.tsx` must end shorter than it started.

Behavior:
- Each thread row has a rename control and a delete control, shown on hover and on keyboard focus, each with an `aria-label` ("Rename thread", "Delete thread").
- Rename turns the name into a text input with the current name selected. Enter saves, Escape cancels, leaving the field saves. `maxLength` is 80. An empty name cancels. A 400 shows the server's sentence under the field.
- Delete asks once in a dialog: title "Delete this thread?", body "Its messages and schedules are deleted. This cannot be undone.", buttons "Cancel" and "Delete". Focus starts on "Cancel". Escape cancels.
- After deleting the open thread, the chat area shows its empty state.
- When a turn's stream ends (done or failed), reload the thread list. If the open thread is no longer in it, show the empty state. This is how a rename or delete by the agent appears.
- A `down` result shows the existing system-down notice. A 401 returns to the public page as other calls do.
- Use the existing palette and type sizes in `globals.css`. The list works at 360 px wide.

- [ ] **Step 1: Add** `renameThread` and `deleteThread` to `api.ts`.
- [ ] **Step 2: Extract** `ThreadList` with no behavior change. Run `npm run lint && npm run typecheck && npm run build`. Expected: pass.
- [ ] **Step 3: Add** rename, delete, the dialog, and the reload after a turn.
- [ ] **Step 4: Run** `npm run lint && npm run typecheck && npm run build`. Expected: pass.
- [ ] **Step 5: Check by hand** with the API running locally: rename by control, cancel with Escape, delete the open thread, delete another thread, tell the agent to rename the thread and see the list change, tab to every control, narrow the window to 360 px. Record what was checked in the pull request.
- [ ] **Step 6: Commit** `Rename and delete a thread from the thread list.` Push, open the pull request, wait for CI.

---

### Task 7: Settings screen

Branch `settings-screen`, from `thread-controls` once Tasks 5 and 6 pass their checks.

**Files:**
- Create: `frontend/app/settings.tsx`
- Modify: `frontend/lib/api.ts`, `frontend/app/folio-app.tsx` (one control that opens settings, and the render switch), `frontend/app/globals.css`

**Interfaces:**
- Consumes: the Task 5 routes, and the existing `postDisplayName`.
- Produces, in `frontend/lib/api.ts`:

```ts
export type SessionItem = { id: string; created_at: string; last_active_at: string; current: boolean };
export type TokenTotals = { input_tokens: number; output_tokens: number; total_tokens: number };
export async function listSessions(): Promise<ApiResult<SessionItem[]>>
export async function endSession(id: string): Promise<ApiResult<true>>
export async function getUsage(): Promise<ApiResult<TokenTotals>>
export async function deleteAccount(): Promise<ApiResult<true>>
```

- `settings.tsx` exports `Settings`, which takes the account, `onClose`, `onAccountChange(account)`, and `onSignedOut`.

Behavior:
- A "Settings" control in the signed-in shell opens the screen. "Back to chat" closes it.
- Four sections, in this order, each with a heading: "Name", "Sessions", "Usage", "Delete account".
- Name: a text field with the current display name, `maxLength` 40, and "Save". The saved name shows in the shell at once.
- Sessions: one row per session with "Started" and "Last active" times in the browser's locale. The current one is marked "This browser". Each row has "End session". Ending the current session returns to the public page.
- Usage: three figures labeled "Input tokens", "Output tokens", "Total tokens", with thousands separators. No cost, no cap.
- Delete account: one sentence, "Deleting your account is immediate and cannot be restored.", and a "Delete account" button. It asks once in a dialog: title "Delete your account?", body "Your threads and messages are deleted now. This cannot be undone.", buttons "Cancel" and "Delete account". Focus starts on "Cancel". After success call `signOutFirebase()` and return to the public page. A 502 shows the server's sentence.
- No section for integrations, the admin, or model selection.
- Every control has a label and works by keyboard. The screen works at 360 px wide. Use the existing palette.

- [ ] **Step 1: Add** the four API functions and types to `api.ts`.
- [ ] **Step 2: Build** `Settings` and wire the open and close controls.
- [ ] **Step 3: Run** `npm run lint && npm run typecheck && npm run build`. Expected: pass.
- [ ] **Step 4: Check by hand** with the API running locally: change the name, end the other session from a second browser, end the current session, compare the totals with `GET /usage`, cancel the delete dialog, delete a test account and sign in again, 360 px width. Record what was checked in the pull request.
- [ ] **Step 5: Commit** `Add the settings screen with sessions, usage, and account deletion.` Push, open the pull request, wait for CI.
