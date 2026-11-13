locals {
  services = [
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "compute.googleapis.com",
    "servicenetworking.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "folio" {
  repository_id = "folio"
  location      = var.region
  format        = "DOCKER"
  description   = "Folio container images"

  depends_on = [google_project_service.enabled]
}
