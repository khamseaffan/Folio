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

The state holds the generated database password. Keep the bucket private.
