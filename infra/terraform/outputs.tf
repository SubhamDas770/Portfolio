output "vercel_project_id" {
  description = "Vercel project ID for linking the local CLI."
  value       = vercel_project.portfolio.id
}

output "neon_project_id" {
  description = "Neon project ID."
  value       = neon_project.portfolio.id
}

output "neon_region_id" {
  description = "Region selected for the Neon Postgres project."
  value       = neon_project.portfolio.region_id
}
