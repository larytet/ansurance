environment     = "prod"
account_id      = "222222222222"   # replaced in CI from the secret AWS_ACCOUNT_ID
allowed_cidrs   = ["192.0.2.0/24"] # the office or VPN egress addresses
certificate_arn = "arn:aws:acm:il-central-1:222222222222:certificate/00000000-0000-0000-0000-000000000000"
alarm_email     = "ops@example.invalid"

rds_instance_class    = "db.t4g.medium"
rds_multi_az          = true
backup_retention_days = 14
deletion_protection   = true
nat_per_az            = true
api_desired_count     = 2
worker_desired_count  = 2
log_retention_days    = 365
