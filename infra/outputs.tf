output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "api_service" {
  value = google_cloud_run_v2_service.api.name
}

output "migrate_job" {
  value = google_cloud_run_v2_job.migrate.name
}

output "image_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.folio.repository_id}"
}

output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "deploy_service_account" {
  value = google_service_account.deploy.email
}

output "worker_url" {
  value = local.worker_url
}

output "worker_service" {
  value = google_cloud_run_v2_service.worker.name
}

output "run_queue" {
  value = google_cloud_tasks_queue.runs.name
}

output "research_url" {
  value = local.research_url
}

output "research_service" {
  value = google_cloud_run_v2_service.research.name
}

output "research_queue" {
  value = google_cloud_tasks_queue.research.name
}

output "files_bucket" {
  value = google_storage_bucket.files.name
}

output "integrations_key" {
  value = google_kms_crypto_key.integrations.id
}
