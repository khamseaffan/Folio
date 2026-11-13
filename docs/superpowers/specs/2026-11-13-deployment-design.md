# Deployment and CI/CD: the first live URL

Stage 2.5 in `docs/decisions.md`. Date: 2026-11-13.

## Intent

Put what exists (sign-in, threads, one streamed chat turn) on a public URL, and make every merge to `main` deploy it. The task in `Goal.md` requires Google Cloud, infrastructure as code, CI and automated deployment, and a URL to submit. This is the smallest slice that meets those and leaves room to add features onto a running system.

## Goals

- A public HTTPS URL where an allowed email signs in and holds a chat.
- All Google Cloud resources described in Terraform, and applied from it.
- A merge to `main` that passes checks builds the API image, migrates the database, deploys the API, and ships the website.
- No secret in the repo (the repo is public).

## Non-goals

- Resources for features that do not exist yet: Cloud Storage, Cloud Tasks, Cloud Scheduler, the worker service, KMS envelope keys.
- A static outbound IP or Cloud NAT (nothing outside Google needs allowlisting). Regional database failover, a second region, autoscaling tuning, alerts, dashboards.
- A staging environment. One live environment.

## Anti-goals

- A deploy that works only from one laptop, or only by clicking in a console.
- A key file for Google, GitHub, or Firebase stored anywhere.
- Signing in that works locally and fails live (the cookie problem below).

## Approaches considered

- **A. Every resource in the docs, up front.** Most complete. KMS, Tasks, Scheduler and Storage would sit unused and unreviewed until their features exist.
- **B. Thin slice (chosen).** Only what chat needs. Other resources are added with the stage that uses them. Same IaC and CD story, live soonest.
- **C. Console clicks, Terraform later.** Rejected. It fails the IaC requirement and cannot be reproduced.
- Database: Cloud SQL, not Supabase. `Goal.md` says to use Google Cloud for infrastructure. Cloud SQL keeps the data inside it. Cost is small at 25 users.

## Constraints

- Project `khamsebot`, region `us-central1`, billing on. About 25 users. Public URL: `https://folio.khamseaffan.com` (custom domain on Vercel, decided by the person).
- Postgres 18, Python 3.13, FastAPI. The API reads `DATABASE_URL`, `FRONTEND_ORIGIN`, `OPENAI_API_KEY`, and Firebase credentials from the environment.
- The repo `khamseaffan/Folio` is public.
- Project rules in `CLAUDE.md`: no AI attribution anywhere, no em dashes, YAGNI.

## Design

### Shape

```
Browser -> Vercel (Next.js)  --/api/* rewrite-->  Cloud Run API  -->  Cloud SQL (Postgres 18)
                                                       |
                                                       +--> Secret Manager (OpenAI key, DB password)
Browser -> Firebase Authentication (sign-in), API verifies the ID token
GitHub Actions --(Workload Identity Federation)--> Artifact Registry, Cloud Run, Terraform
```

### Same-origin cookie (my call, matches D11)

Today the web app calls the API directly and the session cookie is `SameSite=Lax`, so a Vercel page and a `run.app` API would not share it. The web app calls relative `/api/...` paths, and Vercel rewrites them to the Cloud Run URL. The browser sees one origin. `FRONTEND_ORIGIN` on the API is set to the Vercel URL so the origin check passes. The frontend change is small: the API base becomes empty in production, and a rewrite is added to `next.config.ts`.

### Terraform (in `infra/`)

- State in a Cloud Storage bucket, created once by a short bootstrap script (the one manual step that cannot be Terraform).
- Enable the needed APIs (Run, SQL Admin, Compute, Service Networking, Artifact Registry, Secret Manager, IAM Credentials).
- Artifact Registry repository for the API image.
- A VPC network and one subnet in `us-central1`, plus private services access. Cloud SQL, Postgres 18, smallest shared-core tier, single zone, **private IP only** (no public IP, no authorized networks), TLS required, a generated password stored in Secret Manager. Regional failover is left out on cost. Only the Cloud Run API and the migration job connect to it, over Direct VPC egress, which has no connector fee. Vercel never connects to the database.
- Cloud Run API service: runtime service account, Direct VPC egress into the subnet, `DATABASE_URL` (private IP host, `sslmode=require`) and `OPENAI_API_KEY` from Secret Manager, `FRONTEND_ORIGIN` from a variable, public invoker (the API enforces its own sessions), one to three instances.
- Workload Identity Federation: a pool and provider that trust only `khamseaffan/Folio` on `main`, and a deploy service account with the minimum roles.
- The OpenAI key secret is created empty by Terraform. The value is added once with `gcloud secrets versions add`, reading `.env`, so it never enters Terraform state or GitHub.

### API container and migrations

- A `backend/Dockerfile` (Python 3.13 slim, uv, non-root, listens on `$PORT`).
- A `python -m app.migrate` entry that runs each module's existing idempotent `apply()` in dependency order. It runs in CI before the new revision takes traffic, as a Cloud Run job from the same image. Alembic is not introduced.
- Firebase: when no key file is configured, the API uses the Cloud Run service identity (Application Default Credentials). The key-file path still works locally. Test: the existing sign-in tests stay green, and a new test covers the fallback choice.
- Cookies: `secure` is derived from the request scheme. Behind Cloud Run and the Vercel rewrite the scheme arrives as `http` unless forwarded headers are trusted, so the API starts uvicorn with proxy headers trusted. Test: a request carrying `X-Forwarded-Proto: https` gets a `Secure` cookie.

### Vercel

- Project created and linked from `frontend/` with the CLI, deployed by GitHub Actions with a Vercel token. Production env vars: the Firebase web config (public values) and nothing else.
- The custom domain `folio.khamseaffan.com` is added to the Vercel project. The person adds the DNS record Vercel shows (a CNAME for `folio`) at the registrar of `khamseaffan.com`. `FRONTEND_ORIGIN` and the Firebase Authorized domains use this domain, and the `*.vercel.app` address is added to Authorized domains too so previews can sign in.

### GitHub Actions

- `ci.yml` stays as the pull request check, plus `terraform fmt -check` and `terraform validate`, and a `docker build` of the API.
- A new `deploy.yml` on push to `main`, after CI passes: authenticate to Google with Workload Identity Federation, build and push the image tagged with the commit, run the migration job, deploy the Cloud Run revision, then deploy the website to Vercel. A smoke test calls `/ready` on the live URL and fails the run if it is down.
- Terraform is applied by a separate manual workflow (`workflow_dispatch`), not on every merge. Infrastructure changes are rare and should be reviewed, and a plan is shown first.
- Branch protection on `main` requires the CI checks.

### Order of work

1. Bootstrap the state bucket, then Terraform for APIs, registry, Cloud SQL, secrets, Cloud Run (with a placeholder image), Workload Identity Federation. Apply.
2. API changes: Dockerfile, migrate entry, ADC fallback, proxy headers. Tests first.
3. Build and push the first image by hand once, deploy, check `/ready`.
4. Frontend rewrite and relative API base, Vercel project, env vars, first deploy.
5. Firebase authorized domain, end-to-end sign-in and one chat turn on the live site.
6. `deploy.yml`, branch protection, and a merge that deploys on its own.
7. Update `README.md` status and `docs/architecture.md`, and add the live URL to the hand-in report.

Each step is its own pull request, matching how the repo has been built.

## What I need from the person during the build

- Approve each Terraform plan before it is applied.
- Add the DNS record for `folio.khamseaffan.com`, and add the domain to Firebase Authorized domains if the API cannot.
- A GitHub token scope check: `gh` must be allowed to set repo secrets and branch protection.

## Left to the builder

Exact machine size and Cloud Run limits within the ranges above, resource names, the Terraform module layout, the Python base image tag, and how the smoke test retries.

## Risks

- Private IP needs the VPC and private services access created first, and Cloud SQL can take 10 to 15 minutes to create. Nobody can reach the database from a laptop, so inspection goes through a Cloud Run job.
- A Firebase sign-in popup depends on the domain being in Authorized domains, and the custom domain needs its DNS record and certificate to finish before it works.
- Cold starts on a scaled-to-zero API make the first request slow. The person chose not to keep an instance warm, on cost. The hand-in report says so.
