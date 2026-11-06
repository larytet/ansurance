# Detection and audit of the AWS account itself. Turn off with enable_account_security = false
# if the account already runs these (they are one-per-account services).

resource "aws_s3_account_public_access_block" "main" {
  count                   = var.enable_account_security ? 1 : 0
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudtrail" "main" {
  count                         = var.enable_account_security ? 1 : 0
  name                          = local.name
  s3_bucket_name                = aws_s3_bucket.b["trail"].id
  kms_key_id                    = aws_kms_key.main.arn
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true
  depends_on                    = [aws_s3_bucket_policy.b]
}

resource "aws_guardduty_detector" "main" {
  count  = var.enable_account_security ? 1 : 0
  enable = true
}

resource "aws_inspector2_enabler" "ecr" {
  count          = var.enable_account_security ? 1 : 0
  account_ids    = [var.account_id]
  resource_types = ["ECR"]
}

resource "aws_accessanalyzer_analyzer" "main" {
  count         = var.enable_account_security ? 1 : 0
  analyzer_name = local.name
  type          = "ACCOUNT"
}

# AWS Config: record configuration and flag drift from the baseline.
resource "aws_iam_role" "config" {
  count = var.enable_account_security ? 1 : 0
  name  = "${local.name}-config"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "config.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "config" {
  count      = var.enable_account_security ? 1 : 0
  role       = aws_iam_role.config[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWS_ConfigRole"
}

resource "aws_config_configuration_recorder" "main" {
  count    = var.enable_account_security ? 1 : 0
  name     = local.name
  role_arn = aws_iam_role.config[0].arn
  recording_group {
    all_supported                 = true
    include_global_resource_types = true
  }
}

resource "aws_config_delivery_channel" "main" {
  count          = var.enable_account_security ? 1 : 0
  name           = local.name
  s3_bucket_name = aws_s3_bucket.b["config"].id
  depends_on     = [aws_config_configuration_recorder.main, aws_s3_bucket_policy.b]
}

resource "aws_config_configuration_recorder_status" "main" {
  count      = var.enable_account_security ? 1 : 0
  name       = aws_config_configuration_recorder.main[0].name
  is_enabled = true
  depends_on = [aws_config_delivery_channel.main]
}

resource "aws_config_config_rule" "baseline" {
  for_each = var.enable_account_security ? toset([
    "S3_BUCKET_PUBLIC_READ_PROHIBITED",
    "S3_BUCKET_PUBLIC_WRITE_PROHIBITED",
    "S3_BUCKET_SSL_REQUESTS_ONLY",
    "S3_BUCKET_VERSIONING_ENABLED",
    "RDS_STORAGE_ENCRYPTED",
    "RDS_INSTANCE_PUBLIC_ACCESS_CHECK",
    "CLOUD_TRAIL_ENABLED",
    "ENCRYPTED_VOLUMES",
    "RESTRICTED_INCOMING_TRAFFIC",
  ]) : toset([])
  name = "${local.name}-${lower(replace(each.key, "_", "-"))}"
  source {
    owner             = "AWS"
    source_identifier = each.key
  }
  depends_on = [aws_config_configuration_recorder_status.main]
}
