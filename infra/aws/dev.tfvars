environment     = "dev"
account_id      = "111111111111"   # replaced in CI from the secret AWS_ACCOUNT_ID
allowed_cidrs   = ["192.0.2.0/24"] # the office or VPN egress addresses
certificate_arn = "arn:aws:acm:il-central-1:111111111111:certificate/00000000-0000-0000-0000-000000000000"
alarm_email     = "ops@example.invalid"

rds_instance_class    = "db.t4g.small"
rds_multi_az          = false
backup_retention_days = 7
nat_per_az            = false
api_desired_count     = 1
worker_desired_count  = 1
