#!/usr/bin/env bash
# Creates the Terraform state bucket. Safe to run again.
set -euo pipefail

PROJECT="${PROJECT:-khamsebot}"
REGION="${REGION:-us-central1}"
BUCKET="${PROJECT}-folio-tfstate"

gcloud services enable serviceusage.googleapis.com cloudresourcemanager.googleapis.com \
  --project "$PROJECT"

if gcloud storage buckets describe "gs://${BUCKET}" --project "$PROJECT" >/dev/null 2>&1; then
  echo "bucket gs://${BUCKET} already exists"
else
  gcloud storage buckets create "gs://${BUCKET}" --project "$PROJECT" \
    --location "$REGION" --uniform-bucket-level-access
fi
gcloud storage buckets update "gs://${BUCKET}" --versioning
echo "state bucket ready: gs://${BUCKET}"
