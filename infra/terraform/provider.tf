terraform {
  required_version = ">= 1.9"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.0"
    }
  }

  # State stored in DO Spaces (S3-compatible).
  # Credentials supplied via -backend-config=backend.hcl (gitignored).
  # See docs/COMMANDS.md § Provisioning for init procedure.
  backend "s3" {
    endpoints = {
      s3 = "https://sfo3.digitaloceanspaces.com"
    }
    key    = "terraform.tfstate"
    bucket = "co-pm-spaces-1"
    region = "us-east-1" # required by S3 backend; unused by DO Spaces

    skip_credentials_validation  = true
    skip_metadata_api_check      = true
    skip_region_validation       = true
    skip_requesting_account_id   = true
  }
}

provider "digitalocean" {
  token = var.do_token
}
