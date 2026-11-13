# Infrastructure

Terraform for the Folio Google Cloud project `khamsebot`. State lives in `gs://khamsebot-folio-tfstate`.

First time: `bash bootstrap.sh`, then `gcloud auth application-default login`.

```bash
terraform init
terraform plan -out=plan.tfplan
terraform apply plan.tfplan
```

Read the plan before applying. CI only runs `terraform fmt -check` and `terraform validate`. Deploys of the API image happen in `.github/workflows/deploy.yml`, not here.

The OpenAI key is added once by hand, so it never enters Terraform state:

```bash
grep '^OPENAI_API_KEY=' ../.env | cut -d= -f2- | gcloud secrets versions add openai-api-key --data-file=-
```

The state holds the generated database password. Keep the bucket private.
