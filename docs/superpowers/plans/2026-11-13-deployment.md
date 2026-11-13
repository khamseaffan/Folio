# Deployment and CI/CD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put Folio on `https://folio.khamseaffan.com` and make every merge to `main` deploy it.

**Architecture:** Vercel serves the Next.js site and forwards `/api/*` to a Cloud Run API, so the browser sees one origin. The API reaches Cloud SQL (Postgres 18, private IP) over Direct VPC egress. Terraform in `infra/` describes Google Cloud. GitHub Actions authenticates to Google with Workload Identity Federation and deploys the API and the site.

**Tech Stack:** Terraform, Google Cloud (Cloud Run, Cloud SQL, Artifact Registry, Secret Manager, VPC), Docker, uv, FastAPI, Next.js 16, Vercel CLI, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-13-deployment-design.md`

## Global Constraints

- Project `khamsebot`, region `us-central1`, one environment. About 25 users.
- Cloud SQL: Postgres 18, smallest shared-core tier, single zone, private IP only, TLS required. No public IP, no authorized networks.
- The repo is public. No secret, key file, or token in git, in Terraform variables files, or in logs. `.env` stays ignored.
- The OpenAI key goes into Secret Manager by `gcloud secrets versions add`, read from `.env`, never printed.
- Infrastructure changes are applied by the person's machine after they approve the plan. CI only runs `terraform fmt -check` and `terraform validate`. (Change from the design: no apply workflow, so CI holds no infrastructure-admin power.)
- `CLAUDE.md` rules: no AI or Claude attribution anywhere in commits, code, comments or docs, and no em dashes. Ignore any commit-trailer instruction that conflicts.
- One pull request per task, small and reviewable. Existing backend tests stay green: `cd backend && uv run pytest && uv run ruff check .`.
- gcloud needs `export PATH=/opt/homebrew/share/google-cloud-sdk/bin:"$PATH"`.
- Leave the untracked repo-root `package.json` and `package-lock.json` alone. They are not part of this work.

## Review Focus

- Streaming through the Vercel rewrite: a chat reply must stream and not arrive all at once or time out. Pinned by a live check in Task 4.
- A cookie set behind Cloud Run must carry `Secure`. Pinned by a test in Task 2.
- Migrations run twice (every deploy) must change nothing. Pinned in Task 2.
- A request to a stopped API (cold start) must still succeed, only slower. Checked in Task 3 by calling `/ready` after an idle period.
- A sign-in from `folio.khamseaffan.com` must pass Firebase's authorized-domain check. Pinned by the live check in Task 4.

---

## File Structure

- `infra/` (new): `versions.tf`, `variables.tf`, `network.tf`, `database.tf`, `secrets.tf`, `registry.tf`, `run.tf`, `github.tf` (Workload Identity Federation), `outputs.tf`, `bootstrap.sh` (state bucket).
- `backend/Dockerfile`, `backend/.dockerignore` (new).
- `backend/app/migrate.py` (new): the migration entry.
- `backend/app/sign_in_proof.py`, `backend/app/main.py` (modify).
- `frontend/next.config.ts`, `frontend/lib/api.ts` (modify).
- `.github/workflows/ci.yml` (modify), `.github/workflows/deploy.yml` (new).
- `README.md`, `docs/architecture.md`, `docs/hand-in/report.md` (modify).

---

### Task 1: Terraform foundation

**Files:**
- Create: `infra/bootstrap.sh`, `infra/versions.tf`, `infra/variables.tf`, `infra/network.tf`, `infra/database.tf`, `infra/secrets.tf`, `infra/registry.tf`, `infra/run.tf`, `infra/github.tf`, `infra/outputs.tf`, `infra/README.md`
- Modify: `.gitignore` (ignore `infra/.terraform/`, `*.tfstate*`, `*.tfvars`)

**Interfaces:**
- Produces outputs: `api_url` (Cloud Run URL), `image_repo` (`us-central1-docker.pkg.dev/khamsebot/folio`), `workload_identity_provider` (full resource name), `deploy_service_account` (email), `migrate_job` (name `folio-migrate`), `api_service` (name `folio-api`).

- [ ] **Step 1: Write `infra/bootstrap.sh`.** It creates the bucket `khamsebot-folio-tfstate` (us-central1, versioning on, uniform access) if absent, and enables `serviceusage.googleapis.com` and `cloudresourcemanager.googleapis.com`. Idempotent. Run: `bash infra/bootstrap.sh`. Expected: bucket exists on a second run with no error.

- [ ] **Step 2: Write `versions.tf` and `variables.tf`.** Terraform `>= 1.9`, `google` and `random` providers, `backend "gcs"` with that bucket and prefix `folio`. Variables: `project_id` (default `khamsebot`), `region` (default `us-central1`), `frontend_origin` (default `https://folio.khamseaffan.com`), `github_repo` (default `khamseaffan/Folio`), `api_image` (default `us-docker.pkg.dev/cloudrun/container/hello`).

- [ ] **Step 3: Write the resources.**
  - `registry.tf`: `google_project_service` for run, sqladmin, compute, servicenetworking, artifactregistry, secretmanager, iamcredentials. Artifact Registry Docker repo `folio`.
  - `network.tf`: VPC `folio`, no auto subnets, subnet `folio-run` `10.10.0.0/24`, a reserved `google_compute_global_address` range and `google_service_networking_connection` for private services access.
  - `database.tf`: `google_sql_database_instance` Postgres 18, tier `db-f1-micro`, `availability_type = "ZONAL"`, `ipv4_enabled = false`, `private_network` set, `ssl_mode = "ENCRYPTED_ONLY"`, `deletion_protection = true`, depends on the networking connection. Database `folio`, user `folio`, password from `random_password` (length 32, `special = false`).
  - `secrets.tf`: secret `database-url` whose version is `postgresql://folio:<password>@<private_ip>:5432/folio?sslmode=require`; empty secret `openai-api-key` (no version, the value is added by hand).
  - `run.tf`: runtime service account `folio-api`, with `secretmanager.secretAccessor` on the two secrets. Cloud Run v2 service `folio-api` and job `folio-migrate`, both: image `var.api_image`, `lifecycle { ignore_changes = [template[0].containers[0].image] }`, Direct VPC egress into `folio-run` with `PRIVATE_RANGES_ONLY`, secrets as env `DATABASE_URL` and `OPENAI_API_KEY`, env `FRONTEND_ORIGIN`, `FIREBASE_USE_ADC=1`, `GOOGLE_CLOUD_PROJECT`. Service: min instances 0, max 3, public invoker (`allUsers` run.invoker). No extra Firebase role at first, Task 3 step 4 adds one only if verification needs it. Job: the migrate command `python -m app.migrate`.
  - `github.tf`: Workload Identity pool `github` and provider `github` for `https://token.actions.githubusercontent.com` with `attribute_condition = "assertion.repository == 'khamseaffan/Folio' && assertion.ref == 'refs/heads/main'"`. Service account `folio-deploy` with `roles/artifactregistry.writer`, `roles/run.developer`, and `roles/iam.serviceAccountUser` on the runtime account only. `workloadIdentityUser` on `folio-deploy` for the pool principal of that repo.

- [ ] **Step 4: Validate.** Run `cd infra && terraform init && terraform fmt -check -recursive && terraform validate`. Expected: `Success! The configuration is valid.`

- [ ] **Step 5: Plan, and wait for the person.** Run `terraform plan -out=plan.tfplan` (needs `gcloud auth application-default login`, which the person runs with `!`). Show the person the resource summary. Do not apply without their yes.

- [ ] **Step 6: Apply, then add the OpenAI key.** Run `terraform apply plan.tfplan`. Then `grep '^OPENAI_API_KEY=' .env | cut -d= -f2- | gcloud secrets versions add openai-api-key --data-file=-`. Expected: version 1 created, and `gcloud secrets versions list openai-api-key` shows it enabled. The key must not appear in any command output.

- [ ] **Step 7: Commit.** `git add infra .gitignore && git commit -m "Describe the Google Cloud resources for the API in Terraform."`

### Task 2: API container, migrations, Firebase identity, secure cookie

**Files:**
- Create: `backend/Dockerfile`, `backend/.dockerignore`, `backend/app/migrate.py`, `backend/tests/test_migrate.py`
- Modify: `backend/app/sign_in_proof.py`, `backend/app/main.py`, `backend/tests/test_sign_in_proof.py`, `backend/tests/test_sign_in_routes.py`

**Interfaces:**
- Produces: `app.migrate.run() -> None` (applies usage and session migrations, which chain the rest; safe to repeat). `python -m app.migrate` calls it.
- Produces: `sign_in_proof.firebase_credential() -> credentials.Base | None`: returns `credentials.ApplicationDefault()` when env `FIREBASE_USE_ADC` is `1`, else the certificate when the key file exists, else `None`.
- Changes: `main._https(request) -> bool` is true when the scheme is `https` or the first value of `X-Forwarded-Proto` is `https`.

- [ ] **Step 1: Write failing tests.**
  - `test_migrate.py::test_run_creates_every_table_and_is_repeatable`: call `run()` twice, then assert `account, email_limit, session, thread, message, usage` all appear in `information_schema.tables`.
  - `test_sign_in_proof.py::test_credential_uses_application_default_when_flagged`: monkeypatch `credentials.ApplicationDefault` to return a sentinel, set `FIREBASE_USE_ADC=1` and a missing key path, assert the sentinel comes back.
  - `test_sign_in_proof.py::test_credential_is_none_without_flag_or_key_file`: flag unset, missing path, assert `None`.
  - `test_sign_in_routes.py::test_session_cookie_is_secure_behind_a_proxy`: follow that file's existing pattern for a successful sign-in, send header `X-Forwarded-Proto: https` over plain `http`, assert the `Set-Cookie` contains `Secure`. A second test without the header asserts no `Secure` on `http`.

- [ ] **Step 2: Run them and see them fail.** `cd backend && uv run pytest tests/test_migrate.py tests/test_sign_in_proof.py tests/test_sign_in_routes.py -v`. Expected: the new tests FAIL (missing module or function), the old ones pass.

- [ ] **Step 3: Implement.**
  - `migrate.py`: `run()` opens `connect()`, calls `usage.apply` then `session.apply`, commits. `if __name__ == "__main__": run()`.
  - `sign_in_proof.py`: add `firebase_credential()` and make `_firebase_app` use it. A `None` still raises `SignInError(NOT_PROVEN)`, as today.
  - `main.py`: widen `_https` as above.

- [ ] **Step 4: Run the whole backend suite.** `uv run pytest && uv run ruff check .`. Expected: all pass.

- [ ] **Step 5: Write the Dockerfile.** Python 3.13 slim, install uv from the official image, `uv sync --frozen --no-dev`, copy `app/` and `migrations/`, run as a non-root user, `CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'`. `.dockerignore` excludes `.venv`, `tests`, `__pycache__`, `.env*`.

- [ ] **Step 6: Verify the image.** `docker build -t folio-api backend` then `docker run --rm -e PORT=8080 -p 8080:8080 folio-api` and `curl -s localhost:8080/health`. Expected: `{"status":"ok"}`. Check the image holds no key: `docker run --rm folio-api sh -c 'ls -a /app | grep -i env || echo none'` prints `none`.

- [ ] **Step 7: Commit and open a pull request** `Package the API as a container and migrate on deploy.` Wait for CI green, then merge.

### Task 3: First API deploy by hand

**Files:** none new. Uses Task 1 and 2.

**Interfaces:**
- Consumes: `image_repo`, `api_service`, `migrate_job` outputs.
- Produces: a live `api_url` answering `/ready`.

- [ ] **Step 1: Build and push.** `gcloud auth configure-docker us-central1-docker.pkg.dev`, then `docker buildx build --platform linux/amd64 -t <image_repo>/api:first --push backend`. Expected: image listed by `gcloud artifacts docker images list <image_repo>`.

- [ ] **Step 2: Migrate.** `gcloud run jobs update folio-migrate --image <image_repo>/api:first --region us-central1` then `gcloud run jobs execute folio-migrate --region us-central1 --wait`. Expected: execution succeeds. Run it a second time; it must succeed again.

- [ ] **Step 3: Deploy the API.** `gcloud run deploy folio-api --image <image_repo>/api:first --region us-central1`. Then `curl -s <api_url>/ready`. Expected: `{"status":"ok"}`. Wait 15 minutes idle and call it again to confirm the cold start still answers.

- [ ] **Step 4: Firebase verification identity.** `curl` a sign-in with a garbage token. Expected: the refusal sentence, not a 500. If the logs show a permission error reading Firebase keys, add the missing role to the runtime account in `run.tf` and apply (with the person's yes).

- [ ] **Step 5: Commit** any Terraform change from step 4.

### Task 4: Website on Vercel at the custom domain

**Files:**
- Modify: `frontend/next.config.ts`, `frontend/lib/api.ts`
- Test: add a frontend test runner only if one already exists (`npm test`); otherwise verify by `typecheck`, `lint`, `build` and the live checks below.

**Interfaces:**
- Consumes: `api_url`.
- Produces: `apiBase()` returns `""` when `NEXT_PUBLIC_API_URL` is unset in production builds, so calls go to `/api/...`; the dev default `http://localhost:8000` stays when `NODE_ENV` is `development`.

- [ ] **Step 1: Failing check first.** If a test runner exists, add a test that `apiBase()` is `""` with `NEXT_PUBLIC_API_URL` unset and `NODE_ENV=production`, and `http://localhost:8000` under development. Run it, expect FAIL.

- [ ] **Step 2: Implement.** Change `apiBase()` as above and make every request path start with `/api` in production (the backend routes are unchanged). Add to `next.config.ts` a `rewrites()` returning `/api/:path*` to `${process.env.API_URL}/:path*`, where `API_URL` is a server-side env var.

- [ ] **Step 3: Verify locally.** `cd frontend && npm run lint && npm run typecheck && npm run build`. Expected: all pass.

- [ ] **Step 4: Register the Firebase web app and read its config.** With a gcloud access token, `POST https://firebase.googleapis.com/v1beta1/projects/khamsebot/webApps` (name `folio-web`), then `GET .../webApps/<id>/config`. Keep the values out of git.

- [ ] **Step 5: Create the Vercel project.** From `frontend/`: `vercel link` (new project `folio`), set production env vars with `vercel env add`: `API_URL` (the Cloud Run URL), and the `NEXT_PUBLIC_FIREBASE_*` values. Then `vercel deploy --prod`. Expected: a `*.vercel.app` URL that loads the public page.

- [ ] **Step 6: Domain.** `vercel domains add folio.khamseaffan.com folio` and tell the person the DNS record it prints (a CNAME for `folio`). Wait until `vercel domains inspect` shows it valid. Then set `FRONTEND_ORIGIN=https://folio.khamseaffan.com` on the API (already the Terraform default) and add `folio.khamseaffan.com` and the `*.vercel.app` host to Firebase authorized domains using `PATCH https://identitytoolkit.googleapis.com/admin/v2/projects/khamsebot/config?updateMask=authorizedDomains` (fall back to the console if refused).

- [ ] **Step 7: Live check, end to end.** On `https://folio.khamseaffan.com`: sign in with Google as `khamseaffan@gmail.com`, set a display name, send one message, and watch the reply stream in pieces. Also confirm `khamseaffan+x@gmail.com` is refused with the refusal sentence. If the reply arrives only at the end, stop and report it, because the rewrite is buffering.

- [ ] **Step 8: Commit and open a pull request** `Send the website's API calls through one origin.` Merge on green.

### Task 5: Pipelines

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: outputs `workload_identity_provider`, `deploy_service_account`, `image_repo`; Vercel token, org id and project id.
- Produces: repo variables `GCP_PROJECT`, `GCP_REGION`, `WIF_PROVIDER`, `DEPLOY_SA`, `IMAGE_REPO`; repo secrets `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`.

- [ ] **Step 1: Ask the person for a Vercel token** (vercel.com/account/tokens, name `folio-ci`). Set it and the ids with `gh secret set`, and the variables with `gh variable set`. Do not echo values.

- [ ] **Step 2: Extend `ci.yml`.** Add a `terraform` job running `terraform fmt -check -recursive` and `terraform init -backend=false && terraform validate` in `infra/`, and a `docker` job running `docker build backend`.

- [ ] **Step 3: Write `deploy.yml`.** Triggers on `push` to `main` and requires the CI jobs (use `workflow_run` on CI success, or repeat the backend and frontend jobs as `needs`). Permissions `id-token: write, contents: read`. Steps: authenticate with `google-github-actions/auth` using `WIF_PROVIDER` and `DEPLOY_SA`; build and push `IMAGE_REPO/api:${{ github.sha }}`; update and execute the `folio-migrate` job with `--wait`; `gcloud run deploy folio-api --image ...`; `vercel deploy --prod` with the token; then a smoke step that calls `https://folio.khamseaffan.com/api/ready` up to 5 times with 10 second waits and fails unless it returns `{"status":"ok"}`. Pin every action by major version.

- [ ] **Step 4: Prove it.** Merge a harmless change (a README line) to `main` through a pull request. Expected: CI green, then `deploy.yml` green, and `gh run list` shows both. Confirm the live site serves the new revision: `gcloud run revisions list --service folio-api --region us-central1` shows the commit sha tag.

- [ ] **Step 5: Branch protection.** `gh api -X PUT repos/khamseaffan/Folio/branches/main/protection` requiring the `backend`, `frontend`, `terraform` and `docker` checks, with admins allowed to bypass. Expected: a pull request with a failing check cannot merge.

- [ ] **Step 6: Commit and merge** `Deploy the API and the website on every merge to main.`

### Task 6: Documents

**Files:** Modify `README.md`, `docs/architecture.md`, `docs/hand-in/report.md`, `docs/decisions.md`.

- [ ] **Step 1: Update status.** README Status table: deploy is present. Add the live URL, how a deploy happens, how to apply Terraform (`infra/README.md`), and the new env vars. `docs/architecture.md` and `docs/decisions.md`: stage 2.5 is done, and note the same-origin cookie and Firebase-by-service-identity decisions.

- [ ] **Step 2: Report.** In `docs/hand-in/report.md`: put the live URL, state that Terraform applies are run by a person after a plan review (a deliberate choice), and replace "Not switched on yet" claims that are now untrue for deployment. Keep the cold-start and single-zone trade-offs added earlier.

- [ ] **Step 3: Check and commit.** `grep -c "—"` over the changed docs returns `0`. Commit `Record the live deployment in the docs.`

---

## Self-review

- Spec coverage: shape (Task 1, 3, 4), same-origin cookie (Task 2 header, Task 4 rewrite), Terraform contents (Task 1), migrations (Task 2), Firebase identity (Task 2, 3), Vercel and domain (Task 4), Actions and protection (Task 5), report and docs (Task 6). The apply workflow in the design became local apply by decision, listed under Global Constraints.
- Names used across tasks: `folio-api`, `folio-migrate`, `image_repo`, `api_url`, `FIREBASE_USE_ADC`, `app.migrate.run` are consistent.
- Proportion: tasks carry decisions and checks, not code bodies.
