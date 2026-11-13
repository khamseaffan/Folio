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
