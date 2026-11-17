# Folio

[![CI](https://github.com/khamseaffan/Folio/actions/workflows/ci.yml/badge.svg)](https://github.com/khamseaffan/Folio/actions/workflows/ci.yml)
[![Vercel](https://img.shields.io/github/deployments/khamseaffan/Folio/Production?label=vercel&logo=vercel)](https://folio.khamseaffan.com)

A private AI assistant. Talk to it in chat, ask for a researched report with cited sources, let it remember how you like to work, and have it do tasks on a schedule while you are away.

**Live:** https://folio.khamseaffan.com (invite only) · [Privacy policy](https://folio.khamseaffan.com/privacy)

The agent is written from scratch for this project: its own loop, tools, memory, and limits. It does not host an existing agent.

## What it does

- **Chat.** Threads, replies that stream as they are written, a stop button, and no limit on message length.
- **Deep research.** Ask for a report. Folio searches the web, opens up to 20 pages in its own browser, and writes a report with a numbered source list. A banner in the thread shows what it is reading while it works.
- **Memory.** It keeps notes about you as Markdown and reads your profile on every message. You read them on the Profile page and change them by telling Folio.
- **Schedules.** Say when, in the chat. A worker runs the task while you are away and the result lands in the thread. A schedule can start research.
- **Private accounts.** Sign in with Google or an email link. Only approved addresses can join. Each account has a token limit, and deleting an account removes its data at once.

## Status

| Area | State |
| --- | --- |
| Accounts, chat, memory, schedules | Live |
| Deep research and reports, with a progress banner | Live |
| Files: upload, read by the model, written by the assistant, download | Built and tested. Not yet tried live. |
| Voice input | Built and tested. Not yet tried live. |
| Google Drive import | Built and tested. Not yet tried live. |
| Web access for scheduled tasks | Built and tested. Not yet tried live. |
| Confirmed website sends, choosing the model from settings, alerts | Not built |

The full requirement list is in [docs/product/mvp-mlp.md](docs/product/mvp-mlp.md).

## How it fits together

```mermaid
flowchart LR
  person[Person] --> web["Website<br/>Next.js on Vercel"]
  web -->|"/api/*"| api["API<br/>FastAPI on Cloud Run"]
  api --> sql[("Cloud SQL<br/>PostgreSQL")]
  api --> bucket[("Cloud Storage<br/>files")]
  api --> tasks["Cloud Tasks"]
  clock["Cloud Scheduler<br/>every minute"] --> worker
  tasks --> worker["Worker<br/>schedules"]
  tasks --> research["Research service<br/>with Chrome"]
  worker --> sql
  research --> sql
  api --> model["Model provider"]
  worker --> model
  research --> model
```

A short chat is answered on the request. Research and scheduled runs go on a queue, and a worker picks them up, so a half-hour report does not hold a connection open. The database is on a private address and only the services reach it. [docs/architecture.md](docs/architecture.md) has the full diagram.

## Repository

| Folder | What is in it |
| --- | --- |
| `backend/` | Python 3.13 API, worker, and research service (FastAPI) |
| `frontend/` | The Next.js website |
| `infra/` | Terraform for Google Cloud. See [infra/README.md](infra/README.md). |
| `docs/` | Product rules, decisions, diagrams, and the hand-in report |

## Run it locally

You need [uv](https://docs.astral.sh/uv/), Node 20 or newer, and a PostgreSQL database. The API looks for `postgresql://folio:folio@localhost:5432/folio` unless `DATABASE_URL` says otherwise. For example, with Docker:

```bash
docker run --name folio-db -e POSTGRES_USER=folio -e POSTGRES_PASSWORD=folio -e POSTGRES_DB=folio -p 5432:5432 -d postgres:18
```

**API**, from `backend/`:

```bash
uv sync --all-groups
uv run python -m app.migrate
uv run uvicorn app.main:app --reload
```

**Worker**, from `backend/`. It serves the one-minute tick and the run route.

```bash
uv run uvicorn app.worker:app --port 8001
curl -X POST localhost:8001/tick
```

**Website**, from `frontend/`:

```bash
npm ci
npm run dev
```

**Settings.** The code reads these from the environment.

| Variable | Used for |
| --- | --- |
| `OPENAI_API_KEY` | The chat model. Without it the API gives a fixed placeholder reply. |
| `TAVILY_API_KEY` | Web search for research |
| `DATABASE_URL` | The database, if it is not the default above |
| `FIREBASE_SERVICE_ACCOUNT_PATH` | Sign-in on the API side |
| `NEXT_PUBLIC_FIREBASE_*` | Sign-in on the website side, in `frontend/.env.local` |

`app.migrate` creates and updates the tables. A request creates a table only when it is missing, so run `app.migrate` again after a change to a file in `backend/migrations`.

## Checks

```bash
uv run ruff check .     # backend, from backend/
uv run pytest           # backend, from backend/
npm run lint            # frontend, from frontend/
npm run typecheck       # frontend, from frontend/
```

Install the commit hook once with `uvx pre-commit install`. It runs the same checks before a commit.

## Deploy

- **Pull request:** GitHub Actions runs `backend`, `frontend`, `terraform`, and `docker`. All four must pass before a merge into `main`.
- **Merge to `main`:** Vercel builds the website. `deploy.yml` builds the images tagged with the commit, runs the migration job, deploys the Cloud Run services, and checks that the live site answers. Only the newest commit is deployed.
- **Infrastructure:** `infra/` is applied by a person after reading the plan. CI only checks the formatting and the syntax.
- **Same origin:** the website forwards `/api/*` to the API, so the session cookie is first-party.

## Docs

**Start here**
- [Goal](Goal.md), the requirement list
- [Decisions](docs/decisions.md)
- [Architecture](docs/architecture.md) and the [system map](docs/diagrams/system-map.html)
- [Hand-in report](docs/hand-in/report.md)

**Product rules**
- Accounts and control: [Access](docs/product/access.md), [Control](docs/product/control.md), [Models](docs/product/models.md)
- Using Folio: [Screens](docs/product/screens.md), [Conversations](docs/product/conversations.md), [Profile](docs/product/profile.md), [Abilities](docs/product/abilities.md)
- Work it does: [Research](docs/product/research.md), [Schedules](docs/product/schedules.md), [Files](docs/product/files.md), [Drive](docs/product/drive.md), [Browser](docs/product/browser.md), [Voice](docs/product/voice.md), [Submit confirmations](docs/product/confirmations.md)
- When things go wrong: [Failure](docs/product/failure.md)
- Data: [Records](docs/product/records.md) and the [schema picture](docs/diagrams/schema.html)
- What is next: [MVP and MLP](docs/product/mvp-mlp.md), [Later](docs/product/later.md)

**Working on the code**
- [Working rules](AGENTS.md)
- [How docs are written](docs/conventions.md)
