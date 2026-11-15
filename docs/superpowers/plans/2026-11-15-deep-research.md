# Deep Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person asks for a report in a thread. Folio searches the web, opens public pages in Chrome, reads them, and writes one report with the sources it really opened, inside limits that code enforces.

**Architecture:** A research run is a `run` of kind `research`, run by a second Cloud Run service that holds Chrome. The research loop is plain code that calls the model with tools: search, open a page, read a knowledge file, submit the report. Every address Chrome loads goes through a small proxy that refuses private destinations. The report and its source rows are saved once, in one transaction.

**Tech Stack:** Python 3.13, FastAPI, psycopg 3, Playwright with Chromium (research image only), Tavily search, Postgres 18, Cloud Run, Cloud Tasks, Terraform, Next.js 16, React 19.

**Spec:** `docs/superpowers/specs/2026-11-15-deep-research-design.md`

## Global Constraints

- The only schema changes are the two tables in Task 1, exactly as written. `report` has no `storage_key`. `report_source` has no `file_id`. No other table, column, or constraint. A search is a usage row and needs no schema change.
- One module per table: `backend/app/report.py` and `backend/app/report_source.py`, each with `apply(connection, force=False)` in the style of `backend/app/run.py`. `backend/app/migrate.py` runs both with `force=True`.
- Every SQL statement on these tables filters on `account_id`, except the account delete in Task 1. A report of another account is reported as not found.
- No route writes a report. Reports are changed only by the agent, through operations.
- The research loop's tools are only `search_web`, `open_page`, `submit_report`, and the three read tools `list_knowledge`, `search_knowledge`, `read_knowledge`. It has no thread, schedule, or memory-write tool.
- `open_page` accepts only an address that appeared in a search result of the same attempt, or in the links returned for a page the same attempt opened. The model cannot make up an address.
- Page text reaches only the digest call, as quoted material, with no tools. The loop sees the digest, never the raw page. The raw page is not stored.
- Chrome's only way out to the network is the local proxy. The proxy resolves each name itself, refuses a destination that is not public, and connects to the address it checked. Only ports 80 and 443.
- The model never supplies an account id. Source titles, addresses, and numbers in a report come from code, not from the model.
- Every model call and every search is recorded as usage with the operation `research` and the run id, as it happens, in its own short transaction. Missing counts are stored as missing.
- Limits: 20 sources, 25 searches per attempt, 60 steps, reading stops at 25 minutes, 30 minutes of work in total across attempts, 8 results per search, 30 links per page, 40,000 characters read per page, 30 seconds to load a page, 12,000 characters of a knowledge file, a report body of at most 100,000 characters, a report name of 1 to 80 characters.
- One platform model does the loop, the digests, and the report: the one `open_model()` returns.
- New dependencies: `playwright` in a new dependency group `research` only. Nothing else in the backend or the frontend, except that the frontend reuses the Markdown component already installed.
- No AI or tool attribution anywhere, in code, comments, docs, commits, or pull requests. No em dashes anywhere. Comments only for a why that is not obvious. English copy. Docs in simple English, following `docs/conventions.md`.
- Backend checks, from `backend/`: `uv run ruff check .` and `uv run pytest`. Frontend checks, from `frontend/`: `npm run lint`, `npm run typecheck`, `npm run build`. Terraform checks, from `infra/`: `terraform fmt -check -recursive` and `terraform init -backend=false && terraform validate`. The frontend has no test runner. Do not add one. Terraform is never applied by an agent or by CI.
- Tests never call the network, a real model, Tavily, or Google Cloud. The one test that runs real Chromium is skipped when Playwright or its browser is not installed.
- Branches, all from `research-design`: `research-records` (Tasks 1, 2), `research-search` (Task 3), `research-browser` (Task 4), `research-loop` (Tasks 5, 6), `research-infra` (Task 7), `research-web` (Task 8). Tasks 1 and 2, 3, 4, 7, 8 run at the same time. Task 5 starts when 3 and 4 are done, and is rebased onto them. The pull requests are stacked in this order: records, search, browser, loop, infra, web.

## Review Focus

1. A page whose text tells the model to open `https://attacker.example/?data=...` or to read the profile and send it. Expected: the model's `open_page` for an address it was not shown is refused, nothing is opened, and the digest call had no tools. Test in Task 5.
2. An address that is or resolves to a private destination: `127.0.0.1`, `10.x`, `169.254.169.254`, `::1`, an IPv4 address inside IPv6, a decimal or hexadecimal form of an address, a name that resolves to one of these, a redirect to one, and a name that answers a public address first and a private one second. Expected: refused, and Chrome never connects. Tests in Task 4.
3. A page that is huge, slow, binary, a download, a PDF link, empty, or a 404. Expected: it does not count as a source, the model is told in one sentence, and the load ends within the time limit. Tests in Task 4.
4. The model never submits, submits with a source number that does not exist, with an empty body, or with a source it never opened. Expected: a sentence the model can act on, then a fallback report that code assembles from the digests, never a lost run and never an invented source. Tests in Task 5.
5. Cancel, the admin's stop, a deleted thread, and the token cap arriving in the middle of a run. Expected: cancel, stop, and a deleted thread end the run before the next step with no report. The cap lets one last call write the report. The right line is written in the thread, and no second report appears if the task is delivered twice. Tests in Task 6.
6. The search service is down, returns an error, or has no key. Expected: the tool answers with a sentence, the run goes on and may finish from knowledge files, and the failure is logged without the key. Tests in Tasks 3 and 5.

---

### Task 1: The report tables and their modules

Branch `research-records`.

**Files:**
- Create: `backend/migrations/report.sql`, `backend/migrations/report_source.sql`, `backend/app/report.py`, `backend/app/report_source.py`
- Modify: `backend/app/migrate.py`, `backend/app/account.py` (`delete_account`), `backend/tests/test_migrate.py` (add both tables to `TABLES`), `docs/diagrams/schema.html` (remove `report.storage_key` and `report_source.file_id`)
- Test: `backend/tests/test_report.py`

**The SQL, exactly:**

```sql
-- report.sql
CREATE TABLE IF NOT EXISTS report (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    thread_id uuid,
    run_id uuid NOT NULL,
    name text NOT NULL,
    question text NOT NULL,
    body text NOT NULL,
    stop_note text NOT NULL,
    status text NOT NULL DEFAULT 'ready',
    content_type text NOT NULL DEFAULT 'text/markdown',
    byte_size bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, account_id),
    UNIQUE (run_id),
    CONSTRAINT report_status_allowed CHECK (status = 'ready'),
    CONSTRAINT report_thread_account_fkey FOREIGN KEY (thread_id, account_id)
        REFERENCES thread (id, account_id) ON DELETE SET NULL (thread_id),
    CONSTRAINT report_run_account_fkey FOREIGN KEY (run_id, account_id)
        REFERENCES run (id, account_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS report_account_id_created_at
    ON report (account_id, created_at);

-- report_source.sql
CREATE TABLE IF NOT EXISTS report_source (
    account_id uuid NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    report_id uuid NOT NULL,
    position integer NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    url text,
    used_for text NOT NULL,
    knowledge_file_id uuid,
    PRIMARY KEY (report_id, position),
    CONSTRAINT report_source_kind_allowed CHECK (kind IN ('page', 'file', 'drive_copy')),
    CONSTRAINT report_source_report_account_fkey FOREIGN KEY (report_id, account_id)
        REFERENCES report (id, account_id) ON DELETE CASCADE,
    CONSTRAINT report_source_knowledge_account_fkey FOREIGN KEY (knowledge_file_id, account_id)
        REFERENCES knowledge_file (id, account_id) ON DELETE SET NULL (knowledge_file_id)
);
```

`report.apply` applies `run` and `thread` first. `report_source.apply` applies `report` and `knowledge_file` first.

**Interfaces:**

```python
# app/report_source.py
SourceRow = tuple[int, str, str, str | None, str, uuid.UUID | None]
    # position, kind, title, url, used_for, knowledge_file_id
@dataclass(frozen=True)
class NewSource:
    kind: str; title: str; url: str | None; used_for: str; knowledge_file_id: uuid.UUID | None = None
def apply(connection, force: bool = False) -> None
def list_sources(connection, account_id, report_id) -> list[SourceRow]      # by position

# app/report.py
ReportRow = tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, uuid.UUID, str, str, str, str, str, str, int, datetime]
    # id, account_id, thread_id, run_id, name, question, body, stop_note, status, content_type, byte_size, created_at
def apply(connection, force: bool = False) -> None
def create_report(connection, account_id, run_id, thread_id, name, question, body, stop_note,
                  sources: Sequence[NewSource]) -> ReportRow | None
    # None when the run already has a report (ON CONFLICT (run_id) DO NOTHING). Sources are numbered
    # from 1 in the order given. byte_size is the byte length of render_download for this report.
def list_reports(connection, account_id) -> list[ReportRow]                 # newest first
def get_report(connection, account_id, report_id) -> ReportRow | None
def rename_report(connection, account_id, report_id, name) -> ReportRow | None
def delete_report(connection, account_id, report_id) -> bool
def render_download(report: ReportRow, sources: Sequence[SourceRow]) -> str
    # Markdown: "# {name}", the question, the body, "## How this research stopped" and the stop note,
    # then "## Sources" with a numbered line for each: title, address when there is one, and "used for".
def download_filename(name: str) -> str
    # Lowercase letters, digits, and hyphens, at most 60 characters, ending in ".md". "report.md" when
    # nothing is left. Never contains a slash, a dot-dot, a quote, or a control character.
```

`account.delete_account` deletes the account's reports first, in the same transaction, then the account, because `report.run_id` restricts the delete of its run.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_report.py` (helpers as in `backend/tests/test_run.py`: a thread, a run of kind `research`, unique email per test, cleanup that creates the tables first):
  - create then get, list, rename, delete; list is newest first; sources are numbered from 1 in order and `byte_size` equals the length of `render_download(...).encode()`.
  - a second `create_report` for the same run returns None and leaves one row and one set of sources.
  - another account cannot get, rename, delete, or see the report or its sources.
  - deleting the thread clears `thread_id` and keeps the report. Deleting a knowledge file clears `knowledge_file_id` and keeps the source row and its title.
  - deleting a run that has a report is refused by the database. Deleting the account removes its reports, sources, runs, and threads in one call, and `delete_account` still works for an account with no report.
  - `render_download` contains the question, the body, the stop note, and for each source its title, its address, and its used-for line. A source with no address prints no empty brackets.
  - `download_filename` for `"Heat pumps vs. boilers!"`, `"../../etc/passwd"`, `"   "`, `"Ünïcode name"`, a 200 character name, and one with a newline and a quote: no slash, no dot-dot, only `[a-z0-9-]` before `.md`, at most 64 characters in all, and `report.md` when empty.
  - a `report` row with status other than `ready` and a source with kind `video` are refused by the database.
- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_report.py -q`. Expected: FAIL, modules missing.
- [ ] **Step 3: Implement** the SQL, the two modules, the `migrate.py` lines, the `delete_account` change, and the picture change.
- [ ] **Step 4: Run** `uv run ruff check . && uv run pytest -q`, also on a new empty database. Expected: all pass.
- [ ] **Step 5: Commit** `Store reports and their sources.`

---

### Task 2: Research operations, routes, and the queue's kind

Branch `research-records`, after Task 1.

**Files:**
- Modify: `backend/app/operations.py`, `backend/app/run.py` (two functions), `backend/app/queue.py`, `backend/app/main.py`, `backend/app/turn.py` (one prompt constant)
- Test: `backend/tests/test_research_operations.py`, `backend/tests/test_report_routes.py`, additions to `backend/tests/test_queue.py`

**Interfaces:**
- Consumes: Task 1.
- Produces, in `app.run`:

```python
def cancel_run(connection, account_id, run_id) -> bool
    # Sets status 'cancelled' on a run of kind 'research' of that account that is queued or working. False otherwise.
def list_in_flight(connection, account_id, kind: str) -> list[RunRow]     # queued or working, newest first
```

- Produces, in `app.queue`: `enqueue(run_id, kind="schedule", client=None)`. A research run uses `RESEARCH_QUEUE`, `RESEARCH_URL`, and `INVOKER_ACCOUNT`. A schedule keeps `RUN_QUEUE`, `WORKER_URL`, and `INVOKER_ACCOUNT`. The task name, audience, and deadline are as for schedules. When the variables for that kind are missing and the environment variable `K_SERVICE` is set (the code is running on Cloud Run), `enqueue` raises `QueueNotConfigured` and never falls back to the local form. Without `K_SERVICE` the local form runs as today.
- Produces, in `app.operations`: six operations, all `agent=True` and `chat_only=True`:

| Name | Input | `ok` result |
| --- | --- | --- |
| `start_research` | `question: str` | `{"run": {"id", "question", "status": "queued"}}` |
| `cancel_research` | `run_id: uuid.UUID` | `{"run_id"}` |
| `list_research_runs` | none | `{"runs": [{"id", "question", "status", "created_at"}]}` |
| `list_reports` | none | `{"reports": [{"id", "name", "question", "created_at"}]}` |
| `rename_report` | `report_id: uuid.UUID`, `name: str` | `{"report": {"id", "name"}}` |
| `delete_report` | `report_id: uuid.UUID` | `{"report_id"}` |

  Constants: `NO_REPORT = "Report not found."`, `NO_RUN = "Run not found."`, `BAD_REPORT_NAME = "A report name is 1 to 80 characters."`, `BAD_QUESTION = "A research question is 1 to 2,000 characters."`, `NOT_AVAILABLE = "Research is not available right now."`.

- Rules: `question` is stripped, 1 to 2,000 characters, no null character, encodable as UTF-8, else `invalid` with `BAD_QUESTION`. With no thread in the context `start_research` is `not_found` with the thread sentence. A stopped account or an account at its token cap is refused with `code` `limit` and the same sentences chat uses (`_STOPPED`, `_CAPPED` in `app.turn`). The run is made with `create_run(..., schedule_id=None, kind="research", task=question)`, then `connection.commit()`, then `enqueue(run_id, "research")`. If `enqueue` raises `QueueNotConfigured` the run is marked `failed` and the result is `{"ok": False, "code": "unavailable", "error": NOT_AVAILABLE}`, logged as an error. Any other exception from `enqueue` is logged with the run id and the result is still ok, because the tick puts a queued run back on its queue after 2 minutes. A report name is 1 to 80 characters, stripped, no null character, else `BAD_REPORT_NAME`. A missing run or report, or one of another account, is `not_found`. `cancel_research` works only on research runs.
- Routes in `main.py`, all needing a session:

| Route | Success | Errors |
| --- | --- | --- |
| `GET /reports` | 200, `[{"id", "name", "question", "created_at"}]` newest first | 401 |
| `GET /reports/{id}` | 200, `{"id", "name", "question", "body", "stop_note", "created_at", "sources": [{"position", "kind", "title", "url", "used_for"}]}` | 404 `{"error": "Not found"}`, 401 |
| `GET /reports/{id}/download` | 200, `render_download` as `text/markdown; charset=utf-8` with `Content-Disposition: attachment; filename="<download_filename>"` | 404, 401 |

- In `turn.py`, add a public constant `RESEARCH` to the chat system prompt only, after the memory instruction. A scheduled run's prompt does not get it. Exact text:

```
When the person asks for a report that needs many sources, make sure you understand the question. If it is unclear, ask one short question first. Then call start_research with the question written out in full, tell the person it has started, and say the report will appear in Reports when it is ready. Do not do the research yourself in the chat.
```

- [ ] **Step 1: Write the failing tests:**
  - `start_research` makes a `research` run for the thread and calls `enqueue` once with `("research")` after the run is committed (assert the run row is visible from a second connection at the moment `enqueue` is called). A stopped account and a capped account are refused and make no run. A bad question is refused. `QueueNotConfigured` fails the run and answers `NOT_AVAILABLE`. A raising `enqueue` still answers ok.
  - `cancel_research` cancels a queued and a working research run of the caller, refuses a schedule run, a finished run, and another account's run, and a cancelled queued run is not started by `start_attempt`.
  - `list_research_runs`, `list_reports`, `rename_report` (bad names, another account's report), `delete_report` (the report is gone, its sources are gone, its run stays, the thread stays).
  - `agent_tools()` has all six, `agent_tools(in_run=True)` has none of them, and no schema mentions an account id.
  - routes: needs a session, returns only the caller's reports, 404 for another account's, the download body equals `render_download`, the header filename is safe, and there is no route that writes a report (POST, PUT, PATCH, DELETE on all three give 404 or 405).
  - the chat system prompt contains `RESEARCH`, and a scheduled run's prompt does not. Update the tests that rebuild the expected chat prompt.
  - queue: research uses its own variables and queue path; a schedule still uses its own; with `K_SERVICE` set and the variables missing, `QueueNotConfigured` is raised and no thread starts; without `K_SERVICE` the local form still runs.
- [ ] **Step 2: Run** the new files. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run ruff check . && uv run pytest -q`.
- [ ] **Step 5: Commit** `Let the agent start and cancel research and manage reports.`

---

### Task 3: The search service and its usage row

Branch `research-search`.

**Files:**
- Create: `backend/app/search.py`
- Modify: `backend/app/usage.py`
- Test: `backend/tests/test_search.py`, additions to `backend/tests/test_usage.py`

**Interfaces:**

```python
# app/search.py
@dataclass(frozen=True)
class SearchResult:
    title: str; url: str; snippet: str
class SearchError(Exception): ...        # the message is safe to show to the model and never holds the key
class Search(Protocol):
    def search(self, query: str, limit: int = 8) -> list[SearchResult]: ...
class TavilySearch:
    def __init__(self, api_key: str, post: Callable[..., Any] | None = None) -> None
class FakeSearch:
    def __init__(self, results: Sequence[SearchResult] = (), error: Exception | None = None) -> None
    # records every query in .queries
def open_search() -> Search
    # TavilySearch when TAVILY_API_KEY is set and not blank. Otherwise an object whose search() raises
    # SearchError("Web search is not configured.").

# app/usage.py
SEARCH_PRICE: Decimal
def record_search(connection, account_id, email, run_id) -> UsageRow
```

- `TavilySearch` makes one HTTPS `POST https://api.tavily.com/search` with a JSON body of the query, `max_results`, and basic depth, and the key in an `Authorization: Bearer` header. Use the standard library, with a 15 second timeout. Confirm the request and response shape against Tavily's current documentation before writing it, and say in the report what you confirmed and where. A result is kept only when its address starts with `http://` or `https://`. Duplicate addresses are dropped. The list is cut to `limit`.
- An HTTP error answers `SearchError("Search failed with status N.")`. A network error answers `SearchError("Search could not be reached.")`. A body that is not the expected JSON answers `SearchError("Search returned something unreadable.")`. No message holds the key, and the key is cleaned out of any text that is logged, as `app.model` does.
- `record_search` stores one row: `operation` `research`, `provider` `tavily`, `model` `search`, no token counts, `usage_missing` false, `estimated` false, `expected_cost` `SEARCH_PRICE`, and the run id. `SEARCH_PRICE` is the vendor's published price for one basic search. Look it up on the vendor's pricing page and write the source in a comment. If it cannot be reached use `Decimal("0.008")` and say in the report that it is unconfirmed.

- [ ] **Step 1: Write the failing tests** with a stub `post`: a normal answer maps to results; non-web addresses and duplicates are dropped and the limit applies; a 401, a 429, a 500, a timeout, and an unreadable body each give the right `SearchError` sentence; the key never appears in an error message or a log record (use `caplog`); `open_search()` without the key raises the not-configured sentence on `search()`, and with the key returns a `TavilySearch`; `record_search` writes the row above, adds nothing to `tokens_for_email` or `token_totals_for_email`, and keeps the account id when the account exists and clears it when the account is deleted.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run ruff check . && uv run pytest -q`.
- [ ] **Step 5: Commit** `Add the search service and record each search as usage.`

---

### Task 4: Reading a page safely

Branch `research-browser`. Two commits.

**Files:**
- Create: `backend/app/urlcheck.py`, `backend/app/proxy.py`, `backend/app/robots.py`, `backend/app/browser.py`
- Modify: `backend/pyproject.toml` and `backend/uv.lock` (group `research` with `playwright`), `docs/hand-in/report.md` (security lines)
- Test: `backend/tests/test_urlcheck.py`, `backend/tests/test_proxy.py`, `backend/tests/test_robots.py`, `backend/tests/test_browser.py`

**Interfaces:**

```python
# app/urlcheck.py
class BadAddress(Exception): ...
def is_public_ip(ip: str) -> bool
    # False for loopback, private, link-local (including 169.254.169.254), multicast, reserved, unspecified,
    # carrier-grade NAT (100.64.0.0/10), unique local and link-local IPv6, and an IPv4 address inside IPv6.
def normalize_url(url: str) -> str
    # http and https only, a host, no user name or password, ports 80 and 443 only, at most 2,048 characters.
    # Lowercases the scheme and host, drops the fragment and a default port. Raises BadAddress.
def resolve_public(host: str, resolver=socket.getaddrinfo) -> list[str]
    # Every address the name resolves to. Raises BadAddress when there is none, or when any is not public.

# app/proxy.py
class SafeProxy:
    def __init__(self, resolver=socket.getaddrinfo, is_public=is_public_ip) -> None
    def start(self) -> int          # listens on 127.0.0.1 only, on a free port, in a background thread
    def stop(self) -> None
    refused: list[str]              # hosts it refused, for tests and logs

# app/robots.py
USER_AGENT = "FolioResearch/0.1"
class Robots:
    def __init__(self, fetch: Callable[[str], tuple[int, str]], agent: str = "FolioResearch") -> None
    def allowed(self, url: str) -> bool
def fetch_through_proxy(port: int) -> Callable[[str], tuple[int, str]]

# app/browser.py
MAX_PAGE_CHARS = 40_000; MAX_LINKS = 30; PAGE_TIMEOUT_SECONDS = 30
@dataclass(frozen=True)
class Link: text: str; url: str
@dataclass(frozen=True)
class OpenedPage: url: str; title: str; text: str; links: tuple[Link, ...]
class PageError(Exception): ...     # the message is one sentence safe to show to the model
class PageOpener(Protocol):
    def open(self, url: str) -> OpenedPage: ...
    def close(self) -> None: ...
class FakePageOpener:
    def __init__(self, pages: Mapping[str, OpenedPage | PageError]) -> None   # records .opened
class ChromePageOpener:
    def __init__(self, proxy: SafeProxy | None = None, robots: Robots | None = None) -> None
```

- The proxy is an HTTP proxy. For `CONNECT host:port` and for `GET` and `HEAD` of an absolute `http://` address: resolve the name once with `resolver`, refuse with `403` unless every address passes `is_public`, connect to the address it checked, and pass bytes both ways. Any other method gets `405`. Ports other than 80 and 443 get `403`. At most 32 connections at once, 60 seconds idle, 120 seconds in total per connection.
- `Robots`: 200 reads the file and applies it for the agent token. A 4xx other than 429 allows everything. A 5xx, 429, or a failed fetch disallows the whole host for the run. One fetch per host, cached for the life of the object. `fetch_through_proxy` gets the file through the safe proxy, with the user agent, a 10 second timeout, and at most 500 KB.
- `ChromePageOpener.open`: `normalize_url`, then `Robots.allowed`, then Chromium through Playwright, started once with `--proxy-server=http://127.0.0.1:{port}`, `--no-sandbox`, and `--disable-dev-shm-usage`. Each page gets a new empty context with downloads off, no permissions, service workers blocked, and the Folio user agent. Requests for images, media, and fonts are aborted, and so is any request that is not `http` or `https`. The page loads to `domcontentloaded`, then waits up to 5 seconds for the network to go quiet. Accepted types are HTML, XHTML, and plain text; anything else is a `PageError` that names the type. A status of 400 or more, a timeout, a page with fewer than 20 readable characters, and a refused address are each a `PageError` with a different sentence. Text is the body's visible text with white space collapsed, cut to `MAX_PAGE_CHARS`. Links are the first `MAX_LINKS` distinct `http` or `https` links in document order, with at most 100 characters of text. The context is closed in every case.
- Add `playwright` to a dependency group `research` in `pyproject.toml`, so the API image does not carry it. Playwright is imported only inside `ChromePageOpener`.

- [ ] **Step 1 (commit 1): Write the failing tests for `urlcheck`, `proxy`, and `robots`.**
  - `is_public_ip`: false for `127.0.0.1`, `10.1.2.3`, `172.16.0.1`, `192.168.1.1`, `169.254.169.254`, `0.0.0.0`, `100.64.0.1`, `::1`, `fe80::1`, `fc00::1`, `::ffff:127.0.0.1`, `::ffff:10.0.0.1`, `224.0.0.1`, and true for `8.8.8.8` and `2606:4700:4700::1111`.
  - `normalize_url`: refuses `file:///etc/passwd`, `ftp://x`, `data:text/html,x`, `http://user:pw@host/`, `http://host:8080/`, `javascript:alert(1)`, an empty string, and a 3,000 character address; accepts and normalizes `HTTPS://Example.COM:443/a?b=1#frag` to `https://example.com/a?b=1`.
  - `resolve_public` with a stub resolver: a name that gives `8.8.8.8` passes; a name that gives `127.0.0.1`, or `8.8.8.8` and `10.0.0.5` together, or nothing, is refused; so are the stubbed answers for `2130706433`, `0x7f.1`, and `0177.0.0.1`.
  - `SafeProxy`, with real sockets on loopback and an injected `is_public` that accepts `127.0.0.1` only for the one allowed test name: an origin reached through `CONNECT allowed.test:443` and through a plain `GET` returns its content; the same through the default `is_public` is answered `403` for `localhost`, a name stubbed to `169.254.169.254`, `10.0.0.5`, `[::1]`, and `::ffff:127.0.0.1`; a port other than 80 and 443 is `403`; `POST` is `405`; a resolver that answers a public address first and a private address on a second call makes the proxy connect to the first, and the test checks the address the origin saw; the proxy listens only on `127.0.0.1`; `refused` lists the refused hosts.
  - `Robots`: a disallowed path, an allowed path, no file (404), a file that fails (500), a fetch that raises, the agent token versus `*`, and one fetch per host.
- [ ] **Step 2: Run, implement, run** `uv run ruff check . && uv run pytest -q`. Commit `Check addresses, and keep Chrome behind a proxy that refuses private destinations.`
- [ ] **Step 3 (commit 2): Write the failing tests for `ChromePageOpener`** in `backend/tests/test_browser.py`, skipped with `pytest.importorskip("playwright")` and a check that Chromium is installed. Use a local HTTP server on loopback and a `SafeProxy` whose `is_public` accepts loopback and a resolver that maps test names to it:
  - a plain page returns its title and visible text; a page whose text is written by a script is read after it runs; a redirect is followed and the final address is returned.
  - a 404, an image, a PDF content type, a download, an empty page, and a page that never answers (with `PAGE_TIMEOUT_SECONDS` patched to 2) each raise the right `PageError` within 10 seconds.
  - a 200,000 character page is cut to `MAX_PAGE_CHARS`. A page with 50 links gives 30. A page with images makes no image request (the server records none).
  - a page that tries to load a script from a name that resolves to a private address: the proxy lists it in `refused` and the page still returns its own text.
  - `robots.txt` that disallows the path gives a `PageError` and the page is never requested.
  - `FakePageOpener` returns the pages it was given, records the order opened, and raises a `PageError` it was given.
- [ ] **Step 4: Run, implement, run** the full suite. Add three lines to the Security section of `docs/hand-in/report.md`: Chrome reads public pages only and its only way out is a proxy that refuses private destinations; the model can open only addresses it was shown; page text reaches only a call with no tools. Commit `Open a public page in Chrome and read it.`

---

### Task 5: The research loop

Branch `research-loop`, after Tasks 3 and 4 are rebased in.

**Files:**
- Create: `backend/app/research.py`
- Test: `backend/tests/test_research.py`

**Interfaces:**
- Consumes: `Search`, `SearchResult`, `SearchError` (Task 3); `PageOpener`, `OpenedPage`, `PageError` (Task 4); `NewSource` (Task 1); `Model`, `Block`, `Reply`, `ToolCall`, `ToolSpec` from `app.model`; `normalize_url` from `app.urlcheck`.
- Produces:

```python
READ_SECONDS = 25 * 60; WORK_SECONDS = 30 * 60; MAX_SOURCES = 20; MAX_SEARCHES = 25; MAX_STEPS = 60
SEARCH_RESULTS = 8; KNOWLEDGE_READ_CHARS = 12_000; MAX_REPORT_CHARS = 100_000; NAME_LIMIT = 80

@dataclass(frozen=True)
class Outcome:
    status: str                       # "ready", "failed", or "cancelled"
    reason: str                       # "submitted", "sources", "time", "searches", "steps", "capped",
                                      # "cancelled", "stopped", "thread_gone", or "no_source"
    name: str = ""; body: str = ""; stop_note: str = ""
    sources: tuple[NewSource, ...] = ()

class Control(Protocol):
    def check(self) -> str | None: ...        # None to go on, else "cancelled", "stopped", "capped", or "thread_gone"
class Recorder(Protocol):
    def model_call(self, reply: Reply | None) -> None: ...   # None when the call raised
    def search_call(self) -> None: ...

def run_research(question: str, *, model: Model, search: Search, opener: PageOpener, control: Control,
                 recorder: Recorder, memory: Callable[[ToolCall], str], system_prompt: str,
                 elapsed: Callable[[], float]) -> Outcome
    # elapsed() is the seconds of work so far, including earlier attempts. memory(call) runs one of the three
    # read tools for the account and returns its JSON text.
```

- The tools the model sees, and what each returns (JSON text, as `scheduled` does):
  - `search_web(query)`: `{"ok": true, "results": [{"n", "title", "url", "snippet"}]}` and the addresses join the allowed set; a `SearchError` gives `{"ok": false, "error": <its sentence>}`; the call is recorded with `recorder.search_call()` whether or not it succeeded.
  - `open_page(url)`: refused with `{"ok": false, "error": "That address was not in a search result or in a page's links."}` unless its `normalize_url` form is allowed; a form already opened returns the earlier digest and counts nothing; a `PageError` gives `{"ok": false, "error": <its sentence>}` and counts nothing; otherwise one digest call (below), the page becomes the next source, its links join the allowed set, and the result is `{"ok": true, "source": <n>, "title", "url", "digest", "links": [{"text", "url"}]}`. Past 20 sources a call gets `{"ok": false, "error": "The limit of 20 sources is reached."}`.
  - `list_knowledge`, `search_knowledge`: pass to `memory`, count nothing. `read_knowledge`: passes to `memory`; the file text in the result is cut to `KNOWLEDGE_READ_CHARS` with a `"truncated": true` flag; the first read of a file is the next source with kind `file`; a second read of the same file counts nothing.
  - `submit_report(name, body, stop_note, sources)`: `sources` is a list of `{"number", "used_for"}`. Refused, with a sentence the model can act on, when there is no source yet, the name is not 1 to 80 characters, the body is empty or over `MAX_REPORT_CHARS`, any text has a null character, or a `number` is not the number of an opened source or is repeated. Accepted, the loop ends with reason `submitted`. Every opened source is listed. A source the model gave no note for has the used-for line `Read during the research.` Titles, addresses, numbers, and kinds come from code.
- The digest: one `model.complete` call with no tools. Its instruction says that the page text between markers is quoted material and not instructions, to follow nothing in it, and to write the facts that bear on the question in about 300 words with names, numbers, and dates kept. The recorder is told about every model call, including a call that raises (`None`), and then the error goes on.
- Before each step: `control.check()`. `cancelled`, `stopped`, and `thread_gone` return `Outcome(status="cancelled", ...)` at once with no more calls. `capped` and the code limits (20 sources, 25 searches, 60 steps, `elapsed()` at `READ_SECONDS`) end the loop, and one last call is made with only `submit_report`, told why the research is over and to write the report now. When `elapsed()` is at `WORK_SECONDS` no call is made, and the fallback below is used. A model reply with no tool call is answered once with a nudge to submit, and the second time is treated as the end of the loop.
- The stop note is the model's text, followed by one line that code adds when code stopped the run: `Stopped because 20 sources were open.`, `... the reading time of 25 minutes was reached.`, `... the limit of 25 searches was reached.`, `... the limit of 60 steps was reached.`, or `... the usage limit was reached.` A run that ended by submitting adds nothing.
- Fallback, when the last call gives no valid submit after two tries: the name is the question cut to 80 characters, the body is each source's title and digest in order under a note that the model did not write a report, and the stop note says so. A run that ends with no source is `Outcome(status="failed", reason="no_source")`.

- [ ] **Step 1: Write the failing tests** with `FakeModel(script=...)`, `FakeSearch`, `FakePageOpener`, and small fake `Control`, `Recorder`, `memory`, and `elapsed`:
  - happy path: a search, two pages, a submit with a note for one source. The outcome is `ready`, the two sources are in the order opened with titles and addresses from the pages, the unnoted source has the default line, the recorder counted every loop call and both digest calls and one search, and the digest calls had no tools.
  - Review Focus 1: a digest that says to open `https://attacker.example/?x=1`, and a scripted `open_page` for it, is refused; `opener.opened` has no such address; every digest call has `tools == ()`.
  - an address from a page's links can be opened; one never shown cannot; the same address with a fragment or a default port counts once and the second call returns the first digest.
  - the limits: after 20 sources the next step is the last call and the stop note ends with the stopped-because line; 25 searches; 60 steps; `elapsed` at 25 minutes gives the last call; `elapsed` at 30 minutes makes no model call and uses the fallback.
  - `cancelled`, `stopped`, `thread_gone` return `cancelled` with no model call after; `capped` makes one last call and ends `ready` with the usage-limit line.
  - Review Focus 4: no submit at all, a nudge then a last call then the fallback; a submit with a number that does not exist, a repeated number, an empty body, an 81 character name, and a null character are each refused and a later valid submit is accepted; a submit before any source is refused; a run with no source ends `failed` with reason `no_source`.
  - Review Focus 6 and the page errors: a `PageError` and a `SearchError` come back as tool results and count nothing (the search still counts as recorded); the run goes on.
  - knowledge: `read_knowledge` text is cut to 12,000 characters with the flag; the first read is a source of kind `file`, the second is not; `list_knowledge` and `search_knowledge` are not.
  - a model call that raises is passed to the recorder as `None` and the error propagates.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run ruff check . && uv run pytest -q`.
- [ ] **Step 5: Commit** `Add the research loop.`

---

### Task 6: Run research as a run, and save the report

Branch `research-loop`, after Task 5.

**Files:**
- Create: `backend/app/research_worker.py`
- Modify: `backend/app/scheduled.py` (kind switch, tick passes the kind), `backend/app/worker.py` (share the run handler), `backend/app/run.py` (`add_work_ms`), `backend/app/research.py` (`execute_research`), `docs/product/research.md`, `docs/product/failure.md`, `docs/product/models.md`, `docs/plan/decisions.md`, `docs/hand-in/report.md`, `docs/product/mvp-mlp.md`
- Test: `backend/tests/test_research_execute.py`, additions to `backend/tests/test_worker.py` and `backend/tests/test_scheduled.py`

**Interfaces:**
- Consumes: Tasks 1 to 5, and the existing `scheduled.execute`, `hold_working`, `finish_working`, `start_attempt`.
- Produces:

```python
# app/research.py
READY_LINE = 'Your report "{name}" is ready. Open Reports to read or download it.'
NO_SOURCE_LINE = "I could not open any source for this question."
FAILED_LINE = "The research failed."
MIN_RETRY_SECONDS = 3 * 60
def execute_research(run: RunRow, model: Model, search: Search | None = None,
                     opener: PageOpener | None = None) -> str
    # Returns "ready", "failed", "cancelled", "dead", "retry", or the run's real status when it was changed
    # meanwhile. With search or opener None it builds open_search() and a ChromePageOpener behind a SafeProxy.

# app/run.py
def add_work_ms(connection, run_id, ms: int) -> None
```

- `scheduled._execute`, after `start_attempt`, calls `execute_research(run, model)` when the run's kind is `research`. Everything before it (the lock, the stopped and capped check, the thread check) is shared. The tick's requeue passes the run's kind to `enqueue`.
- `execute_research`: builds the prompt with `_system_prompt(...)` plus a research line that holds the question and the tool rules; makes a `Control` that reads the run's status (`cancelled`), the account's stop and cap (`stopped`, `capped`), and the thread (`thread_gone`); a `Recorder` that writes one usage row per model call with `record_usage(... operation="research", run_id=...)` and one `record_search` per search, each in its own short transaction with no message id; `elapsed` is the `work_ms` already on the run plus the time since this attempt began; `memory` runs `operations.call(..., in_run=True)` for the three read tools with a fresh `Turn`, and refuses any other name.
- When the loop returns: `ready`: one transaction that takes `hold_working`, creates the report and its sources with `create_report`, writes the `READY_LINE` as an assistant message of kind `result` with the run id, sets `delivered_at`, and finishes the run `ready`. If `hold_working` is false the report is dropped and the run's real status is returned. `failed` with `no_source`: the run is finished `failed`, `NO_SOURCE_LINE` is written as a message of kind `result`, and it is not retried. `cancelled`: when the person cancelled it nothing more is written. For `thread_gone` the run is finished `cancelled` and nothing is written. For `stopped` the run is finished `cancelled` and the stopped sentence from chat is written as a message of kind `result`.
- Work time: at the end of every attempt, including a failed one, `add_work_ms` adds the time of the attempt. An exception from the loop is logged with the run id and the exception type only. When fewer than `MIN_RETRY_SECONDS` of the 30 minutes are left the run is finished `failed` with `FAILED_LINE`. When it was the fourth attempt the run is finished `dead` with `FAILED_LINE`. Otherwise `"retry"`.
- `research_worker.py` is a FastAPI app with `GET /health` and `POST /runs/{run_id}`, the same handler as the schedule worker, with no `/tick`. `app.main` imports neither worker.
- Docs: `research.md` says what is built, that reading stops at 25 minutes of the 30, how the token cap and the admin's stop differ, the search and step limits, and the no-source outcome. `failure.md` gains the research lines. `models.md` says a search is a usage row that does not count toward the token cap. `decisions.md` records the designer's calls from the spec. The report draft gains the research feature with the reasons from the spec's questions and answers: why our own search and Chrome, why the source list is a promise, why the model can open only addresses it was shown, why a proxy, why a digest, why a second service, and what leaves Folio. `mvp-mlp.md` item 14 is updated.

- [ ] **Step 1: Write the failing tests** in `test_research_execute.py` with `FakeModel`, `FakeSearch`, `FakePageOpener`, a real database, and a monkeypatched `enqueue`:
  - a full run: `execute` on a research run makes one report with the sources in order, one `READY_LINE` result message with the run id, `delivered_at` set, status `ready`, and usage rows for every model call and the search, each with the run id and no message id and written before the run finished (check the count from a second connection while the model is held on an event). A second `execute` of the same run returns `"duplicate"` and adds nothing.
  - Review Focus 5: a run cancelled by `cancel_research` during a step ends with no report and no line; a deleted thread ends `cancelled` with no report and no line; a stopped account ends `cancelled` with the stopped sentence once in the thread and no report; a capped account gets one last call, a report, and the usage-limit line in its stop note. Delivering the task twice writes one report.
  - a failing model: attempt 1 returns `"retry"` and adds its time to `work_ms`; with 28 minutes already in `work_ms` the failure is `failed` with `FAILED_LINE` and no retry; the fourth attempt is `dead`. Logs hold the run id and exception type and never the question, a page, or a report.
  - no source: `failed`, `NO_SOURCE_LINE`, no report, not retried.
  - a run that was marked dead while the loop worked drops its report, writes nothing, and returns `"dead"`.
  - `memory` answers only the three read tools: a scripted `write_knowledge` call is answered `unknown`.
  - `scheduled.execute` for a schedule run still behaves as before (the existing tests pass unchanged); the tick passes the kind to `enqueue` when it puts a research run back on its queue, and gives up on a stuck research run as it does for a schedule.
  - worker: `research_worker.app` has `/health` and `/runs/{id}` with the same status codes as the schedule worker, no `/tick`, and no session or thread route.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement,** then update the documents.
- [ ] **Step 4: Run** `uv run ruff check . && uv run pytest -q`, on the usual database and on a new empty one.
- [ ] **Step 5: Commit** `Run research as a run and save the report once.`

---

### Task 7: The Chrome image, the research service, and the pipeline

Branch `research-infra`.

**Files:**
- Create: `backend/Dockerfile.research`, `infra/research.tf`
- Modify: `infra/secrets.tf` (the search key secret), `infra/run.tf` and `infra/worker.tf` (environment for enqueueing), `infra/variables.tf`, `infra/outputs.tf`, `infra/github.tf`, `infra/README.md`, `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`

**Facts:**
- `backend/Dockerfile.research` starts from the same base as `backend/Dockerfile`, installs the `research` dependency group, runs `playwright install --with-deps chromium` as root into a path the app user can read, then drops to a non-root user. Its command is `uvicorn app.research_worker:app --host 0.0.0.0 --port ${PORT:-8080}`, with the same proxy flags as the API. The API image is unchanged.
- `infra/research.tf`, in the style of `infra/worker.tf`: a service account `folio-research`; read access to the database, OpenAI, and the new Tavily secrets only; a queue `folio-research` with 4 attempts, backoff from 10 to 300 seconds, and 3 dispatches at once; a Cloud Run service `folio-research` that is not public, with the image from a new variable `research_image`, a 30 minute timeout, 4 GiB of memory and 2 CPUs (to be measured), one request at a time per instance (`max_instance_request_concurrency = 1`), 0 to 3 instances, the second generation execution environment, the same VPC access and plain and secret environment as the API plus `TAVILY_API_KEY`, and only `folio-invoker` allowed to call it. Its address is built from the project number as the worker's is.
- The API's service account may put tasks on the research queue and act as `folio-invoker`, and the API service gets `RESEARCH_QUEUE`, `RESEARCH_URL`, and `INVOKER_ACCOUNT`. The worker's service account may put tasks on the research queue, and the worker gets the same three variables, because its tick puts a research run back on its queue.
- A secret `tavily-api-key` with no value in the repository. `infra/README.md` says the person adds the value, as for the OpenAI key, and gives the command.
- `ci.yml`: the `docker` job builds both images. A new job `browser` installs the `research` group and Chromium on the runner and runs `pytest tests/test_browser.py`. The other tests stay as they are.
- `deploy.yml`: build and push the research image with the commit tag, and deploy it to `folio-research` after the worker step, skipping with one printed line when the service does not exist yet, as the worker step does. The step changes the image only.

- [ ] **Step 1: Write** the Dockerfile, the Terraform, and the workflow changes.
- [ ] **Step 2: Run** from `infra/`: `terraform fmt -recursive`, `terraform fmt -check -recursive`, `terraform init -backend=false && terraform validate`. From `backend/`: `docker build -f Dockerfile.research .` if Docker is available, and say in the report whether it built. Expected: validate passes. Never run `plan` or `apply`.
- [ ] **Step 3: Commit** `Describe the research service, its queue, and its image.`

---

### Task 8: The Reports screen

Branch `research-web`. Build against the route shapes in Task 2.

**Files:**
- Create: `frontend/app/reports.tsx`
- Modify: `frontend/lib/api.ts`, `frontend/app/folio-app.tsx`, `frontend/app/globals.css`, `docs/product/screens.md`, `README.md`

Behavior:
- `api.ts`: `ReportItem = { id; name; question; created_at }`, `ReportSource = { position; kind; title; url: string | null; used_for }`, `ReportDetail = ReportItem & { body; stop_note; sources: ReportSource[] }`, `listReports()`, `getReport(id)`, and `reportDownloadUrl(id)` (the `apiBase()` form plus `/reports/{id}/download`). Parse every field, never cast.
- A "Reports" button in the account block beside Settings, with `DocumentTextIcon` from `@heroicons/react/24/outline`. It opens the Reports screen in place of the chat area, as Settings does. Opening Reports closes Settings and the profile panel, and the reverse.
- The screen has a list of reports, newest first, each with its name, question, and time, and shows the first one by default. A report shows its name as a heading, "Question: ..." under it, the body rendered as Markdown with the existing `Markdown` component, a section "How this research stopped" with the stop note, and a numbered "Sources" list. A page source is a link that opens in a new tab with `rel="noopener noreferrer"`, followed by its used-for line. A file source is plain text with its used-for line. A "Download" link has the `download` attribute and points at `reportDownloadUrl`. "Back to chat" closes it.
- The list reloads when a chat turn ends and when the open thread receives a new message (the places that raise `reloadKey`), so a report that has just finished appears without a refresh. Empty state, exactly: "No reports yet. Ask Folio to research something."
- A 401 returns to the public page and a `down` result shows the system-down notice, each reported at most once per load. A failed single report shows a sentence in place.
- `toolStatus` gains: `start_research` "Starting research", `cancel_research` "Cancelling research", `list_research_runs` "Looking at research", `list_reports` "Looking at reports", `rename_report` "Renaming a report", `delete_report` "Deleting a report".
- No control creates, renames, or deletes a report. The agent does that. Every control has a label and works by keyboard. No sideways scroll at 360 px. Use the existing palette. `screens.md` says what the screen shows. The README status table moves research to "Present".

- [ ] **Step 1 to 3:** the API functions, the screen, the wiring and the tool sentences.
- [ ] **Step 4: Run** `npm run lint && npm run typecheck && npm run build`. Expected: pass.
- [ ] **Step 5: Commit** `Add the Reports screen to read and download a report.`
