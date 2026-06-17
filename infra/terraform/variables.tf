variable "do_token" {
  description = "DigitalOcean personal access token (database + vpc scopes)"
  type        = string
  sensitive   = true
}

variable "allowed_external_ips" {
  description = "IP addresses permitted to reach the database cluster (VM egress IP + any dev workstations)"
  type        = list(string)
  validation {
    condition     = length(var.allowed_external_ips) > 0
    error_message = "At least one IP must be in allowed_external_ips or the cluster will be unreachable."
  }
}

variable "region" {
  description = "DigitalOcean region for all resources"
  type        = string
  default     = "sfo3"
}

variable "cluster_size" {
  description = "Database cluster node size slug"
  type        = string
  default     = "db-s-1vcpu-1gb"
}
