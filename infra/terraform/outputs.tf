# DSNs are constructed by scripts/write-db-secrets.sh using these components,
# which allows proper URL-encoding of generated passwords.

output "db_host" {
  description = "Database cluster public hostname"
  value       = digitalocean_database_cluster.power_map.host
}

output "db_port" {
  description = "Database cluster port"
  value       = digitalocean_database_cluster.power_map.port
}

output "production_user_name" {
  description = "Username for the production app role"
  value       = digitalocean_database_user.production_user.name
}

output "production_user_password" {
  description = "Password for co_pm_db_production_user"
  value       = digitalocean_database_user.production_user.password
  sensitive   = true
}

output "production_migrations_name" {
  description = "Username for the production migrations role"
  value       = digitalocean_database_user.production_migrations.name
}

output "production_migrations_password" {
  description = "Password for co_pm_db_production_migrations"
  value       = digitalocean_database_user.production_migrations.password
  sensitive   = true
}

output "test_user_name" {
  description = "Username for the test role"
  value       = digitalocean_database_user.test_user.name
}

output "test_user_password" {
  description = "Password for co_pm_db_test_user"
  value       = digitalocean_database_user.test_user.password
  sensitive   = true
}
