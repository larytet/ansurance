variable "environment" {
  type        = string
  description = "dev or prod"
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "region" {
  type        = string
  default     = "il-central-1"
  description = "Tel Aviv by default (data residency). Confirm service availability before prod (decision D4)."
}

variable "name_prefix" {
  type    = string
  default = "ansurance"
}

variable "account_id" {
  type        = string
  description = "AWS account id (used in bucket names and key policies; no API lookup so plans work offline)."
}

variable "offline" {
  type        = bool
  default     = false
  description = "Plan without AWS credentials (tests)."
}

variable "azs" {
  type    = list(string)
  default = ["il-central-1a", "il-central-1b"]
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "allowed_cidrs" {
  type        = list(string)
  description = "Who may reach the load balancer: the agency's office addresses or VPN egress. Never 0.0.0.0/0 for prod."
  validation {
    condition     = length(var.allowed_cidrs) > 0 && !contains(var.allowed_cidrs, "0.0.0.0/0")
    error_message = "List the allowed CIDRs explicitly; 0.0.0.0/0 is not accepted."
  }
}

variable "certificate_arn" {
  type        = string
  description = "ACM certificate for the HTTPS listener."
}

variable "alarm_email" {
  type        = string
  description = "Where alarms and budget alerts go."
}

variable "image_tag" {
  type        = string
  description = "Immutable tag of the api and web images in ECR: the git SHA that CI pushed. No default on purpose: a deploy must name exactly what it deploys."
  validation {
    condition     = can(regex("^[0-9a-f]{7,40}$", var.image_tag))
    error_message = "image_tag must be a git commit SHA, never a moving tag such as latest."
  }
}

variable "clamav_image" {
  type    = string
  default = "clamav/clamav:stable@sha256:6d87c712e3391e13c1ba6e05dfb074d4b1ddb5a6834b23bb9da990d2a4ef042a"
}

# --- sizing -----------------------------------------------------------------------------
variable "nat_per_az" {
  type    = bool
  default = false
}

variable "rds_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "rds_allocated_storage" {
  type    = number
  default = 50
}

variable "rds_multi_az" {
  type    = bool
  default = false
}

variable "backup_retention_days" {
  type    = number
  default = 14
}

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "worker_desired_count" {
  type    = number
  default = 1
}

variable "api_cpu" {
  type    = number
  default = 512
}

variable "api_memory" {
  type    = number
  default = 1024
}

variable "worker_cpu" {
  type    = number
  default = 1024
}

variable "worker_memory" {
  type    = number
  default = 2048
}

variable "log_retention_days" {
  type    = number
  default = 90
}

variable "originals_lock_days" {
  type        = number
  default     = 2555
  description = "Default Object Lock retention for originals (about 7 years, to be confirmed with counsel)."
}

# --- security and operations ------------------------------------------------------------
variable "enable_waf" {
  type    = bool
  default = false
}

variable "enable_account_security" {
  type        = bool
  default     = true
  description = "CloudTrail, GuardDuty, Inspector, Config. Turn off if the account already has them."
}

variable "budget_usd" {
  type        = number
  default     = 0
  description = "Monthly budget alert threshold. 0 means no budget (decision D9: no fixed ceiling)."
}
