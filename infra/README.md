# Infrastructure

Terraform for the Folio Google Cloud project `khamsebot`. State lives in `gs://khamsebot-folio-tfstate`.

First time: `bash bootstrap.sh`, then `gcloud auth application-default login`.

```bash
terraform init
terraform plan -out=plan.tfplan
terraform apply plan.tfplan
```

Read the plan before applying. CI only runs `terraform fmt -check` and `terraform validate`. Deploys of the API image happen in `.github/workflows/deploy.yml`, not here.

## Schedules

Three things run schedules. All of them are in `worker.tf`.

- `folio-worker` is a second Cloud Run service. It uses the same image as the API but starts `app.worker:app`. It is not public. Only the `folio-invoker` account may call it. A call can last up to 30 minutes.
- `folio-runs` is a Cloud Tasks queue. The worker puts one task on it for each run. It tries a task up to 4 times and runs at most 3 at once.
- `folio-tick` is a Cloud Scheduler job. It calls the worker's `/tick` route every minute.

`folio-invoker` is the account that Cloud Tasks and Cloud Scheduler call the worker as. It can do one thing: call the worker. The worker's own account, `folio-worker`, has the same secrets as the API, may put tasks on the queue, and may act as the invoker.

The worker needs to know its own address, but a service cannot read its own address in its own settings. So the address is built from the project number, in the form `https://folio-worker-<project number>.<region>.run.app`. Cloud Run serves a service at that address.

Order for a person:

1. Apply Terraform as above. This makes the worker with the placeholder image.
2. Merge to `main`, or run the Deploy workflow again. It puts the real image on `folio-worker`. Before step 1 the workflow skips the worker and says so.

The OpenAI key is added once by hand, so it never enters Terraform state:

```bash
grep '^OPENAI_API_KEY=' ../.env | cut -d= -f2- | gcloud secrets versions add openai-api-key --data-file=-
```

## Research

Research runs use a second service and a second queue. All of them are in `research.tf`.

- `folio-research` is a Cloud Run service. It uses its own image, built from `backend/Dockerfile.research`, which is the API image plus Chrome. It starts `app.research_worker:app`. It is not public. Only `folio-invoker` may call it. A call can last up to 30 minutes. It takes one call at a time per instance, has 4 GiB of memory and 2 CPUs, and runs 0 to 3 instances. The memory and CPU are a first guess. Measure a real run and change them.
- `folio-research` is also a Cloud Tasks queue. It tries a task up to 4 times and runs at most 3 at once, so Chrome's memory stays bounded.
- `folio-research` is also the service account of that service. It can read only the database URL, the OpenAI key, and the Tavily key.

The API and the worker know the queue, the service address, and the invoker account, so they can put a research run on the queue. Their accounts may put tasks on the research queue, and the API's account may act as `folio-invoker`. The address of the research service is built from the project number, as the worker's is.

Order for a person:

1. Make the secret first: `terraform apply -target=google_secret_manager_secret.tavily_api_key`. A secret has to exist before it can get a value.
2. Add the Tavily key, with the command below. Do this before the full apply, because Cloud Run will not start the service while the secret has no value.
3. Apply Terraform as above. This makes the research service with the placeholder image.
4. Merge to `main`, or run the Deploy workflow again. It puts the real image on `folio-research`. Before step 3 the workflow skips that service and says so.

The Tavily key is added once by hand, in the same way as the OpenAI key, so it never enters Terraform state:

```bash
grep '^TAVILY_API_KEY=' ../.env | cut -d= -f2- | gcloud secrets versions add tavily-api-key --data-file=-
```

The state holds the generated database password. Keep the bucket private.
