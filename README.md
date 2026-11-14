# Folio

[![Vercel](https://img.shields.io/github/deployments/khamseaffan/Folio/Production?label=vercel&logo=vercel)](https://folio.khamseaffan.com)
[![CI](https://github.com/khamseaffan/Folio/actions/workflows/ci.yml/badge.svg)](https://github.com/khamseaffan/Folio/actions/workflows/ci.yml)

Personal AI agent. Each person has a private account, and their own memory, files, research, and scheduled runs. The platform holds the model keys.

The product rules are in [docs/product](docs/product). The record picture is [docs/diagrams/schema.html](docs/diagrams/schema.html).

## Stack

| Layer | Choice |
| --- | --- |
| Web | Next.js on Vercel |
| API and worker | Python 3.13, FastAPI, Cloud Run |
| Data | Cloud SQL for PostgreSQL 18, Cloud Storage |
| Sign-in | Firebase Authentication |
| Jobs | Cloud Tasks, Cloud Scheduler |
| Secrets | Cloud KMS for model keys and integration secrets. Secret Manager for app secrets. |
| Infrastructure | Terraform for Google Cloud, `us-central1`. The website is on Vercel. |
| CI | GitHub Actions |

## Status

Live at https://folio.khamseaffan.com.

| Present | Not built |
| --- | --- |
| Sign-in with an email allowlist, sessions, a display name, and a settings screen with the sessions, the token totals, and account deletion | Memory, research, files, schedules |
| Threads with create, rename, and delete by a control or by the agent, and a streamed chat turn with usage rows | Drive, the browser, voice |
| Deployment: Terraform on Google Cloud, Vercel, and CI/CD | Admin limits, and the provider and model selection |

## Deploy

- **Pull request:** GitHub Actions runs `backend`, `frontend`, `terraform` and `docker`. All four must pass to merge into `main`.
- **Merge to `main`:** Vercel builds the website from its GitHub connection. `deploy.yml` then builds the API image tagged with the commit, runs the migration job, deploys Cloud Run, and checks that `https://folio.khamseaffan.com/api/ready` answers. Only the newest commit on `main` is deployed.
- **Infrastructure:** `infra/` is applied by a person after reading the plan. See `infra/README.md`.
- **Same origin:** the site forwards `/api/*` to the API, so the session cookie works.

## Run

API, from `backend/`:

```bash
uv sync --all-groups
uv run python -m app.migrate
uv run uvicorn app.main:app --reload
uv run ruff check .
uv run pytest
```

Web, from `frontend/`:

```bash
npm ci
npm run dev
npm run lint
npm run typecheck
npm run build
```

`app.migrate` creates and updates the tables. A request creates a table only when it is missing, so run `app.migrate` after a change to a file in `backend/migrations`.

Health check: `GET /health` returns `{"status":"ok"}`.

Install the commit hook once: `uvx pre-commit install`.

## Docs

- [Decisions](docs/decisions.md)
- [Architecture](docs/architecture.md)
- [System map](docs/diagrams/system-map.html)
- [Access](docs/product/access.md)
- [Control](docs/product/control.md)
- [Screens](docs/product/screens.md)
- [Failure](docs/product/failure.md)
- [Records](docs/product/records.md)
- [Schema picture](docs/diagrams/schema.html)
- [Models](docs/product/models.md)
- [Conversations](docs/product/conversations.md)
- [Profile](docs/product/profile.md)
- [Abilities](docs/product/abilities.md)
- [Files](docs/product/files.md)
- [Drive](docs/product/drive.md)
- [Research](docs/product/research.md)
- [Schedules](docs/product/schedules.md)
- [Browser](docs/product/browser.md)
- [Voice](docs/product/voice.md)
- [Submit confirmations](docs/product/confirmations.md)
- [Later](docs/product/later.md)
- [MVP and MLP](docs/product/mvp-mlp.md)
- [Hand-in report, draft 1](docs/hand-in/report.md)
- [How docs are written](docs/conventions.md)
