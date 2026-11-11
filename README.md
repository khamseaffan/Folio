# Sakana

Personal AI agent. Each invited person has a private account, and their own memory, files, research, and scheduled runs. The platform holds the model keys.

## Product

- Conversation with the platform model. The current model is Gemini. The person cannot change it.
- Deep research reports the person can leave and reopen.
- Voice, file, and read-only Google Drive input.
- Long-term memory used in later answers.
- Scheduled runs that leave a message or a report in the app.
- Accounts: create, log in, log out, delete.

## Stack

| Layer | Choice |
| --- | --- |
| Web | Next.js on Vercel |
| API and worker | Python, FastAPI, Cloud Run |
| Data | Cloud SQL for Postgres, Cloud Storage |
| Sign-in | Firebase Authentication |
| Jobs | Cloud Tasks, Cloud Scheduler |
| Secrets | Cloud KMS for model keys. Secret Manager for app secrets. |
| Infrastructure | Terraform, Google Cloud, `us-central1` |
| CI | GitHub Actions |

## Status

| Present | Not built |
| --- | --- |
| API health check and tests | Accounts, chat, memory, research, files, schedules |
| Web app shell | Google Cloud and Vercel deploy |

## Run

API, from `backend/`:

```bash
uv sync --all-groups
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

Health check: `GET /health` returns `{"status":"ok"}`.

Install the commit hook once: `uvx pre-commit install`.

## Docs

- [Decisions](docs/decisions.md)
- [Architecture](docs/architecture.md)
- [System map](docs/diagrams/system-map.html)
- [Access](docs/product/access.md)
- [Control](docs/product/control.md)
- [Models](docs/product/models.md)
- [Conversations](docs/product/conversations.md)
- [Profile](docs/product/profile.md)
- [Abilities](docs/product/abilities.md)
- [Files](docs/product/files.md)
- [Drive](docs/product/drive.md)
- [Research](docs/product/research.md)
- [Schedules](docs/product/schedules.md)
- [Browser](docs/product/browser.md)
- [Submit confirmations](docs/product/confirmations.md)
- [Later](docs/product/later.md)
- [How docs are written](docs/conventions.md)
